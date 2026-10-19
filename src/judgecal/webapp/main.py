"""Streamlit UI: labeling queue (single + blind double-label), agreement, eval trend.

Untrusted text (model outputs, prompts) is rendered with st.text / st.code only, never as Markdown
or HTML, so it cannot inject links, images or scripts.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import pandas as pd
import streamlit as st

from judgecal import db
from judgecal.agreement import judge_vs_labels, self_agreement
from judgecal.config import REPO_ROOT, get_settings
from judgecal.rubric import load_rubric
from judgecal.runs import read_jsonl

RUBRIC_VERSION = os.environ.get("RUBRIC_VERSION", "")
DOUBLE_LABEL_MIN_HOURS = float(os.environ.get("DOUBLE_LABEL_MIN_HOURS", "20"))
MAX_SCORES = {"correctness": 4, "completeness": 4, "coherence": 4, "groundedness": 4, "safety": 2}


def _rubric_version() -> str:
    if RUBRIC_VERSION:
        return RUBRIC_VERSION
    cfg = json.loads((REPO_ROOT / "judge_release.json").read_text(encoding="utf-8"))
    return str(cfg["rubric_version"])


def _iap_labeler() -> str:
    """Behind IAP the verified user email arrives in a header; use its local part as the labeler id."""
    try:
        email = st.context.headers.get("X-Goog-Authenticated-User-Email", "")
    except Exception:
        email = ""
    local = email.split(":")[-1].split("@")[0]
    return re.sub(r"[^A-Za-z0-9_.\-]", "", local)[:40]


def _labeler() -> str:
    default = _iap_labeler()
    lab = st.sidebar.text_input("Labeler id", value=st.session_state.get("labeler", default), disabled=bool(default))
    if lab and not db.LABELER_RE.match(lab):
        st.sidebar.error("2-40 chars: letters, digits, _ . -")
        return ""
    st.session_state["labeler"] = lab
    return str(lab or "")


def _show_item(item: dict[str, Any]) -> None:
    st.caption(f"{item['item_id']}  ·  source={item['source']}  ·  kind={item['kind']}")
    st.markdown("**Input (task / question)**")
    st.text(item["task"])
    st.markdown("**Output (response under evaluation)**")
    st.code(item["response"], language=None, wrap_lines=True)
    if item.get("reference"):
        with st.expander("Reference (gold answer and evidence)"):
            st.text(item["reference"])
    if item.get("context"):
        with st.expander("Retrieved context / citations"):
            st.text(item["context"])


def _score_form(item: dict[str, Any], labeler: str, pass_no: int) -> None:
    rubric = load_rubric(_rubric_version())
    crits = rubric.names(item["kind"])
    with st.expander("Rubric (every point defined)", expanded=False):
        st.text(rubric.render(crits, with_anchors=False))
    with st.form(key=f"form-{item['item_id']}-{pass_no}", clear_on_submit=True):
        scores: dict[str, int | None] = {}
        for name in crits:
            c = rubric.criterion(name)
            scores[name] = st.radio(
                f"{name}: {c.question}",
                sorted(c.scale),
                horizontal=True,
                index=None,
                key=f"{item['item_id']}-{name}-{pass_no}",
                help="  \n".join(f"{k}: {v}" for k, v in sorted(c.scale.items())),
            )
        note = st.text_area("Note (why; what is ambiguous?)", max_chars=2000)
        writes_enabled = get_settings().label_writes_enabled  # kill switch, read on every render
        if not writes_enabled:
            st.warning("Labeling is read-only right now (LABEL_WRITES_ENABLED=false).")
        if st.form_submit_button("Save label", disabled=not writes_enabled):
            if any(v is None for v in scores.values()):
                st.error("Score every criterion before saving.")
                return
            with db.connect() as conn:
                db.save_label(
                    conn,
                    item_id=item["item_id"],
                    labeler_id=labeler,
                    pass_no=pass_no,
                    scores={k: int(v) for k, v in scores.items() if v is not None},
                    note=note,
                    valid={k: rubric.criterion(k).max_score for k in crits},
                )
            st.success("Saved.")
            st.rerun()


def page_label() -> None:
    st.header("Label outputs")
    labeler = _labeler()
    if not labeler:
        st.info("Enter a labeler id in the sidebar.")
        return
    source = st.sidebar.selectbox("Queue", ["p1", "helpsteer2"], index=0)
    with db.connect() as conn:
        counts = db.label_counts(conn)
        upcoming = db.queue_preview(conn, labeler, source, limit=5)  # same query as GET /api/v1/queue
    st.sidebar.write(counts)
    if not upcoming:
        st.success("Queue done for this labeler.")
        return
    item = upcoming[0]
    with st.sidebar.expander(f"Up next ({len(upcoming)} shown)"):
        st.dataframe(pd.DataFrame([{"item_id": r["item_id"], "kind": r["kind"]} for r in upcoming]), hide_index=True)
    _show_item(item)
    _score_form(item, labeler, pass_no=1)


def page_double() -> None:
    st.header("Double-label (blind re-label for self-agreement)")
    st.caption(
        f"Shows items you labeled at least {DOUBLE_LABEL_MIN_HOURS:g} h ago; your first scores are hidden. "
        "Target: 20 items."
    )
    labeler = _labeler()
    if not labeler:
        return
    with db.connect() as conn:
        item = db.next_double_label_item(conn, labeler, DOUBLE_LABEL_MIN_HOURS)
    if item is None:
        st.info("Nothing eligible yet (or 20 re-labels already done).")
        return
    _show_item(item)
    _score_form(item, labeler, pass_no=2)


def page_agreement() -> None:
    st.header("Agreement")
    with db.connect() as conn:
        labels = db.all_labels(conn)
    st.write(f"{len(labels)} labels stored.")
    sa = self_agreement(labels, MAX_SCORES)
    st.subheader(f"Self-agreement (pass 1 vs pass 2): {sa['pairs']} pairs")
    if sa["criteria"]:
        st.dataframe(pd.DataFrame(sa["criteria"]).T)
    judge_file = REPO_ROOT / "results" / "judgments" / "p1_answers.jsonl"
    judge = {r["item_id"]: r["scores"] for r in read_jsonl(judge_file) if "scores" in r}
    jv = judge_vs_labels(labels, judge, MAX_SCORES)
    st.subheader(f"Judge vs your labels: {jv['items']} items")
    if jv["criteria"]:
        st.dataframe(pd.DataFrame(jv["criteria"]).T)


def page_trend() -> None:
    st.header("Eval scores over time")
    kind = st.selectbox("Run kind", ["release_gate", "calibration"])
    with db.connect() as conn:
        runs = db.list_runs(conn, kind)
    if not runs:
        st.info("No runs stored yet. `make eval` stores each run; `judgecal import-runs` loads history.")
        return
    rows = []
    for r in runs:
        for crit, v in (r["summary"] or {}).items():
            val = v.get("normalized", v.get("qwk")) if isinstance(v, dict) else v
            rows.append(
                {
                    "created_at": r["created_at"],
                    "criterion": crit,
                    "score": val,
                    "prompt": r["prompt_sha256"][:8],
                    "rubric": r["rubric_version"],
                }
            )
    df = pd.DataFrame(rows)
    st.line_chart(df, x="created_at", y="score", color="criterion")
    st.dataframe(pd.DataFrame(runs).drop(columns=["summary"]))


def main() -> None:
    st.set_page_config(page_title="Judge calibration", layout="wide")
    nav = st.navigation(
        [
            st.Page(page_label, title="Label", url_path="label"),
            st.Page(page_double, title="Double-label", url_path="double"),
            st.Page(page_agreement, title="Agreement", url_path="agreement"),
            st.Page(page_trend, title="Eval trend", url_path="trend"),
        ]
    )
    nav.run()


main()
