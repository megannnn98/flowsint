"""Quality metrics on the synthetic report, mocked and (opt-in) live OpenRouter."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from flowsint_core.himera.config import HimeraSettings
from flowsint_core.himera.extraction import HimeraExtractionService
from flowsint_core.himera.importer import HimeraGraphImporter
from flowsint_core.himera.metrics import evaluate, summary
from flowsint_core.himera.openrouter import OpenRouterClient

from .fakes import (
    FakeOpenRouter,
    load_json,
    perfect_entities_handler,
    perfect_records_handler,
    synthetic_pdf,
)
from .test_importer import MergingGraph

GOLD = load_json("expected_synthetic.json")


def extract(handlers, settings=None):
    fake = FakeOpenRouter(handlers)
    settings = settings or HimeraSettings(pages_per_chunk=2, records_per_batch=4)
    return asyncio.run(HimeraExtractionService(fake, settings).extract(synthetic_pdf()))


def test_perfect_model_scores_full_marks():
    result = extract(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": perfect_entities_handler(),
        }
    )
    m = evaluate(result, GOLD)
    assert m["record_recall"] == m["record_precision"] == 1.0
    assert m["entity_recall"] == m["entity_precision"] == 1.0
    assert m["relationship_recall"] == m["relationship_precision"] == 1.0
    assert m["wrong_subject_links"] == [] and m["subject_nodes"] == 1
    assert 0 < m["unverified_share"] < 0.2
    assert m["cost_usd"] > 0


def test_metrics_detect_a_bad_model():
    whole = load_json("llm_records_whole.json")
    whole["records"] = [
        r for r in whole["records"] if r["page_start"] != 3
    ]  # lost page
    by_record = load_json("llm_entities_by_record.json")
    # model wrongly says the namesake is the subject and links their phone
    by_record["p04-01"]["entities"][0]["subject_link"] = "subject"
    result = extract(
        {
            "himera_records": perfect_records_handler(whole),
            "himera_entities": perfect_entities_handler(by_record),
        }
    )
    m = evaluate(result, GOLD)
    assert m["record_recall"] < 1.0
    assert m["entity_recall"] < 1.0
    assert m["relationship_recall"] < 1.0
    # the deterministic guard keeps the namesake away from the subject
    assert m["wrong_subject_links"] == []


def test_full_pipeline_pdf_to_graph():
    result = extract(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": perfect_entities_handler(),
        }
    )
    graph = MergingGraph()
    out = HimeraGraphImporter(graph).import_result(result)
    assert out.errors == [] and out.nodes_written == len(graph.nodes)
    s = summary(result)
    assert (
        s["records"] == 11
        and s["multi_page_records"] == 1
        and s["name_match_records"] == 1
    )
    for type_, value in [
        ("phone", "+79990001122"),
        ("phone", "+79165551234"),
        ("email", "petrov.ps@example.com"),
        ("username", "petrov_ps"),
        ("socialaccount", "https://vk.com/id123456789"),
    ]:
        assert (type_, value) in graph.nodes
    assert "ПЕТРОВ" not in json.dumps(s, ensure_ascii=False)  # summary has no values


LIVE = os.environ.get("HIMERA_LIVE_TEST") == "1" and os.environ.get(
    "OPENROUTER_API_KEY"
)


@pytest.mark.skipif(not LIVE, reason="set HIMERA_LIVE_TEST=1 and OPENROUTER_API_KEY")
def test_live_openrouter_on_synthetic_report():
    """Real OpenRouter call on the synthetic (fictional) report. Costs money."""
    settings = HimeraSettings.from_env()
    client = OpenRouterClient(
        os.environ["OPENROUTER_API_KEY"], timeout_s=settings.timeout_s
    )
    result = asyncio.run(
        HimeraExtractionService(client, settings).extract(synthetic_pdf())
    )
    m = evaluate(result, GOLD)
    out = os.environ.get("HIMERA_METRICS_OUT")
    if out:
        Path(out).write_text(
            json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(m, ensure_ascii=False, indent=2))
    assert m["record_recall"] >= 0.9
    assert m["entity_recall"] >= 0.8
    assert m["wrong_subject_links"] == []
    assert m["cost_usd"] <= settings.max_cost_usd
