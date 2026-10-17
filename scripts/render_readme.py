"""Render README.md from docs/README.template.md + the raw result JSON files (no number typed by hand).

  python scripts/render_readme.py
Placeholders: {{NAME}} replaced by the functions below; missing results render as "not yet measured".
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
NYM = "not yet measured"
CRIT = ["correctness", "completeness", "coherence"]
HS2 = {"correctness": "correctness", "completeness": "helpfulness", "coherence": "coherence"}


def _j(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


CAL = _j(RES / "calibration_summary.json") or {"runs": {}, "human_ceiling": {}}
BIAS = _j(RES / "bias_summary.json") or {}
RELEASE = _j(ROOT / "judge_release.json") or {}
FINAL = RELEASE.get("rubric_version", "v?")


def _run(name: str) -> dict[str, Any] | None:
    r = CAL["runs"].get(name)
    return r if isinstance(r, dict) else None


def _fmt(x: Any, nd: int = 2) -> str:
    return NYM if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def test_table() -> str:
    t = _run(f"calib_{FINAL}_test_reason_first")
    ceil = CAL["human_ceiling"].get("test", {})
    lines = [
        "| Criterion (HelpSteer2 attribute) | n | Judge QWK [95% CI] | Spearman | Exact | Within 1 | "
        "Human pairwise QWK | Human leave-one-out QWK |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in CRIT:
        h = ceil.get(c, {})
        if t:
            m = t["criteria"][c]
            ci = m.get("qwk_ci95", [None, None])
            lines.append(
                f"| {c} ({HS2[c]}) | {m['n']} | **{m['qwk']:.2f}** [{ci[0]:.2f}, {ci[1]:.2f}] | "
                f"{m['spearman']:.2f} | {m['exact']:.2f} | {m['within_1']:.2f} | "
                f"{h.get('pairwise_qwk_mean', 0):.2f} | {h.get('leave_one_out_qwk', 0):.2f} |"
            )
        else:
            lines.append(
                f"| {c} ({HS2[c]}) | 0 | {NYM} | | | | {h.get('pairwise_qwk_mean', 0):.2f} | "
                f"{h.get('leave_one_out_qwk', 0):.2f} |"
            )
    return "\n".join(lines)


def test_n() -> str:
    t = _run(f"calib_{FINAL}_test_reason_first")
    return str(t["n_items"]) if t else "0"


def test_mean_qwk() -> str:
    t = _run(f"calib_{FINAL}_test_reason_first")
    return f"{t['mean_qwk']:.2f}" if t else NYM


def iteration_table() -> str:
    lines = [
        "| Rubric | Split | n | correctness QWK | completeness QWK | coherence QWK | mean |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for _name, r in sorted(CAL["runs"].items()):
        if r["order"] != "reason_first":
            continue
        if r["split"] == "dev":
            for label, block in (("DEV (all scored)", r), ("DEV subset (6 per level)", r.get("dev_subset", {}))):
                if not block.get("criteria"):
                    continue
                q = [block["criteria"][c]["qwk"] for c in CRIT]
                lines.append(
                    f"| {r['rubric']} | {label} | {block['n_items']} | "
                    + " | ".join(f"{x:.2f}" for x in q)
                    + f" | {sum(q) / 3:.2f} |"
                )
        else:
            q = [r["criteria"][c]["qwk"] for c in CRIT]
            lines.append(
                f"| {r['rubric']} | TEST | {r['n_items']} | "
                + " | ".join(f"{x:.2f}" for x in q)
                + f" | {sum(q) / 3:.2f} |"
            )
    return "\n".join(lines)


def position_table() -> str:
    pos = BIAS.get("position") or {}
    if not pos:
        return NYM
    lines = [
        "| Judge | Pairs (both orders) | Flip rate [95% CI] | First-slot win share | Agreement with human "
        "(per judgment) | Longer answer wins (consistent verdicts) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for judge, p in pos.items():
        ci = p["flip_rate_ci95"]
        lines.append(
            f"| {judge} (MT-bench) | {p['pairs']} | **{p['flip_rate']:.0%}** [{ci[0]:.0%}, {ci[1]:.0%}] | "
            f"{_pct(p['first_slot_win_share_of_non_tie'])} | {_pct(p['agreement_with_human_per_judgment'])} | "
            f"{_pct(p['longer_answer_wins_share_consistent'])} |"
        )
    sp = BIAS.get("self_preference") or {}
    for judge in ("openai/gpt-oss-20b", "qwen/qwen3.8-27b"):
        if judge in sp:
            lines.append(
                f"| {judge} (self-preference pairs) | {sp['pairs_judged_by_both']} | "
                f"{sp[judge]['position_flip_rate_on_these_pairs']:.0%} | | | |"
            )
    return "\n".join(lines)


def _pct(x: Any) -> str:
    return NYM if x is None else f"{x:.0%}"


def selfpref_table() -> str:
    sp = BIAS.get("self_preference")
    if not sp:
        return (
            f"{NYM}. Answers from both models are generated and committed "
            "(`results/bias/selfpref_answers.jsonl`); resume the judging with "
            "`uv run python scripts/bias_selfpref.py --stage judge --n 10`."
        )
    lines = [
        "| Judge | Own family win rate (judged by itself) | Same answers judged by the other model | "
        "Self-preference delta [95% CI] |",
        "|---|---:|---:|---:|",
    ]
    for judge in ("openai/gpt-oss-20b", "qwen/qwen3.8-27b"):
        s = sp[judge]
        ci = s["self_preference_delta_ci95"]
        lines.append(
            f"| {judge} | {s['own_family_win_rate_by_self']:.0%} | {s['same_answers_win_rate_by_other_judge']:.0%}"
            f" | **{s['self_preference_delta']:+.2f}** [{ci[0]:+.2f}, {ci[1]:+.2f}] |"
        )
    return "\n".join(lines) + f"\n\nPairs judged by both judges: n = {sp['pairs_judged_by_both']} (planned 20)."


def length_section() -> str:
    ln = BIAS.get("length") or {}
    out = []
    corr = ln.get("correlational_test_split")
    if corr:
        out.append(
            f"Correlational (TEST, rubric {corr['rubric']}, n = {corr['n']}): Spearman of judge score vs "
            "response length inside each human score bucket, and of (judge - human) vs length:"
        )
        out.append("")
        out.append("| Criterion | n-weighted within-bucket rho | rho(judge - human, length) |")
        out.append("|---|---:|---:|")
        for c in CRIT:
            v = corr["criteria"][c]
            out.append(
                f"| {c} | {_fmt(v['n_weighted_mean_within_bucket_rho'])} | {_fmt(v['spearman_residual_vs_length'])} |"
            )
    else:
        out.append(f"Correlational: {NYM}.")
    pert = ln.get("controlled_perturbation")
    out.append("")
    if pert:
        out.append(
            f"Controlled (n = {pert['n']} TEST items, planned 30; +{pert['mean_added_chars']:.0f} chars of "
            "neutral preamble/closing, content unchanged): mean score change padded - original:"
        )
        out.append("")
        out.append("| Criterion | Mean delta [95% CI] | Share up | Share down |")
        out.append("|---|---:|---:|---:|")
        for c in CRIT:
            v = pert["criteria"][c]
            out.append(
                f"| {c} | **{v['mean_delta']:+.2f}** [{v['ci95'][0]:+.2f}, {v['ci95'][1]:+.2f}] | "
                f"{v['share_up']:.0%} | {v['share_down']:.0%} |"
            )
    else:
        out.append(f"Controlled perturbation: {NYM} (resume: `uv run python scripts/bias_length.py --rubric {FINAL}`).")
    return "\n".join(out)


def order_section() -> str:
    oe = BIAS.get("reason_vs_score_order")
    if not oe:
        return (
            f"{NYM} (resume: `uv run python scripts/run_calibration.py --rubric {FINAL} --split dev --order "
            "score_first --ids-from results/order_experiment_ids.jsonl`)."
        )
    out = []
    for rubric, o in oe.items():
        out.append(f"Rubric {rubric}, n = {o['n_items']} DEV items (planned 15), temperature 0:")
        out.append("")
        out.append("| Criterion | Reason-first QWK | Score-first QWK | Agreement between orders (QWK) | Same score |")
        out.append("|---|---:|---:|---:|---:|")
        for c in CRIT:
            v = o["criteria"][c]
            out.append(
                f"| {c} | {v['reason_first_qwk_vs_human']:.2f} | {v['score_first_qwk_vs_human']:.2f} | "
                f"{v['agreement_between_orders_qwk']:.2f} | {v['exact_same_score_share']:.0%} |"
            )
    return "\n".join(out)


def gate_table() -> str:
    base = _j(ROOT / "eval_runs" / "baseline.json")
    if not base:
        return NYM
    lines = ["| Criterion | Baseline mean (normalized) | Allowed drop |", "|---|---:|---:|"]
    cfg = _j(ROOT / "gate_config.json")["max_drop_normalized"]
    for c, v in base["summary"].items():
        lines.append(
            f"| {c} | {v['mean']:.2f} / {base['max_scores'][c]} ({v['normalized']:.2f}) | {cfg.get(c, 0):.2f} |"
        )
    return (
        "\n".join(lines) + f"\n\nBaseline run `{base['run_id'][:8]}`, {len(base['items'])} items, ${base['usd']:.4f}."
    )


def demo_pr() -> str:
    d = _j(RES / "demo_pr.json")
    if not d:
        return NYM
    return f"[{d['url']}]({d['url']}) (label `demo-regression`, left open). CI: {d['ci']}"


def _snapshot() -> None:
    """Write results/spend_snapshot.json from the shared ledger (only when the ledger is readable)."""
    import os
    import time
    from collections import defaultdict

    path = Path(os.environ.get("LLM_SPEND_LEDGER", "F:/.llm-spend/ledger.jsonl"))
    if not path.is_file():
        return
    by_model: dict[str, float] = defaultdict(float)
    calls = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("project") == "p4-judge":
            by_model[row.get("model", "?")] += float(row.get("usd", 0))
            calls += 1
    snap = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "p4_usd": round(sum(by_model.values()), 6),
        "calls": calls,
        "by_model": {k: round(v, 6) for k, v in by_model.items()},
    }
    (RES / "spend_snapshot.json").write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")


def spend() -> str:
    path = Path("F:/.llm-spend/ledger.jsonl")
    snap = _j(RES / "spend_snapshot.json")
    if snap:
        bm = snap["by_model"]
        qwen = bm.get("qwen/qwen3.8-27b", 0)
        oss = bm.get("openai/gpt-oss-20b", 0)
        return (
            f"**${snap['p4_usd']:.4f}** across {snap['calls']} paid calls (qwen3.8-27b ${qwen:.4f}, "
            f"gpt-oss-20b ${oss:.4f}), read from the shared ledger at {snap['ts']}"
        )
    return NYM if not path.is_file() else "see results/spend_snapshot.json"


def main() -> None:
    _snapshot()
    tpl = (ROOT / "docs" / "README.template.md").read_text(encoding="utf-8")
    funcs = {k: v for k, v in globals().items() if callable(v) and not k.startswith("_") and k != "main"}

    def repl(m: re.Match[str]) -> str:
        name = m.group(1).lower()
        return str(funcs[name]()) if name in funcs else m.group(0)

    out = re.sub(r"\{\{(\w+)\}\}", repl, tpl)
    out = out.replace("{FINAL}", FINAL)
    (ROOT / "README.md").write_text(out, encoding="utf-8", newline="\n")
    print("README.md rendered", file=sys.stderr)


if __name__ == "__main__":
    main()
