"""JSON API and the Streamlit Label page against a real Postgres (CI `integration` job / local compose).

Run with JUDGECAL_INTEGRATION=1 and PG_* / DATABASE_URL pointing at a migrated, seeded database.
"""

from __future__ import annotations

import os
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from judgecal import db

pytestmark = pytest.mark.skipif(os.environ.get("JUDGECAL_INTEGRATION") != "1", reason="needs Postgres")
KEY = "integration-key-" + "x" * 16
RAG = {"correctness": 3, "completeness": 2, "coherence": 4, "groundedness": 1, "safety": 2}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_KEYS", KEY)
    from judgecal.api import Api

    return TestClient(Starlette(routes=Api().routes()), headers={"X-API-Key": KEY})


def test_queue_from_postgres_matches_db_order(client: TestClient) -> None:
    r = client.get("/api/v1/queue", params={"labeler_id": "it-" + uuid.uuid4().hex[:8], "source": "p1", "limit": 5})
    assert r.status_code == 200
    ids = [i["item_id"] for i in r.json()["items"]]
    with db.connect() as conn:
        sql = "SELECT item_id FROM label_items WHERE source = 'p1' ORDER BY 1 LIMIT 5"
        first = [row["item_id"] for row in conn.execute(sql)]
    assert ids == first


def test_label_roundtrip_and_smoke_labels_excluded_from_stats(client: TestClient) -> None:
    lab = "smoke-it" + uuid.uuid4().hex[:8]
    with db.connect() as conn:
        before = db.label_counts(conn)
        item = db.next_item(conn, lab, "p1")
    assert item is not None
    r = client.post("/api/v1/labels", json={"item_id": item["item_id"], "labeler_id": lab, "scores": RAG})
    assert r.status_code == 201, r.text
    got = client.get("/api/v1/labels", params={"item_id": item["item_id"], "labeler_id": lab}).json()
    assert got["scores"] == RAG
    with db.connect() as conn:
        assert db.label_counts(conn) == before  # smoke-* rows never count as human labels
        assert all(not row["labeler_id"].startswith("smoke-") for row in db.all_labels(conn))
        assert db.next_item(conn, lab, "p1")["item_id"] != item["item_id"]  # type: ignore[index]


def test_schema_status_complete_and_status_endpoint(client: TestClient) -> None:
    with db.connect() as conn:
        st = db.schema_status(conn)
    assert st["pending"] == [] and st["applied"] == st["expected"]
    body = client.get("/api/v1/status").json()
    assert body["schema"]["pending"] == []


def test_label_page_lists_queue_items_from_postgres() -> None:
    from streamlit.testing.v1 import AppTest

    from judgecal.config import REPO_ROOT

    lab = "it-page" + uuid.uuid4().hex[:6]
    with db.connect() as conn:
        expected = [r["item_id"] for r in db.queue_preview(conn, lab, "p1", 5)]
    at = AppTest.from_file(str(REPO_ROOT / "src" / "judgecal" / "webapp" / "main.py"), default_timeout=30)
    at.session_state["labeler"] = lab
    at.run()
    assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert expected[0] in captions  # the item being labeled is the head of the Postgres queue
    shown = at.sidebar.dataframe[0].value["item_id"].tolist()
    assert shown == expected
