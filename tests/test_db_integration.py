"""Integration tests against a real Postgres (CI service container or the local compose stack).

Run with JUDGECAL_INTEGRATION=1 and PG_* / DATABASE_URL pointing at a migrated, seeded database.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import psycopg
import pytest

from judgecal import db

pytestmark = pytest.mark.skipif(os.environ.get("JUDGECAL_INTEGRATION") != "1", reason="needs Postgres")
VALID = {"correctness": 4, "completeness": 4, "coherence": 4, "groundedness": 4, "safety": 2}


def _labeler() -> str:
    return "t" + uuid.uuid4().hex[:10]


def test_migrations_recorded_and_idempotent() -> None:
    with db.connect() as conn:
        assert db.migrate(conn) == []  # already applied by the CI step; second run applies nothing
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
        assert versions[:2] == ["0001", "0002"]


def test_queue_seeded_with_p1_answers() -> None:
    with db.connect() as conn:
        n = conn.execute("SELECT count(*) AS n FROM label_items WHERE source = 'p1'").fetchone()
        assert n is not None and int(n["n"]) == 128


def test_label_roundtrip_and_validation() -> None:
    lab = _labeler()
    with db.connect() as conn:
        item = db.next_item(conn, lab, "p1")
        assert item is not None
        db.save_label(
            conn,
            item_id=item["item_id"],
            labeler_id=lab,
            pass_no=1,
            scores={"correctness": 3, "safety": 2},
            note="ok",
            valid=VALID,
        )
        nxt = db.next_item(conn, lab, "p1")
        assert nxt is not None and nxt["item_id"] != item["item_id"]
        with pytest.raises(ValueError):
            db.save_label(
                conn, item_id=item["item_id"], labeler_id=lab, pass_no=1, scores={"safety": 3}, note="", valid=VALID
            )
        with pytest.raises(ValueError):
            db.save_label(
                conn,
                item_id=item["item_id"],
                labeler_id="bad id; drop",
                pass_no=1,
                scores={"safety": 1},
                note="",
                valid=VALID,
            )
        row = conn.execute("SELECT scores, created_at FROM labels WHERE labeler_id = %s", (lab,)).fetchone()
        assert row is not None and row["scores"] == {"correctness": 3, "safety": 2} and row["created_at"] is not None


def test_database_enforces_labeler_and_pass_constraints() -> None:
    with db.connect() as conn:
        item = conn.execute("SELECT item_id FROM label_items LIMIT 1").fetchone()
        assert item is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO labels (item_id, labeler_id, pass_no, scores) VALUES (%s, %s, 1, '{}')",
                (item["item_id"], "x' OR 1=1 --"),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO labels (item_id, labeler_id, pass_no, scores) VALUES (%s, 'okid', 3, '{}')",
                (item["item_id"],),
            )
        conn.rollback()


def test_double_label_is_blind_eligible_after_delay_and_capped() -> None:
    lab = _labeler()
    with db.connect() as conn:
        ids = [r["item_id"] for r in conn.execute("SELECT item_id FROM label_items ORDER BY item_id LIMIT 3")]
        for i in ids:
            db.save_label(conn, item_id=i, labeler_id=lab, pass_no=1, scores={"safety": 2}, note="", valid=VALID)
        assert db.next_double_label_item(conn, lab, min_hours=20) is None  # too soon
        item = db.next_double_label_item(conn, lab, min_hours=0)
        assert item is not None and item["item_id"] in ids
        assert "scores" not in item  # first-pass scores are not returned to the UI
        db.save_label(
            conn, item_id=item["item_id"], labeler_id=lab, pass_no=2, scores={"safety": 1}, note="", valid=VALID
        )
        assert db.next_double_label_item(conn, lab, min_hours=0, max_double=1) is None


def test_eval_run_store_and_list() -> None:
    rec = {
        "run_id": str(uuid.uuid4()),
        "created_at": "2026-10-17T00:00:00Z",
        "prompt_sha256": "a" * 64,
        "judge_config_sha256": "b" * 64,
        "judge_config": {"model": "m", "rubric_version": "v2"},
        "summary": {"safety": {"mean": 2.0, "normalized": 1.0, "n": 1}},
        "items": [{"question_id": "q1", "scores": {"safety": 2}}],
        "usd": 0.0,
    }
    with db.connect() as conn:
        db.insert_run(conn, rec, kind="release_gate")
        runs = db.list_runs(conn, "release_gate")
        assert any(str(r["run_id"]) == rec["run_id"] for r in runs)
        items = conn.execute("SELECT scores FROM eval_run_items WHERE run_id = %s", (rec["run_id"],)).fetchall()
        assert json.loads(json.dumps(items[0]["scores"])) == {"safety": 2}


def test_readyz_reports_ready_with_database() -> None:
    from judgecal.asgi import readyz

    resp = asyncio.run(readyz(None))  # type: ignore[arg-type]
    assert resp.status_code == 200
