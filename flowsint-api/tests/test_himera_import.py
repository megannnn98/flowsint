"""End-to-end Himera import through the API with a mocked OpenRouter and graph."""

import importlib.util
import sys
from pathlib import Path

import pytest

from flowsint_core.core.auth import create_access_token
from flowsint_core.core.models import (
    HimeraImport,
    Investigation,
    InvestigationUserRole,
    Profile,
    Sketch,
)
from flowsint_core.core.services.himera_import_service import (
    create_himera_import_service,
)
from flowsint_core.core.types import Role
from flowsint_core.himera.config import HimeraSettings

CORE_TESTS = Path(__file__).resolve().parents[2] / "flowsint-core" / "tests" / "himera"


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"himera_{name}", CORE_TESTS / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fakes = _load("fakes")


@pytest.fixture
def db_session():
    """In-memory SQLite shared across threads (TestClient runs handlers in a worker thread)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from flowsint_core.core.models import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class MergingGraph:
    def __init__(self):
        self.nodes, self.edges = {}, {}

    def batch_create_nodes(self, nodes):
        ids = []
        for n in nodes:
            key = (n["nodeType"], n["nodeLabel"])
            self.nodes.setdefault(key, {}).update(n)
            ids.append("|".join(key))
        return {"nodes_created": len(ids), "node_ids": ids, "errors": []}

    def batch_create_edges_by_element_id(self, edges):
        for e in edges:
            self.edges[tuple(sorted((k, str(v)) for k, v in e.items()))] = e
        return {"edges_created": len(edges), "errors": []}


@pytest.fixture
def setup(db_session, client, monkeypatch):
    user = Profile(email="analyst@example.com", hashed_password="x")
    viewer = Profile(email="viewer@example.com", hashed_password="x")
    db_session.add_all([user, viewer])
    db_session.flush()
    inv = Investigation(name="inv", owner_id=user.id)
    db_session.add(inv)
    db_session.flush()
    sketch = Sketch(title="s", investigation_id=inv.id, owner_id=user.id)
    db_session.add(sketch)
    db_session.add_all(
        [
            InvestigationUserRole(
                user_id=user.id, investigation_id=inv.id, roles=[Role.OWNER]
            ),
            InvestigationUserRole(
                user_id=viewer.id, investigation_id=inv.id, roles=[Role.VIEWER]
            ),
        ]
    )
    db_session.commit()

    fake = fakes.FakeOpenRouter(
        {
            "himera_records": fakes.perfect_records_handler(),
            "himera_entities": fakes.perfect_entities_handler(),
        }
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(
        HimeraSettings,
        "from_env",
        classmethod(lambda cls, env=None: HimeraSettings(pages_per_chunk=2)),
    )

    from app.api.routes import himera as route

    async def process(import_id, pdf_bytes):
        await create_himera_import_service(db_session).process(
            import_id, pdf_bytes, client_factory=lambda key: fake
        )

    graph = MergingGraph()
    monkeypatch.setattr(route, "_process_in_background", process)
    monkeypatch.setattr(route, "create_graph_service", lambda **kw: graph)

    def headers(email="analyst@example.com"):
        return {"Authorization": f"Bearer {create_access_token({'sub': email})}"}

    return {"sketch": sketch, "fake": fake, "graph": graph, "headers": headers}


def upload(client, setup, headers=None, name="report.pdf", data=None):
    return client.post(
        f"/api/sketches/{setup['sketch'].id}/import/himera",
        files={"file": (name, data or fakes.synthetic_pdf(), "application/pdf")},
        headers=headers or setup["headers"](),
    )


def test_full_flow_upload_preview_import(client, setup):
    res = upload(client, setup)
    assert res.status_code == 200, res.text
    import_id = res.json()["id"]
    assert res.json()["status"] == "processing"

    res = client.get(
        f"/api/sketches/{setup['sketch'].id}/import/himera/{import_id}",
        headers=setup["headers"](),
    )
    body = res.json()
    assert body["status"] == "completed", body
    assert body["cost_usd"] > 0 and body["model"] == "test/model"
    progress = body["progress"]
    assert progress["stage"] == "validating" and progress["calls"] >= 2
    result = body["result"]
    assert len(result["records"]) == 11
    assert result["ambiguous_relationships"] and result["unverified_entities"]

    res = client.post(
        f"/api/sketches/{setup['sketch'].id}/import/himera/{import_id}/execute",
        json={},
        headers=setup["headers"](),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"
    graph = setup["graph"]
    assert ("email", "petrov.ps@example.com") in graph.nodes
    assert ("username", "petrov_ps") in graph.nodes
    worked = [e for e in graph.edges.values() if e["rel_label"] == "WORKED_AT"]
    assert worked[0]["record_id"] == "p02-03" and worked[0]["page_start"] == 2


def test_same_pdf_is_not_analysed_twice(client, setup, db_session):
    first = upload(client, setup).json()
    calls = len(setup["fake"].calls)
    second = upload(client, setup).json()
    assert second["id"] == first["id"] and second["reused"] is True
    assert len(setup["fake"].calls) == calls
    assert db_session.query(HimeraImport).count() == 1


def test_viewer_cannot_upload(client, setup):
    res = upload(client, setup, headers=setup["headers"]("viewer@example.com"))
    assert res.status_code == 403


def test_rejects_non_pdf(client, setup):
    assert upload(client, setup, name="a.txt").status_code == 400
    assert upload(client, setup, data=b"hello").status_code == 400


def test_missing_key_marks_failed(client, setup, monkeypatch, db_session):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    from app.api.routes import himera as route

    async def process(import_id, pdf_bytes):
        await create_himera_import_service(db_session).process(import_id, pdf_bytes)

    monkeypatch.setattr(route, "_process_in_background", process)
    import_id = upload(client, setup).json()["id"]
    body = client.get(
        f"/api/sketches/{setup['sketch'].id}/import/himera/{import_id}",
        headers=setup["headers"](),
    ).json()
    assert body["status"] == "failed" and "OPENROUTER_API_KEY" in body["error"]


def test_stale_processing_row_is_not_reused(client, setup, db_session):
    from datetime import datetime, timedelta, timezone

    first = upload(client, setup).json()
    row = db_session.get(HimeraImport, __import__("uuid").UUID(first["id"]))
    row.status = "processing"
    row.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db_session.commit()
    second = upload(client, setup).json()
    assert second["id"] != first["id"] and second["reused"] is False


def test_balance_endpoint(client, setup, monkeypatch):
    from flowsint_core.core.services import himera_import_service as svc
    from flowsint_core.himera.openrouter import Balance, OpenRouterClient

    async def fake_balance(self):
        return Balance(remaining_usd=4.25, total_usage=5.75, total_credits=10.0)

    monkeypatch.setattr(OpenRouterClient, "get_balance", fake_balance)
    res = client.get(
        f"/api/sketches/{setup['sketch'].id}/import/himera/balance",
        headers=setup["headers"](),
    )
    assert res.status_code == 200, res.text
    assert res.json()["remaining_usd"] == 4.25
    monkeypatch.delenv("OPENROUTER_API_KEY")
    res = client.get(
        f"/api/sketches/{setup['sketch'].id}/import/himera/balance",
        headers=setup["headers"](),
    )
    assert res.status_code == 400 and "OPENROUTER_API_KEY" in res.json()["detail"]
