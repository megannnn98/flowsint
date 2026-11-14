"""A flow step skipped for lack of inputs must be reported in the sketch console."""

from typing import Any, List
from unittest.mock import MagicMock

import pytest

from flowsint_core.core import orchestrator as orchestrator_module
from flowsint_core.core.orchestrator import FlowOrchestrator
from flowsint_core.core.types import FlowBranch, FlowStep


class FakeEnricher:
    def __init__(self, name: str, outputs: List[Any]):
        self._name = name
        self._outputs = outputs
        self.calls: List[List[Any]] = []

    def name(self) -> str:
        return self._name

    async def execute(self, values: List[Any]) -> List[Any]:
        self.calls.append(values)
        return self._outputs


def step(node_id: str, step_type: str, depth: int) -> FlowStep:
    return FlowStep(
        nodeId=node_id,
        type=step_type,
        inputs={},
        outputs={},
        status="pending",
        branchId="branch-0",
        depth=depth,
    )


@pytest.fixture
def logger(monkeypatch, tmp_path):
    # The orchestrator writes enricher_logs/*.json relative to the working directory.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(FlowOrchestrator, "_load_enrichers", lambda self: None)
    mock = MagicMock()
    monkeypatch.setattr(orchestrator_module, "Logger", mock)
    return mock


def test_step_after_empty_result_is_skipped_with_console_warning(logger):
    branch = FlowBranch(
        id="branch-0",
        name="Main Flow",
        steps=[
            step("Domain-1", "type", 0),
            step("domain_to_ip-2", "enricher", 1),
            step("ip_to_infos-3", "enricher", 2),
        ],
    )
    resolve = FakeEnricher("domain_to_ip", outputs=[])
    geolocate = FakeEnricher("ip_to_infos", outputs=["unused"])
    orchestrator = FlowOrchestrator("sketch", "scan", [branch])
    orchestrator.enrichers = {"domain_to_ip-2": resolve, "ip_to_infos-3": geolocate}

    orchestrator.scan(values=["gmail.com"])

    assert geolocate.calls == []
    warnings = [call.args[1]["message"] for call in logger.warn.call_args_list]
    assert warnings == [
        "Enricher ip_to_infos skipped: previous step returned no results."
    ]
