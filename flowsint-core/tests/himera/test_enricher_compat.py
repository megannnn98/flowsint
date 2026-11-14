"""Imported Himera Email/Username nodes must be valid inputs of existing enrichers."""

import asyncio
from unittest.mock import MagicMock

import pytest

from flowsint_core.core.graph import GraphSerializer
from flowsint_core.himera.config import HimeraSettings
from flowsint_core.himera.extraction import HimeraExtractionService
from flowsint_core.himera.importer import entity_to_type
from flowsint_core.himera.schemas import EntityType
from flowsint_enrichers.registry import ENRICHER_REGISTRY, load_all_enrichers
from flowsint_types import Email, Username

from .fakes import (
    FakeOpenRouter,
    perfect_entities_handler,
    perfect_records_handler,
    synthetic_pdf,
)

ENRICHERS = {
    "email_to_username": Email,
    "email_to_gravatar": Email,
    "email_to_domain": Email,
    "username_to_socials_maigret": Username,
    "username_to_socials_sherlock": Username,
}


@pytest.fixture(scope="module")
def stored_nodes():
    """Himera entities as they come back from Neo4j (serialize → deserialize)."""
    fake = FakeOpenRouter(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": perfect_entities_handler(),
        }
    )
    result = asyncio.run(
        HimeraExtractionService(fake, HimeraSettings(pages_per_chunk=0)).extract(
            synthetic_pdf()
        )
    )
    nodes = {}
    for entity in result.entities:
        if entity.type not in (EntityType.EMAIL, EntityType.USERNAME):
            continue
        flat = GraphSerializer.flowsint_type_to_neo4j_dict(entity_to_type(entity, "r"))
        node = GraphSerializer.neo4j_dict_to_graph_node({"id": "x", "data": flat})
        nodes.setdefault(node.nodeType, []).append(node.nodeProperties)
    return nodes


@pytest.mark.parametrize("name,input_type", ENRICHERS.items())
def test_enricher_accepts_imported_nodes(name, input_type, stored_nodes):
    load_all_enrichers()
    enricher = ENRICHER_REGISTRY.get_enricher(
        name, sketch_id="s", scan_id="t", graph_service=MagicMock()
    )
    assert enricher.InputType is input_type
    inputs = stored_nodes[input_type.__name__.lower()]
    assert inputs, f"no {input_type.__name__} nodes imported"
    validated = enricher.preprocess([n.model_dump() for n in inputs])
    assert len(validated) == len(inputs)
    assert all(isinstance(v, input_type) for v in validated)


def test_email_to_username_runs_on_imported_email(stored_nodes):
    load_all_enrichers()
    enricher = ENRICHER_REGISTRY.get_enricher(
        "email_to_username", sketch_id="s", scan_id="t", graph_service=MagicMock()
    )
    data = enricher.preprocess([n.model_dump() for n in stored_nodes["email"]])
    out = asyncio.run(enricher.scan(data))
    assert "petrov.ps" in {u.value for u in out}
