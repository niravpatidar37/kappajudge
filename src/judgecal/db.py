"""Postgres access: numbered SQL migrations, labeling queue + labels, and eval-run history.

Migrations are versioned files in migrations/NNNN_*.sql applied by `judgecal migrate` (a separate
step / Cloud Run Job), never at import time. All queries use bound parameters.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from judgecal.config import REPO_ROOT, get_settings

MIGRATIONS_DIR = REPO_ROOT / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
LABELER_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,40}$")


@contextmanager
def connect(dsn: str | None = None) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    url = dsn or get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
        yield conn


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    out = []
    for p in sorted(directory.glob("*.sql")):
        m = _MIGRATION_RE.match(p.name)
        if not m:
            raise ValueError(f"bad migration filename {p.name}")
        out.append((m.group(1), p))
    return out


def migrate(conn: psycopg.Connection[Any], directory: Path = MIGRATIONS_DIR) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, "
        "applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    applied = {
        r["version"] if isinstance(r, dict) else r[0]
        for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    done: list[str] = []
    for version, path in migration_files(directory):
        if version in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))  # trusted, versioned file from the repo
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        done.append(version)
    return done


def ping(conn: psycopg.Connection[Any]) -> bool:
    row = conn.execute("SELECT 1 AS ok").fetchone()
    return bool(row)


# ---------------------------------------------------------------- labeling
def upsert_items(conn: psycopg.Connection[Any], items: list[dict[str, Any]]) -> int:
    n = 0
    with conn.transaction():
        for it in items:
            conn.execute(
                "INSERT INTO label_items (item_id, source, kind, task, response, reference, context, meta) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (item_id) DO NOTHING",
                (
                    it["item_id"],
                    it["source"],
                    it["kind"],
                    it["task"],
                    it["response"],
                    it.get("reference"),
                    it.get("context"),
                    json.dumps(it.get("meta", {})),
                ),
            )
            n += 1
    return n


def next_item(conn: psycopg.Connection[Any], labeler_id: str, source: str | None = None) -> dict[str, Any] | None:
    row: dict[str, Any] | None = conn.execute(
        "SELECT i.* FROM label_items i WHERE (%(src)s::text IS NULL OR i.source = %(src)s) AND NOT EXISTS "
        "(SELECT 1 FROM labels l WHERE l.item_id = i.item_id AND l.labeler_id = %(lab)s AND l.pass_no = 1) "
        "ORDER BY i.item_id LIMIT 1",
        {"src": source, "lab": labeler_id},
    ).fetchone()
    return row


def next_double_label_item(
    conn: psycopg.Connection[Any], labeler_id: str, min_hours: float, max_double: int = 20
) -> dict[str, Any] | None:
    """A first-pass item of this labeler, labeled >= min_hours ago, not yet relabeled; at most max_double."""
    count_row = conn.execute(
        "SELECT count(*) AS n FROM labels WHERE labeler_id = %s AND pass_no = 2", (labeler_id,)
    ).fetchone()
    if count_row and int(count_row["n"]) >= max_double:
        return None
    row: dict[str, Any] | None = conn.execute(
        "SELECT i.* FROM label_items i JOIN labels l1 ON l1.item_id = i.item_id AND l1.labeler_id = %(lab)s "
        "AND l1.pass_no = 1 WHERE l1.created_at <= now() - make_interval(secs => %(secs)s) AND NOT EXISTS "
        "(SELECT 1 FROM labels l2 WHERE l2.item_id = i.item_id AND l2.labeler_id = %(lab)s AND l2.pass_no = 2) "
        "ORDER BY md5(i.item_id) LIMIT 1",
        {"lab": labeler_id, "secs": float(min_hours) * 3600},
    ).fetchone()
    return row


def save_label(
    conn: psycopg.Connection[Any],
    *,
    item_id: str,
    labeler_id: str,
    pass_no: int,
    scores: dict[str, int],
    note: str,
    valid: dict[str, int],
) -> None:
    if not LABELER_RE.match(labeler_id):
        raise ValueError("labeler id must be 2-40 chars of letters, digits, _ . -")
    if pass_no not in (1, 2):
        raise ValueError("pass_no must be 1 or 2")
    for k, v in scores.items():
        if k not in valid or not isinstance(v, int) or not 0 <= v <= valid[k]:
            raise ValueError(f"invalid score {k}={v!r}")
    with conn.transaction():
        conn.execute(
            "INSERT INTO labels (item_id, labeler_id, pass_no, scores, note) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (item_id, labeler_id, pass_no) DO UPDATE SET scores = EXCLUDED.scores, "
            "note = EXCLUDED.note, created_at = now()",
            (item_id, labeler_id, pass_no, json.dumps(scores), note[:2000]),
        )


def all_labels(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = conn.execute(
        "SELECT item_id, labeler_id, pass_no, scores, note, created_at FROM labels ORDER BY created_at"
    ).fetchall()
    return rows


def label_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    row = conn.execute(
        "SELECT (SELECT count(*) FROM label_items) AS items, "
        "(SELECT count(*) FROM labels WHERE pass_no = 1) AS first_pass, "
        "(SELECT count(*) FROM labels WHERE pass_no = 2) AS second_pass"
    ).fetchone()
    return {k: int(v) for k, v in (row or {}).items()}


# ---------------------------------------------------------------- eval runs
def insert_run(conn: psycopg.Connection[Any], record: dict[str, Any], kind: str = "release_gate") -> None:
    jc = record.get("judge_config", {})
    with conn.transaction():
        conn.execute(
            "INSERT INTO eval_runs (run_id, kind, created_at, git_commit, prompt_sha256, judge_config_sha256, "
            "judge_model, rubric_version, summary, n_items, usd, record) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
            (
                record["run_id"],
                kind,
                record["created_at"],
                record.get("git_commit_at_run", ""),
                record.get("prompt_sha256", ""),
                record.get("judge_config_sha256", ""),
                jc.get("model", ""),
                jc.get("rubric_version", ""),
                json.dumps(record.get("summary", {})),
                len(record.get("items", [])),
                record.get("usd", 0.0),
                json.dumps(record),
            ),
        )
        for it in record.get("items", []):
            conn.execute(
                "INSERT INTO eval_run_items (run_id, item_id, scores) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (record["run_id"], str(it.get("question_id") or it.get("item_id")), json.dumps(it.get("scores", {}))),
            )


def list_runs(conn: psycopg.Connection[Any], kind: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = conn.execute(
        "SELECT run_id, kind, created_at, git_commit, prompt_sha256, judge_model, rubric_version, summary, "
        "n_items, usd FROM eval_runs WHERE (%(k)s::text IS NULL OR kind = %(k)s) ORDER BY created_at",
        {"k": kind},
    ).fetchall()
    return rows
