"""
Deterministic post-processing of LLM entities and relationships.

Checks references (record / entity ids), keeps every mention with its record
and page, merges duplicates and sorts relationships by status. It never adds
facts and never resolves contradictions by guessing.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import normalizers as N
from .schemas import (
    EntityType,
    ExtractedEntity,
    ExtractedRelationship,
    FieldValue,
    LlmEntity,
    LlmRelationship,
    Mention,
    RejectedItem,
    RelationshipStatus,
    SourceRecord,
    SubjectLink,
    Verification,
)

_PLACEHOLDERS = {
    "",
    "-",
    "—",
    "–",
    "нет",
    "не указано",
    "не указан",
    "null",
    "none",
    "n/a",
}


@dataclass
class Assembled:
    entities: List[ExtractedEntity] = field(default_factory=list)
    unverified_entities: List[ExtractedEntity] = field(default_factory=list)
    relationships: List[ExtractedRelationship] = field(default_factory=list)
    ambiguous_relationships: List[ExtractedRelationship] = field(default_factory=list)
    rejected: List[RejectedItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class _Checked:
    llm: LlmEntity
    normalized: str
    verification: Verification
    issues: List[str]
    subject_link: SubjectLink
    page: int
    attributes: List[FieldValue]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _collapse(value: str) -> str:
    return " ".join(value.split())


def is_placeholder(value: str) -> bool:
    stripped = _collapse(value).strip(" .,;:*").casefold()
    return stripped in _PLACEHOLDERS or set(stripped) <= {"*", "x", "х"}


_DATE_ATTRIBUTES = {"birth_date", "date", "issue_date"}


def _record_text(record: SourceRecord) -> str:
    fields = " ".join(f"{f.name} {f.value}" for f in record.fields)
    return f"{record.source_heading} {fields} {record.raw_text}"


def normalize_entity(
    entity: LlmEntity, record: SourceRecord
) -> Tuple[str, Verification, List[str], List[FieldValue]]:
    """Format checks, technical normalisation and grounding in the record text."""
    issues: List[str] = []
    valid = True
    attributes = [FieldValue(name=a.name, value=a.value) for a in entity.attributes]
    attr = {a.name: a.value for a in attributes}
    raw = entity.raw_value
    suggestion = entity.normalized_value

    t = entity.type
    if t == EntityType.EMAIL:
        checked = N.check_email(raw)
    elif t == EntityType.PHONE:
        checked = N.check_phone(raw)
    elif t == EntityType.IDENTIFIER:
        checked = N.check_identifier(raw, attr.get("scheme"))
    elif t == EntityType.USERNAME:
        checked = N.check_username(suggestion or raw)
    elif t == EntityType.SOCIAL_ACCOUNT:
        checked = N.check_social(raw)
        if checked.value.startswith("https://"):
            platform = N.platform_of(checked.value)
            if platform and "platform" not in attr:
                attributes.append(FieldValue(name="platform", value=platform))
            uid = N.vk_user_id(checked.value)
            if uid and "user_id" not in attr:
                attributes.append(FieldValue(name="user_id", value=uid))
    elif t == EntityType.INDIVIDUAL:
        checked = N.Checked(N.collapse(suggestion or raw).upper())
    else:
        checked = N.Checked(N.collapse(suggestion or raw))
    issues.extend(checked.issues)
    valid = checked.valid
    if (
        suggestion
        and t in (EntityType.EMAIL, EntityType.PHONE, EntityType.IDENTIFIER)
        and N.collapse(suggestion).casefold() != checked.value.casefold()
    ):
        issues.append(f"model normalisation '{suggestion}' differs from format check")

    if t == EntityType.ORGANIZATION:
        for key, scheme in (("inn", "inn"), ("ogrn", "ogrn")):
            if attr.get(key):
                sub = N.check_identifier(attr[key], scheme)
                if not sub.valid:
                    issues.append(f"{key}: {', '.join(sub.issues)}")

    for a in attributes:
        if a.name in _DATE_ATTRIBUTES and a.value.strip():
            d = N.check_date(a.value)
            if d.valid:
                a.value = d.value
            else:
                issues.extend(d.issues)

    text = _record_text(record)
    field_values = [f.value for f in record.fields]
    digit_types = (EntityType.PHONE, EntityType.IDENTIFIER, EntityType.DOCUMENT)
    grounded = N.text_contains(text, raw) or (
        t in digit_types
        and (
            N.digits_present(field_values, raw)
            or N.digits_present(field_values, checked.value)
        )
    )
    if not grounded:
        valid = False
        issues.append("value not found in the cited source record")
    elif entity.evidence and not N.words_present(text, entity.evidence):
        issues.append("evidence words are not all in the record")

    verification = Verification.VALIDATED if valid else Verification.UNVERIFIED
    return checked.value, verification, issues, attributes


def _check_entity(
    entity: LlmEntity, record: SourceRecord, rejected: List[RejectedItem]
) -> Optional[_Checked]:
    if is_placeholder(entity.raw_value):
        rejected.append(_reject_entity(entity, "empty or placeholder value"))
        return None
    normalized, verification, issues, attributes = normalize_entity(entity, record)
    page = entity.page
    if not record.page_start <= page <= record.page_end:
        issues.append(
            f"page {page} outside record pages {record.page_start}-{record.page_end}"
        )
        page = record.page_start
    subject_link = entity.subject_link
    if (
        entity.type == EntityType.INDIVIDUAL
        and record.name_match_only
        and subject_link == SubjectLink.SUBJECT
    ):
        subject_link = SubjectLink.UNKNOWN
        issues.append("name-only match cannot be the report subject")
    return _Checked(
        entity, normalized, verification, issues, subject_link, page, attributes
    )


def _reject_entity(entity: LlmEntity, reason: str) -> RejectedItem:
    return RejectedItem(
        kind="entity",
        reason=reason,
        data={
            "entity_id": entity.entity_id,
            "type": entity.type.value,
            "record_id": entity.record_id,
        },
    )


def _dedup_key(item: _Checked) -> Tuple[str, ...]:
    e = item.llm
    if item.verification == Verification.UNVERIFIED:
        # Unverified values never merge with anything.
        return ("unverified", e.type.value, e.raw_value, e.record_id)
    if e.type == EntityType.INDIVIDUAL:
        if item.subject_link == SubjectLink.SUBJECT:
            return ("Individual", "subject")
        birth = _attr(item.attributes, "birth_date")
        if birth:
            return ("Individual", item.normalized.casefold(), birth)
        # Without a birth date namesakes stay separate, one per record.
        return ("Individual", item.normalized.casefold(), "record", e.record_id)
    return (e.type.value, item.normalized.casefold())


def _attr(attributes: List[FieldValue], name: str) -> Optional[str]:
    for a in attributes:
        if a.name == name and a.value.strip():
            return a.value.strip()
    return None


def assemble(
    records: List[SourceRecord],
    llm_entities: List[LlmEntity],
    llm_relationships: List[LlmRelationship],
) -> Assembled:
    out = Assembled()
    by_record = {r.record_id: r for r in records}

    groups: Dict[Tuple[str, ...], List[_Checked]] = {}
    for entity in llm_entities:
        record = by_record.get(entity.record_id)
        if record is None:
            out.rejected.append(_reject_entity(entity, "unknown record_id"))
            continue
        checked = _check_entity(entity, record, out.rejected)
        if checked is not None:
            groups.setdefault(_dedup_key(checked), []).append(checked)

    id_map: Dict[str, ExtractedEntity] = {}
    for key, items in groups.items():
        merged = _merge_group(key, items)
        for item in items:
            id_map[item.llm.entity_id] = merged
        if merged.verification == Verification.VALIDATED:
            out.entities.append(merged)
        else:
            out.unverified_entities.append(merged)

    seen_rels: Dict[str, ExtractedRelationship] = {}
    for rel in llm_relationships:
        built = _build_relationship(rel, id_map, by_record, out.rejected)
        if built is None or built.rel_id in seen_rels:
            continue
        seen_rels[built.rel_id] = built
        if built.status == RelationshipStatus.CONFIRMED:
            out.relationships.append(built)
        else:
            out.ambiguous_relationships.append(built)
    return out


def _merge_group(key: Tuple[str, ...], items: List[_Checked]) -> ExtractedEntity:
    first = items[0]
    attributes: Dict[str, str] = {}
    issues: List[str] = []
    for item in items:
        issues.extend(i for i in item.issues if i not in issues)
        for a in item.attributes:
            value = a.value.strip()
            if not value:
                continue
            if a.name in attributes and attributes[a.name] != value:
                note = f"conflicting {a.name}: kept '{attributes[a.name]}'"
                if note not in issues:
                    issues.append(note)
                continue
            attributes.setdefault(a.name, value)
    roles = sorted({i.llm.role for i in items if i.llm.role})
    return ExtractedEntity(
        entity_id=_stable_id("ent", *key),
        type=first.llm.type,
        raw_value=first.llm.raw_value,
        normalized_value=first.normalized,
        confidence=max(i.llm.confidence for i in items),
        subject_link=first.subject_link,
        role=", ".join(roles) or None,
        attributes=attributes,
        mentions=[
            Mention(
                record_id=i.llm.record_id,
                page=i.page,
                raw_value=i.llm.raw_value,
                evidence=i.llm.evidence,
                confidence=i.llm.confidence,
            )
            for i in items
        ],
        verification=first.verification,
        issues=issues,
    )


def _build_relationship(
    rel: LlmRelationship,
    id_map: Dict[str, ExtractedEntity],
    by_record: Dict[str, SourceRecord],
    rejected: List[RejectedItem],
) -> Optional[ExtractedRelationship]:
    data = {
        "type": rel.type,
        "source_entity_id": rel.source_entity_id,
        "target_entity_id": rel.target_entity_id,
        "record_id": rel.record_id,
    }
    record = by_record.get(rel.record_id)
    source = id_map.get(rel.source_entity_id)
    target = id_map.get(rel.target_entity_id)
    if record is None:
        rejected.append(
            RejectedItem(kind="relationship", reason="unknown record_id", data=data)
        )
        return None
    if source is None or target is None:
        rejected.append(
            RejectedItem(
                kind="relationship", reason="unknown or rejected entity", data=data
            )
        )
        return None
    if source.entity_id == target.entity_id:
        rejected.append(
            RejectedItem(kind="relationship", reason="self-reference", data=data)
        )
        return None
    if rel.record_id not in source.record_ids or rel.record_id not in target.record_ids:
        rejected.append(
            RejectedItem(
                kind="relationship",
                reason="entities are not mentioned in the cited record",
                data=data,
            )
        )
        return None

    status = rel.status
    issues: List[str] = []
    if target.type == EntityType.EVENT or source.type == EntityType.EVENT:
        if status != RelationshipStatus.SOURCE_CLAIM:
            issues.append("links to an event/claim are kept as source claims")
        status = RelationshipStatus.SOURCE_CLAIM
    elif status == RelationshipStatus.CONFIRMED:
        reason = None
        if record.name_match_only:
            reason = "name-only match record"
        elif any(
            e.type == EntityType.INDIVIDUAL and e.subject_link == SubjectLink.UNKNOWN
            for e in (source, target)
        ):
            reason = "person identity unknown"
        elif Verification.UNVERIFIED in (source.verification, target.verification):
            reason = "unverified entity"
        if reason:
            status = RelationshipStatus.CANDIDATE
            issues.append(f"downgraded to candidate: {reason}")

    rel_type = re.sub(r"[^A-Z0-9_]+", "_", rel.type.strip().upper()).strip("_")
    rel_type = rel_type or "RELATED_TO"
    return ExtractedRelationship(
        rel_id=_stable_id(
            "rel", rel_type, source.entity_id, target.entity_id, record.record_id
        ),
        type=rel_type,
        source_entity_id=source.entity_id,
        target_entity_id=target.entity_id,
        record_id=record.record_id,
        page_start=record.page_start,
        page_end=record.page_end,
        evidence=rel.evidence,
        confidence=rel.confidence,
        status=status,
        issues=issues,
    )
