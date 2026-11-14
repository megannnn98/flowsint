from dataclasses import replace

import pytest

from flowsint_core.himera.config import HimeraSettings
from flowsint_core.himera.extraction import HimeraExtractionService
from flowsint_core.himera.openrouter import ChatResult, Usage
from flowsint_core.himera.schemas import EntityType, RelationshipStatus, SubjectLink

from .fakes import (
    FakeOpenRouter,
    batch_records_of,
    load_json,
    perfect_entities_handler,
    perfect_records_handler,
    synthetic_pdf,
)

SETTINGS = HimeraSettings(pages_per_chunk=0, max_cost_usd=1.0, records_per_batch=20)


async def run(entities_handler=None, settings=SETTINGS):
    fake = FakeOpenRouter(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": entities_handler or perfect_entities_handler(),
        }
    )
    result = await HimeraExtractionService(fake, settings).extract(synthetic_pdf())
    return result, fake


def find(result, type_, normalized, pool="all"):
    pools = {
        "all": result.entities + result.unverified_entities,
        "validated": result.entities,
    }[pool]
    matches = [e for e in pools if e.type == type_ and e.normalized_value == normalized]
    assert len(matches) == 1, (type_, normalized, [e.normalized_value for e in pools])
    return matches[0]


def rels_of(result, entity, pool):
    return [
        r
        for r in getattr(result, pool)
        if entity.entity_id in (r.source_entity_id, r.target_entity_id)
    ]


@pytest.mark.asyncio
async def test_subject_merged_across_records_with_provenance():
    result, _ = await run()
    subject = [
        e
        for e in result.entities
        if e.type == EntityType.INDIVIDUAL and e.subject_link == SubjectLink.SUBJECT
    ]
    assert len(subject) == 1
    assert subject[0].record_ids == [
        "p01-01",
        "p01-03",
        "p02-01",
        "p02-02",
        "p02-03",
        "p03-01",
    ]
    assert set(subject[0].pages) == {1, 2, 3}


@pytest.mark.asyncio
async def test_same_value_in_several_records_keeps_every_mention():
    result, _ = await run()
    phone = find(result, EntityType.PHONE, "+79990001122")
    assert [m.record_id for m in phone.mentions] == ["p01-01", "p02-01", "p02-02"]
    assert all(m.evidence for m in phone.mentions)


@pytest.mark.asyncio
async def test_several_phones_and_social_profiles():
    result, _ = await run()
    phones = {e.normalized_value for e in result.entities if e.type == EntityType.PHONE}
    assert phones >= {"+79990001122", "+74950001122", "+79165551234"}
    socials = {
        e.normalized_value
        for e in result.entities
        if e.type == EntityType.SOCIAL_ACCOUNT
    }
    assert socials == {"https://vk.com/id123456789", "petrov_ps"}
    vk = find(result, EntityType.SOCIAL_ACCOUNT, "https://vk.com/id123456789")
    assert vk.attributes["user_id"] == "123456789"
    assert len(rels_of(result, vk, "relationships")) == 1


@pytest.mark.asyncio
async def test_relationship_carries_record_and_pages():
    result, _ = await run()
    org = find(result, EntityType.ORGANIZATION, "ООО ВЕКТОР")
    worked = [r for r in result.relationships if r.type == "WORKED_AT"]
    assert len(worked) == 1
    assert worked[0].target_entity_id == org.entity_id
    assert (worked[0].record_id, worked[0].page_start, worked[0].page_end) == (
        "p02-03",
        2,
        3,
    )
    assert worked[0].evidence


@pytest.mark.asyncio
async def test_namesake_stays_a_separate_candidate():
    result, _ = await run()
    namesakes = [
        e
        for e in result.entities
        if e.type == EntityType.INDIVIDUAL and e.subject_link != SubjectLink.SUBJECT
    ]
    assert len(namesakes) == 1 and namesakes[0].record_ids == ["p04-01"]
    phone = find(result, EntityType.PHONE, "+79160000000")
    assert rels_of(result, phone, "relationships") == []
    amb = rels_of(result, phone, "ambiguous_relationships")
    assert [r.status for r in amb] == [RelationshipStatus.CANDIDATE]


@pytest.mark.asyncio
async def test_name_match_record_cannot_be_subject_even_if_model_says_so():
    gold = load_json("llm_entities_by_record.json")
    gold["p04-01"]["entities"][0]["subject_link"] = "subject"
    for r in gold["p04-01"]["relationships"]:
        r["status"] = "confirmed"
    result, _ = await run(perfect_entities_handler(gold))
    subject = [e for e in result.entities if e.subject_link == SubjectLink.SUBJECT]
    assert len(subject) == 1 and "p04-01" not in subject[0].record_ids
    phone = find(result, EntityType.PHONE, "+79160000000")
    assert rels_of(result, phone, "relationships") == []
    assert all(
        "name-only" in " ".join(r.issues)
        for r in rels_of(result, phone, "ambiguous_relationships")
    )


@pytest.mark.asyncio
async def test_labels_and_scores_are_source_claims():
    result, _ = await run()
    label = find(result, EntityType.EVENT, "МЕТКА: ДОЛЖНИК")
    assert rels_of(result, label, "relationships") == []
    assert [r.status for r in rels_of(result, label, "ambiguous_relationships")] == [
        RelationshipStatus.SOURCE_CLAIM
    ]


@pytest.mark.asyncio
async def test_batches_give_the_same_result():
    one, _ = await run()
    many, fake = await run(settings=replace(SETTINGS, records_per_batch=3))
    assert len(fake.calls_for("himera_entities")) == 4

    def key(res):
        return sorted(e.entity_id for e in res.entities + res.unverified_entities)

    assert key(one) == key(many)
    assert sorted(r.rel_id for r in one.relationships) == sorted(
        r.rel_id for r in many.relationships
    )


@pytest.mark.asyncio
async def test_fabricated_links_and_references_are_rejected():
    perfect = perfect_entities_handler()

    def handler(schema_name, messages):
        out = perfect(schema_name, messages)
        ids = {r["record_id"] for r in batch_records_of(messages)}
        if "p03-01" in ids and "p04-01" in ids:
            # link subject's VK to the namesake's phone: different records
            out["relationships"].append(
                {
                    "type": "HAS_PHONE",
                    "source_entity_id": "p03-01.i",
                    "target_entity_id": "p04-01.p",
                    "record_id": "p03-01",
                    "evidence": "x",
                    "confidence": 0.9,
                    "status": "confirmed",
                }
            )
            out["relationships"].append(
                {**out["relationships"][-1], "target_entity_id": "nope"}
            )
            out["entities"].append(
                {**out["entities"][0], "entity_id": "ghost", "record_id": "p99-01"}
            )
        return out

    result, _ = await run(handler)
    reasons = sorted(r.reason for r in result.rejected)
    assert "entities are not mentioned in the cited record" in reasons
    assert "unknown or rejected entity" in reasons
    assert "unknown record_id" in reasons
    namesake_phone = find(result, EntityType.PHONE, "+79160000000")
    assert rels_of(result, namesake_phone, "relationships") == []


@pytest.mark.asyncio
async def test_empty_values_are_not_entities():
    perfect = perfect_entities_handler()

    def handler(schema_name, messages):
        out = perfect(schema_name, messages)
        if out["entities"]:
            out["entities"].append(
                {**out["entities"][0], "entity_id": "empty", "raw_value": " - "}
            )
        return out

    result, _ = await run(handler)
    assert any(r.reason == "empty or placeholder value" for r in result.rejected)


@pytest.mark.asyncio
async def test_truncated_entities_batch_is_split():
    perfect = perfect_entities_handler()

    def handler(schema_name, messages):
        if len(batch_records_of(messages)) > 5:
            return ChatResult('{"entities": [', "length", Usage(1, 1, 0.0, 1), "m")
        return perfect(schema_name, messages)

    result, _ = await run(handler)
    assert result.complete
    assert find(result, EntityType.PHONE, "+79160000000")
    assert any("token limit" in w for w in result.warnings)
