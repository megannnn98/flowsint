import pytest

from flowsint_core.himera import normalizers as N
from flowsint_core.himera.schemas import (
    EntityType,
    LlmEntity,
    LlmRelationship,
    RelationshipStatus,
    Section,
    SourceRecord,
    SubjectLink,
    Verification,
)
from flowsint_core.himera.validation import assemble

# ------------------------------------------------------------ normalizers


@pytest.mark.parametrize(
    "raw,expected,valid",
    [
        ("petrov.ps@example.com", "petrov.ps@example.com", True),
        ("PETROV85@MAIL.RU", "petrov85@mail.ru", True),
        ("PETROV85@МАIL.RU", "petrov85@mail.ru", False),  # Cyrillic М, А
        ("іvan@example.com", "ivan@example.com", False),  # Cyrillic і
        ("иван@почта.рф", "иван@почта.рф", False),
        ("not-an-email", "not-an-email", False),
    ],
)
def test_email(raw, expected, valid):
    checked = N.check_email(raw)
    assert (checked.value, checked.valid) == (expected, valid)


def test_email_lookalike_issue_text():
    assert "look-alike" in N.check_email("PETROV85@МАIL.RU").issues[0]


@pytest.mark.parametrize(
    "raw,expected,valid",
    [
        ("79990001122", "+79990001122", True),
        ("89990001122", "+79990001122", True),
        ("+7 (916) 555-12-34", "+79165551234", True),
        ("9165551234", "+79165551234", True),
        ("74950001122", "+74950001122", True),
        ("78123456789", "+78123456789", True),
        ("+44 20 7946 0958", "+442079460958", True),
        ("12", "12", False),
    ],
)
def test_phone(raw, expected, valid):
    checked = N.check_phone(raw)
    assert (checked.value, checked.valid) == (expected, valid)


@pytest.mark.parametrize(
    "raw,scheme,valid",
    [
        ("770123456703", "inn", True),
        ("770123456704", "inn", False),
        ("7709876545", "inn", True),
        ("112-233-445 95", "snils", True),
        ("11223344596", "snils", False),
        ("1027700123450", "ogrn", True),
        ("1027700123451", "ogrn", False),
        ("4111 1111 1111 1111", "bank_card", True),
        ("4111 1111 1111 1112", "bank_card", False),
        ("40817810000000000001", "bank_account", True),
        ("123", "other", True),
    ],
)
def test_identifier_checksums(raw, scheme, valid):
    assert N.check_identifier(raw, scheme).valid is valid


@pytest.mark.parametrize(
    "raw,expected,valid",
    [
        ("3.4.1985", "1985-04-03", True),
        ("03.04.1985", "1985-04-03", True),
        ("1985-04-03", "1985-04-03", True),
        ("2020-05-22 00:00:00,000", "2020-05-22", True),
        ("12.10.2012Г", "2012-10-12", True),
        ("2021", "2021", True),
        ("31.02.1990", "31.02.1990", False),
        ("вчера", "вчера", False),
    ],
)
def test_dates(raw, expected, valid):
    checked = N.check_date(raw)
    assert (checked.value, checked.valid) == (expected, valid)


def test_social_and_username():
    vk = N.check_social("vk.com/id123/")
    assert vk.value == "https://vk.com/id123" and vk.valid
    assert N.vk_user_id(vk.value) == "123"
    assert N.platform_of("https://t.me/x") == "telegram"
    assert N.check_social("@petrov_ps").value == "petrov_ps"
    assert not N.check_username("два слова").valid


# --------------------------------------------------------------- assemble


def record(record_id, fields, page=1, name_match=False):
    from flowsint_core.himera.schemas import FieldValue

    return SourceRecord(
        record_id=record_id,
        section=Section.DATASET_RECORD,
        source_heading="ТЕСТ 2020",
        page_start=page,
        page_end=page,
        ordinal_on_start_page=1,
        fields=[FieldValue(name=n, value=v) for n, v in fields],
        name_match_only=name_match,
    )


def entity(
    eid, type_, raw, record_id, norm=None, link="not_applicable", page=1, **attrs
):
    return LlmEntity(
        entity_id=eid,
        type=type_,
        raw_value=raw,
        normalized_value=norm,
        record_id=record_id,
        page=page,
        evidence=raw,
        confidence=0.9,
        subject_link=link,
        attributes=[{"name": k, "value": v} for k, v in attrs.items()],
    )


def rel(type_, s, t, record_id, status="confirmed"):
    return LlmRelationship(
        type=type_,
        source_entity_id=s,
        target_entity_id=t,
        record_id=record_id,
        evidence="x",
        confidence=0.9,
        status=status,
    )


def test_lookalike_email_is_unverified_and_not_merged_with_latin_twin():
    records = [
        record("r1", [("Имя", "ИВАНОВ ИВАН"), ("Email", "IVAN@МАIL.RU")]),
        record("r2", [("Имя", "ИВАНОВ ИВАН"), ("Email", "ivan@mail.ru")]),
    ]
    entities = [
        entity("i1", EntityType.INDIVIDUAL, "ИВАНОВ ИВАН", "r1", link="subject"),
        entity("m1", EntityType.EMAIL, "IVAN@МАIL.RU", "r1", norm="ivan@mail.ru"),
        entity("i2", EntityType.INDIVIDUAL, "ИВАНОВ ИВАН", "r2", link="subject"),
        entity("m2", EntityType.EMAIL, "ivan@mail.ru", "r2"),
    ]
    out = assemble(
        records,
        entities,
        [rel("HAS_EMAIL", "i1", "m1", "r1"), rel("HAS_EMAIL", "i2", "m2", "r2")],
    )
    assert [e.normalized_value for e in out.entities if e.type == EntityType.EMAIL] == [
        "ivan@mail.ru"
    ]
    assert len(out.unverified_entities) == 1
    lookalike = out.unverified_entities[0]
    assert lookalike.raw_value == "IVAN@МАIL.RU"
    assert lookalike.normalized_value == "ivan@mail.ru"
    assert lookalike.verification == Verification.UNVERIFIED
    assert [r.record_id for r in out.relationships] == ["r2"]
    assert out.ambiguous_relationships[0].status == RelationshipStatus.CANDIDATE
    assert "unverified" in out.ambiguous_relationships[0].issues[0]


def test_value_absent_from_record_is_unverified():
    records = [record("r1", [("Телефон", "79990001122")])]
    out = assemble(records, [entity("p", EntityType.PHONE, "79990009999", "r1")], [])
    assert out.entities == []
    assert "value not found" in out.unverified_entities[0].issues[-1]


def test_bad_checksum_is_unverified_but_kept():
    records = [record("r1", [("ИНН", "770123456704")])]
    out = assemble(
        records,
        [entity("n", EntityType.IDENTIFIER, "770123456704", "r1", scheme="inn")],
        [],
    )
    assert out.entities == []
    assert out.unverified_entities[0].normalized_value == "770123456704"


def test_dates_normalised_before_merge_no_false_conflict():
    records = [
        record("r1", [("Имя", "ИВАНОВ ИВАН"), ("ДР", "3.4.1985")]),
        record("r2", [("Имя", "Иванов Иван"), ("ДР", "03.04.1985")]),
    ]
    out = assemble(
        records,
        [
            entity(
                "a",
                EntityType.INDIVIDUAL,
                "ИВАНОВ ИВАН",
                "r1",
                link="subject",
                birth_date="3.4.1985",
            ),
            entity(
                "b",
                EntityType.INDIVIDUAL,
                "Иванов Иван",
                "r2",
                link="subject",
                birth_date="03.04.1985",
            ),
        ],
        [],
    )
    assert len(out.entities) == 1
    assert out.entities[0].attributes["birth_date"] == "1985-04-03"
    assert not any("conflicting" in i for i in out.entities[0].issues)


def test_namesakes_with_different_birth_dates_stay_apart():
    records = [
        record("r1", [("Имя", "ИВАНОВ ИВАН"), ("ДР", "1.1.1980")]),
        record("r2", [("Имя", "ИВАНОВ ИВАН"), ("ДР", "2.2.1990")]),
        record("r3", [("Имя", "ИВАНОВ ИВАН")]),
        record("r4", [("Имя", "ИВАНОВ ИВАН")]),
    ]
    out = assemble(
        records,
        [
            entity(
                "a",
                EntityType.INDIVIDUAL,
                "ИВАНОВ ИВАН",
                "r1",
                link="other_person",
                birth_date="1.1.1980",
            ),
            entity(
                "b",
                EntityType.INDIVIDUAL,
                "ИВАНОВ ИВАН",
                "r2",
                link="other_person",
                birth_date="2.2.1990",
            ),
            entity("c", EntityType.INDIVIDUAL, "ИВАНОВ ИВАН", "r3", link="unknown"),
            entity("d", EntityType.INDIVIDUAL, "ИВАНОВ ИВАН", "r4", link="unknown"),
        ],
        [],
    )
    assert len(out.entities) == 4


def test_unknown_identity_downgrades_link():
    records = [record("r1", [("Имя", "ИВАНОВ ИВАН"), ("Телефон", "79990001122")])]
    out = assemble(
        records,
        [
            entity("i", EntityType.INDIVIDUAL, "ИВАНОВ ИВАН", "r1", link="unknown"),
            entity("p", EntityType.PHONE, "79990001122", "r1"),
        ],
        [rel("HAS_PHONE", "i", "p", "r1")],
    )
    assert out.relationships == []
    assert out.ambiguous_relationships[0].status == RelationshipStatus.CANDIDATE


def test_duplicate_relationships_are_merged_and_type_sanitised():
    records = [record("r1", [("Имя", "ИВАНОВ ИВАН"), ("Телефон", "79990001122")])]
    ents = [
        entity("i", EntityType.INDIVIDUAL, "ИВАНОВ ИВАН", "r1", link="subject"),
        entity("p", EntityType.PHONE, "79990001122", "r1"),
        entity("p2", EntityType.PHONE, "79990001122", "r1"),
    ]
    out = assemble(
        records,
        ents,
        [
            rel("has phone", "i", "p", "r1"),
            rel("HAS_PHONE", "i", "p2", "r1"),
            rel("X`]->(n) DELETE n //", "i", "p", "r1"),
        ],
    )
    types = sorted(r.type for r in out.relationships)
    assert types == ["HAS_PHONE", "X_N_DELETE_N"]
    assert all(set(t) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") for t in types)


def test_subject_link_only_on_individuals_kept():
    records = [record("r1", [("Имя", "ИВАНОВ ИВАН")], name_match=True)]
    out = assemble(
        records,
        [entity("i", EntityType.INDIVIDUAL, "ИВАНОВ ИВАН", "r1", link="subject")],
        [],
    )
    assert out.entities[0].subject_link == SubjectLink.UNKNOWN


def test_numbers_and_null_in_attributes_are_accepted():
    from flowsint_core.himera.schemas import LlmEntitiesOutput

    out = LlmEntitiesOutput.model_validate(
        {
            "entities": [
                {
                    "entity_id": "e1",
                    "type": "Organization",
                    "raw_value": "ООО Х",
                    "record_id": "r1",
                    "page": 1,
                    "evidence": "ООО Х",
                    "confidence": 0.9,
                    "attributes": [
                        {"name": "inn", "value": 7709876545},
                        {"name": "x", "value": None},
                    ],
                }
            ],
            "relationships": [],
        }
    )
    assert [a.value for a in out.entities[0].attributes] == ["7709876545", ""]


def test_reformatted_phone_is_grounded_but_invented_one_is_not():
    records = [record("r1", [("Телефоны", "79161112233, 78123456789")])]
    out = assemble(
        records,
        [
            entity("a", EntityType.PHONE, "+7 (916) 111-22-33", "r1"),
            entity("b", EntityType.PHONE, "+7 (999) 000-00-00", "r1"),
        ],
        [],
    )
    assert [e.normalized_value for e in out.entities] == ["+79161112233"]
    assert out.unverified_entities[0].normalized_value == "+79990000000"
