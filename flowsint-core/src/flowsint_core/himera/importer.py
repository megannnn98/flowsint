"""
Write a ``HimeraExtractionResult`` into a sketch graph.

Every node gets provenance properties (report, record ids, pages,
verification) and every relationship carries the record id, pages, evidence,
confidence and status. Nodes are MERGEd on (type, nodeLabel, sketch) and edges
on their full property set, so importing the same report twice creates no
duplicates.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pydantic import ValidationError

from flowsint_core.core.graph import GraphSerializer, GraphService
from flowsint_types import (
    Document,
    Email,
    Event,
    FlowsintType,
    Identifier,
    Individual,
    Location,
    Organization,
    Phone,
    SocialAccount,
    SourceRecord,
    Username,
)

from .schemas import (
    EntityType,
    ExtractedEntity,
    ExtractedRelationship,
    HimeraExtractionResult,
    RelationshipStatus,
    SubjectLink,
    Verification,
)

SOURCE = "himera"
MENTIONS = "MENTIONS"


@dataclass
class ImportSelection:
    """What the user confirmed in the preview. ``None`` = defaults."""

    # Default: all validated entities.
    entity_ids: Optional[Set[str]] = None
    # Default: confirmed relationships only.
    relationship_ids: Optional[Set[str]] = None
    include_source_records: bool = True


@dataclass
class GraphImportResult:
    nodes_written: int = 0
    edges_written: int = 0
    skipped_relationships: int = 0
    errors: List[str] = field(default_factory=list)


def report_key(result: HimeraExtractionResult) -> str:
    return result.report_id or f"sha256:{result.pdf_sha256[:16]}"


def _split_list(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    items = [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
    return items or None


MISSING = "не указано"


def _build(cls, label: str, **data) -> Optional[FlowsintType]:
    """Validated instance, or None when the value does not fit the strict type.

    Never use ``model_construct`` here: the graph is read back through the
    type's validators (GraphSerializer.neo4j_dict_to_graph_node), and one
    invalid node would break loading of the whole sketch.
    """
    data = {k: v for k, v in data.items() if v not in (None, "", [])}
    try:
        obj = cls(**data)
    except (ValidationError, ValueError):
        return None
    obj.nodeLabel = label
    return obj


def entity_to_type(entity: ExtractedEntity, key: str) -> FlowsintType:
    a = entity.attributes
    value = entity.normalized_value
    unverified = entity.verification == Verification.UNVERIFIED
    label = value
    if unverified:
        label = f"{entity.raw_value} (не подтверждено)"

    t = entity.type
    if t == EntityType.INDIVIDUAL:
        birth = a.get("birth_date")
        base = f"{value} ({birth})" if birth else value
        if entity.subject_link == SubjectLink.SUBJECT:
            label = base
        elif birth:
            label = f"{base} [кандидат]"
        else:
            # Namesakes without a birth date must not MERGE with each other.
            label = f"{base} [кандидат, {key} {entity.record_ids[0]}]"
        obj = _build(
            Individual,
            label,
            full_name=value,
            birth_date=birth,
            aliases=_split_list(a.get("alt_surnames")),
        )
    elif t == EntityType.PHONE:
        obj = _build(Phone, label, number=value)
    elif t == EntityType.EMAIL:
        obj = _build(Email, label, email=value)
    elif t == EntityType.USERNAME:
        obj = _build(Username, label, value=value, platform=a.get("platform"))
    elif t == EntityType.SOCIAL_ACCOUNT:
        url = value if value.startswith("http") else a.get("profile_url")
        handle = (
            value.rstrip("/").rsplit("/", 1)[-1] if value.startswith("http") else value
        )
        obj = _build(
            SocialAccount,
            label,
            username=handle,
            platform=a.get("platform"),
            profile_url=url,
        )
    elif t == EntityType.ORGANIZATION:
        obj = _build(
            Organization, label, name=value, inn=a.get("inn"), ogrn=a.get("ogrn")
        )
    elif t == EntityType.ADDRESS:
        # Location requires city/country/zip: use the parts the model gave,
        # an explicit "не указано" otherwise (never a guessed value).
        obj = _build(
            Location,
            label,
            address=value,
            city=a.get("city") or MISSING,
            country=a.get("country") or MISSING,
            zip=a.get("zip") or MISSING,
        )
    elif t == EntityType.DOCUMENT:
        doc_type = a.get("doc_type")
        title = f"{doc_type} {value}" if doc_type else value
        obj = _build(
            Document,
            f"{title} (не подтверждено)" if unverified else title,
            title=title,
            doc_type=doc_type,
            source=SOURCE,
        )
    elif t == EntityType.IDENTIFIER:
        scheme = a.get("scheme")
        prefix = f"{scheme.upper()} " if scheme else ""
        obj = _build(
            Identifier,
            label if unverified else f"{prefix}{value}",
            value=value,
            scheme=scheme,
        )
    else:  # EVENT
        obj = _build(
            Event,
            label,
            title=value,
            date=a.get("date"),
            description=entity.raw_value,
            assertion="source_claim" if a.get("claim") else "fact",
            source=SOURCE,
        )

    if obj is None:
        # e.g. an unverified phone/e-mail the user chose to import: keep the value
        # as stated in a type without format rules, so it cannot reach enrichers.
        obj = Identifier(value=entity.raw_value, scheme=t.value.lower())
        obj.nodeLabel = label if unverified else f"{entity.raw_value} (не подтверждено)"

    provenance = {
        "source": SOURCE,
        "himera_report": key,
        "himera_records": entity.record_ids,
        "himera_pages": entity.pages,
        "himera_raw_value": entity.raw_value,
        "himera_verification": entity.verification.value,
        "himera_confidence": entity.confidence,
        "himera_subject_link": entity.subject_link.value,
    }
    if entity.role:
        provenance["himera_role"] = entity.role
    for name, val in provenance.items():
        setattr(obj, name, val)
    return obj


def record_to_type(
    result: HimeraExtractionResult, record_id: str, key: str
) -> SourceRecord:
    record = next(r for r in result.records if r.record_id == record_id)
    node = SourceRecord(
        record_key=f"{key}/{record.record_id}",
        report_id=key,
        record_id=record.record_id,
        source_name=record.source_name,
        source_heading=record.source_heading,
        source_period=record.source_period,
        section=record.section.value,
        page_start=record.page_start,
        page_end=record.page_end,
        fields=[f"{f.name}: {f.value}" for f in record.fields],
        name_match_only=record.name_match_only,
    )
    setattr(node, "source", SOURCE)
    # Identical headings on one page are distinct records: keep them apart in the
    # graph (nodes MERGE on nodeLabel), and apart from other reports.
    node.nodeLabel = f"{node.nodeLabel} #{record.record_id} [{key[:13]}]"
    return node


class HimeraGraphImporter:
    def __init__(self, graph_service: GraphService):
        self._graph = graph_service

    def import_result(
        self,
        result: HimeraExtractionResult,
        selection: Optional[ImportSelection] = None,
    ) -> GraphImportResult:
        selection = selection or ImportSelection()
        key = report_key(result)
        out = GraphImportResult()

        all_entities = {
            e.entity_id: e for e in result.entities + result.unverified_entities
        }
        wanted_ids = (
            selection.entity_ids
            if selection.entity_ids is not None
            else {e.entity_id for e in result.entities}
        )
        entities = [all_entities[i] for i in sorted(wanted_ids) if i in all_entities]
        all_rels = {
            r.rel_id: r for r in result.relationships + result.ambiguous_relationships
        }
        rel_ids = (
            selection.relationship_ids
            if selection.relationship_ids is not None
            else {r.rel_id for r in result.relationships}
        )
        relationships = [all_rels[i] for i in sorted(rel_ids) if i in all_rels]

        nodes: List[FlowsintType] = []
        index: Dict[str, int] = {}
        for entity in entities:
            index[entity.entity_id] = len(nodes)
            nodes.append(entity_to_type(entity, key))
        record_ids: List[str] = []
        if selection.include_source_records:
            record_ids = sorted({rid for e in entities for rid in e.record_ids})
            for rid in record_ids:
                index[f"record:{rid}"] = len(nodes)
                nodes.append(record_to_type(result, rid, key))
        if not nodes:
            return out

        created = self._graph.batch_create_nodes(
            GraphSerializer.serialize_flowsint_types(nodes)
        )
        node_ids: List[str] = created.get("node_ids", [])
        out.errors.extend(created.get("errors", []))
        if len(node_ids) != len(nodes):
            out.errors.append(
                f"node write returned {len(node_ids)} ids for {len(nodes)} nodes; edges skipped"
            )
            return out
        out.nodes_written = len(node_ids)

        edges = list(self._relationship_edges(relationships, index, node_ids, key, out))
        if selection.include_source_records:
            edges.extend(self._mention_edges(entities, index, node_ids, key))
        if edges:
            written = self._graph.batch_create_edges_by_element_id(edges)
            out.edges_written = written.get("edges_created", len(edges))
            out.errors.extend(written.get("errors", []))
        return out

    def _relationship_edges(
        self,
        relationships: Iterable[ExtractedRelationship],
        index: Dict[str, int],
        node_ids: List[str],
        key: str,
        out: GraphImportResult,
    ):
        for rel in relationships:
            src = index.get(rel.source_entity_id)
            dst = index.get(rel.target_entity_id)
            if src is None or dst is None:
                out.skipped_relationships += 1
                continue
            yield {
                "from_element_id": node_ids[src],
                "to_element_id": node_ids[dst],
                "rel_label": rel.type,
                "source": SOURCE,
                "himera_report": key,
                "himera_rel_id": rel.rel_id,
                "record_id": rel.record_id,
                "page_start": rel.page_start,
                "page_end": rel.page_end,
                "evidence": rel.evidence,
                "confidence": rel.confidence,
                "status": rel.status.value,
                "is_source_claim": rel.status == RelationshipStatus.SOURCE_CLAIM,
            }

    def _mention_edges(
        self,
        entities: Iterable[ExtractedEntity],
        index: Dict[str, int],
        node_ids: List[str],
        key: str,
    ):
        seen: Set[Tuple[str, str]] = set()
        for entity in entities:
            for m in entity.mentions:
                pair = (m.record_id, entity.entity_id)
                if pair in seen:
                    continue
                seen.add(pair)
                yield {
                    "from_element_id": node_ids[index[f"record:{m.record_id}"]],
                    "to_element_id": node_ids[index[entity.entity_id]],
                    "rel_label": MENTIONS,
                    "source": SOURCE,
                    "himera_report": key,
                    "record_id": m.record_id,
                    "page_start": m.page,
                    "page_end": m.page,
                    "evidence": m.evidence,
                    "confidence": m.confidence,
                    "status": entity.verification.value,
                }
