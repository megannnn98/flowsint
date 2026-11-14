import asyncio

import pytest

from flowsint_core.himera.config import HimeraSettings
from flowsint_core.himera.extraction import HimeraExtractionService
from flowsint_core.himera.importer import (
    HimeraGraphImporter,
    ImportSelection,
    entity_to_type,
)
from flowsint_core.himera.schemas import EntityType

from .fakes import (
    FakeOpenRouter,
    perfect_entities_handler,
    perfect_records_handler,
    synthetic_pdf,
)


class MergingGraph:
    """Mimics Neo4jGraphRepository MERGE semantics used by GraphService."""

    def __init__(self):
        self.nodes = {}  # (nodeType, nodeLabel) -> props
        self.edges = {}  # frozen props -> edge dict

    def batch_create_nodes(self, nodes):
        ids = []
        for n in nodes:
            key = (n["nodeType"], n["nodeLabel"])
            self.nodes.setdefault(key, {}).update(n)
            ids.append(f"{key[0]}|{key[1]}")
        return {"nodes_created": len(ids), "node_ids": ids, "errors": []}

    def batch_create_edges_by_element_id(self, edges):
        for e in edges:
            frozen = tuple(sorted((k, str(v)) for k, v in e.items()))
            self.edges[frozen] = e
        return {"edges_created": len(edges), "errors": []}


async def _extract():
    fake = FakeOpenRouter(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": perfect_entities_handler(),
        }
    )
    settings = HimeraSettings(pages_per_chunk=0, max_cost_usd=1.0)
    return await HimeraExtractionService(fake, settings).extract(synthetic_pdf())


@pytest.fixture
def result():
    return asyncio.run(_extract())


def edges_of(graph, label):
    return [e for e in graph.edges.values() if e["rel_label"] == label]


def test_import_writes_nodes_and_edges_with_provenance(result):
    graph = MergingGraph()
    summary = HimeraGraphImporter(graph).import_result(result)
    assert summary.errors == []
    types = {t for t, _ in graph.nodes}
    assert {
        "individual",
        "phone",
        "email",
        "username",
        "socialaccount",
        "organization",
        "location",
        "document",
        "identifier",
        "sourcerecord",
    } <= types
    worked = edges_of(graph, "WORKED_AT")
    assert len(worked) == 1
    assert (worked[0]["record_id"], worked[0]["page_start"], worked[0]["page_end"]) == (
        "p02-03",
        2,
        3,
    )
    assert worked[0]["evidence"] and worked[0]["status"] == "confirmed"
    # every non-MENTIONS edge points back to a record and a page
    for e in graph.edges.values():
        assert e["record_id"] and e["page_start"] >= 1 and e["himera_report"]
    phone = graph.nodes[("phone", "+79990001122")]
    assert phone["nodeProperties.himera_records"] == ["p01-01", "p02-01", "p02-02"]
    assert phone["nodeProperties.himera_pages"] == [1, 2]
    assert phone["nodeProperties.source"] == "himera"


def test_default_import_excludes_unverified_and_ambiguous(result):
    graph = MergingGraph()
    HimeraGraphImporter(graph).import_result(result)
    labels = {label for _, label in graph.nodes}
    assert not any("не подтверждено" in label for label in labels)
    statuses = {
        e["status"] for e in graph.edges.values() if e["rel_label"] != "MENTIONS"
    }
    assert statuses == {"confirmed"}


def test_namesake_is_separate_node_and_not_linked_to_subject(result):
    graph = MergingGraph()
    everything = ImportSelection(
        entity_ids={e.entity_id for e in result.entities + result.unverified_entities},
        relationship_ids={
            r.rel_id for r in result.relationships + result.ambiguous_relationships
        },
    )
    HimeraGraphImporter(graph).import_result(result, everything)
    people = sorted(label for t, label in graph.nodes if t == "individual")
    assert people[0] == "ПЕТРОВ ПЁТР СЕРГЕЕВИЧ (1985-04-03)"
    assert len(people) == 2 and "кандидат" in people[1]
    phone_edges = [
        e
        for e in edges_of(graph, "HAS_PHONE")
        if e["to_element_id"] == "phone|+79160000000"
    ]
    assert [e["status"] for e in phone_edges] == ["candidate"]
    assert (
        phone_edges[0]["from_element_id"]
        != "individual|ПЕТРОВ ПЁТР СЕРГЕЕВИЧ (1985-04-03)"
    )
    claim = edges_of(graph, "SUBJECT_OF_CLAIM")
    assert claim and claim[0]["is_source_claim"] is True
    assert any("не подтверждено" in label for t, label in graph.nodes if t == "email")


def test_reimport_creates_no_duplicates(result):
    graph = MergingGraph()
    importer = HimeraGraphImporter(graph)
    importer.import_result(result)
    nodes, edges = len(graph.nodes), len(graph.edges)
    importer.import_result(result)
    assert (len(graph.nodes), len(graph.edges)) == (nodes, edges)


def test_nodes_are_enricher_compatible(result):
    by_type = {e.type: e for e in result.entities}
    email = entity_to_type(by_type[EntityType.EMAIL], "r")
    username = entity_to_type(by_type[EntityType.USERNAME], "r")
    assert email.__class__.__name__ == "Email" and "@" in email.email
    assert username.__class__.__name__ == "Username" and username.value
