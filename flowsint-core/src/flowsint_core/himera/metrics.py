"""
Quality metrics of an extraction against a hand-made gold file.

Gold format (see tests/himera/fixtures/expected_synthetic.json):
records [[heading, page_start, page_end]], entities [[type, normalized]],
subject, relationships_confirmed [[type, target normalized]] (from the subject),
must_not_link_subject_to [normalized].
"""

from collections import Counter
from typing import Any, Dict, Iterable, Tuple

from .schemas import EntityType, HimeraExtractionResult, SubjectLink

# Events are source claims with free-form titles: not scored.
_SCORED_TYPES = {t.value for t in EntityType} - {EntityType.EVENT.value}


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _pr(found: Counter, gold: Counter) -> Tuple[float, float]:
    hit = sum((found & gold).values())
    precision = hit / sum(found.values()) if found else 0.0
    recall = hit / sum(gold.values()) if gold else 1.0
    return round(precision, 3), round(recall, 3)


def evaluate(result: HimeraExtractionResult, gold: Dict[str, Any]) -> Dict[str, Any]:
    found_records = Counter(
        (_norm(r.source_heading), r.page_start, r.page_end)
        for r in result.records
        if r.section.value != "service"
    )
    gold_records = Counter((_norm(h), s, e) for h, s, e in gold["records"])
    rec_p, rec_r = _pr(found_records, gold_records)

    all_entities = result.entities + result.unverified_entities
    found_entities = Counter(
        {
            (e.type.value, _norm(e.normalized_value)): 1
            for e in all_entities
            if e.type.value in _SCORED_TYPES
        }
    )
    gold_entities = Counter({(t, _norm(v)): 1 for t, v in gold["entities"]})
    ent_p, ent_r = _pr(found_entities, gold_entities)

    by_id = {e.entity_id: e for e in all_entities}
    subjects = {
        e.entity_id
        for e in all_entities
        if e.type == EntityType.INDIVIDUAL and e.subject_link == SubjectLink.SUBJECT
    }
    subject_links = Counter(
        {
            (r.type, _norm(by_id[r.target_entity_id].normalized_value)): 1
            for r in result.relationships
            if r.source_entity_id in subjects and r.target_entity_id in by_id
        }
    )
    gold_links = Counter({(t, _norm(v)): 1 for t, v in gold["relationships_confirmed"]})
    rel_p, rel_r = _pr(subject_links, gold_links)
    forbidden = {_norm(v) for v in gold.get("must_not_link_subject_to", [])}
    wrong_subject_links = sorted(v for _, v in subject_links if v in forbidden)

    total = len(all_entities)
    return {
        "records_found": sum(found_records.values()),
        "records_expected": sum(gold_records.values()),
        "record_precision": rec_p,
        "record_recall": rec_r,
        "entity_precision": ent_p,
        "entity_recall": ent_r,
        "relationship_precision": rel_p,
        "relationship_recall": rel_r,
        "wrong_subject_links": wrong_subject_links,
        "subject_nodes": len(subjects),
        "unverified_share": round(len(result.unverified_entities) / total, 3)
        if total
        else 0.0,
        "ambiguous_relationship_share": _share(
            result.ambiguous_relationships, result.relationships
        ),
        "complete": result.complete,
        "cost_usd": result.usage.cost_usd,
        "calls": result.usage.calls,
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
    }


def _share(part: Iterable[Any], rest: Iterable[Any]) -> float:
    a, b = len(list(part)), len(list(rest))
    return round(a / (a + b), 3) if a + b else 0.0


def summary(result: HimeraExtractionResult) -> Dict[str, Any]:
    """Counts only (no values) — safe to print for real reports."""
    by_type = Counter(e.type.value for e in result.entities)
    unverified_by_type = Counter(e.type.value for e in result.unverified_entities)
    pages = set()
    for r in result.records:
        pages.update(range(r.page_start, r.page_end + 1))
    return {
        "page_count": result.page_count,
        "pages_covered": len(pages),
        "records": len(result.records),
        "multi_page_records": sum(
            1 for r in result.records if r.page_end > r.page_start
        ),
        "name_match_records": sum(1 for r in result.records if r.name_match_only),
        "entities": dict(by_type),
        "unverified": dict(unverified_by_type),
        "relationships": len(result.relationships),
        "ambiguous_relationships": len(result.ambiguous_relationships),
        "rejected": dict(Counter(r.reason for r in result.rejected)),
        "warnings": len(result.warnings),
        "complete": result.complete,
        "truncation_notice": result.truncation_notice,
        "usage": result.usage.model_dump(),
    }
