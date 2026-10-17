"""Compute every reported number from the raw result files. No number in the README is typed by hand.

python scripts/report.py calibration          -> results/calibration_summary.json, iteration_curve.png
python scripts/report.py disagreements v1     -> results/disagreements_v1_dev.md (DEV only)
python scripts/report.py bias                 -> results/bias_summary.json
python scripts/report.py all
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from judgecal.datasets import CRITERION_TO_HS2, load_sample  # noqa: E402
from judgecal.metrics import agreement_report, inter_annotator, qwk, spearman  # noqa: E402
from judgecal.runs import read_jsonl  # noqa: E402

RES = ROOT / "results"
JUD = RES / "judgments"
CRITERIA = list(CRITERION_TO_HS2)
FILE_RE = re.compile(r"^calib_(v\d+)_(dev|test)_(reason_first|score_first)(?:_(.+))?$")


def _ok(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if "scores" in r]


def calibration() -> dict[str, Any]:
    sample = {s["item_id"]: s for s in load_sample()}
    runs: dict[str, Any] = {}
    for path in sorted(JUD.glob("calib_*.jsonl")):
        m = FILE_RE.match(path.stem)
        if not m:
            continue
        rows = _ok(read_jsonl(path))
        if not rows:
            continue
        rep: dict[str, Any] = {
            "file": str(path.relative_to(ROOT)).replace("\\", "/"),
            "rubric": m.group(1),
            "split": m.group(2),
            "order": m.group(3),
            "variant": m.group(4) or "",
            "n_items": len(rows),
            "errors": len(read_jsonl(path)) - len(rows),
            "criteria": {},
        }
        for c in CRITERIA:
            j = [r["scores"][c] for r in rows]
            h = [sample[r["item_id"]]["human"][CRITERION_TO_HS2[c]] for r in rows]
            rep["criteria"][c] = agreement_report(j, h, 4)
        rep["mean_qwk"] = round(float(np.mean([rep["criteria"][c]["qwk"] for c in CRITERIA])), 4)
        if rep["split"] == "dev":
            keep = {r["item_id"] for r in read_jsonl(RES / "dev_subset_ids.jsonl")}
            sub = [r for r in rows if r["item_id"] in keep]
            rep["dev_subset"] = {"n_items": len(sub), "criteria": {}}
            if len(sub) >= 10:
                for c in CRITERIA:
                    j = [r["scores"][c] for r in sub]
                    h = [sample[r["item_id"]]["human"][CRITERION_TO_HS2[c]] for r in sub]
                    rep["dev_subset"]["criteria"][c] = agreement_report(j, h, 4)
                rep["dev_subset"]["mean_qwk"] = round(
                    float(np.mean([rep["dev_subset"]["criteria"][c]["qwk"] for c in CRITERIA])), 4
                )
        runs[path.stem] = rep
    ceiling: dict[str, Any] = {}
    for split in ("dev", "test"):
        items = [s for s in sample.values() if s["split"] == split]
        ceiling[split] = {
            c: inter_annotator([s["annotators"][h] for s in items], 4) for c, h in CRITERION_TO_HS2.items()
        }
    out = {"criterion_to_helpsteer2_attribute": CRITERION_TO_HS2, "runs": runs, "human_ceiling": ceiling}
    (RES / "calibration_summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    _chart(runs, ceiling)
    return out


def _chart(runs: dict[str, Any], ceiling: dict[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dev = sorted(
        [
            r
            for r in runs.values()
            if r["split"] == "dev" and r["order"] == "reason_first" and not r["variant"]
            and r.get("dev_subset", {}).get("criteria")
        ],
        key=lambda r: int(r["rubric"][1:]),
    )
    if not dev:
        return
    xs = [r["rubric"] for r in dev]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    colors = {"correctness": "tab:blue", "completeness": "tab:orange", "coherence": "tab:green"}
    for c in CRITERIA:
        ax.plot(xs, [r["dev_subset"]["criteria"][c]["qwk"] for r in dev], marker="o", color=colors[c],
                label=f"{c} (DEV n={dev[-1]['dev_subset']['n_items']})")
        test = [r for r in runs.values() if r["split"] == "test" and r["order"] == "reason_first" and not r["variant"]]
        for t in test:
            ax.scatter(
                [t["rubric"]],
                [t["criteria"][c]["qwk"]],
                marker="*",
                s=160,
                color=colors[c],
                edgecolor="black",
                zorder=5,
            )
        ax.axhline(ceiling["test"][c]["leave_one_out_qwk"], color=colors[c], ls=":", lw=1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("rubric iteration (tuned on DEV only)")
    ax.set_ylabel("quadratic-weighted kappa vs HelpSteer2 humans")
    ax.set_title("Judge-human agreement per iteration (stars = TEST, dotted = human LOO ceiling)")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(RES / "iteration_curve.png", dpi=120)


def disagreements(rubric: str, k: int = 20) -> None:
    sample = {s["item_id"]: s for s in load_sample()}
    rows = _ok(read_jsonl(JUD / f"calib_{rubric}_dev_reason_first.jsonl"))
    scored = []
    for r in rows:
        h = {c: sample[r["item_id"]]["human"][CRITERION_TO_HS2[c]] for c in CRITERIA}
        gap = sum(abs(r["scores"][c] - h[c]) for c in CRITERIA)
        scored.append((gap, r, h))
    scored.sort(key=lambda x: -x[0])
    lines = [f"# 20 largest judge-human disagreements, rubric {rubric}, DEV split only", ""]
    for gap, r, h in scored[:k]:
        s = sample[r["item_id"]]
        lines += [
            f"## {r['item_id']} (total gap {gap})",
            "",
            f"**Prompt:** {s['prompt'][:600]}",
            "",
            f"**Response:** {s['response'][:1200]}",
            "",
            "| criterion | judge | human | annotators | judge reasoning |",
            "|---|---|---|---|---|",
        ]
        for c in CRITERIA:
            reason = r["reasoning"][c].replace("|", "/").replace("\n", " ")
            lines.append(f"| {c} | {r['scores'][c]} | {h[c]} | {s['annotators'][CRITERION_TO_HS2[c]]} | {reason} |")
        lines += ["", "**Diagnosis:** _see notes in rubrics/CHANGELOG.md_", ""]
    (RES / f"disagreements_{rubric}_dev.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------------------ bias
def _boot(vals: list[float], n: int = 2000, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    means = sorted(float(np.mean([rng.choice(vals) for _ in vals])) for _ in range(n))
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)]


def position_summary(path: Path) -> dict[str, Any] | None:
    rows = [r for r in read_jsonl(path) if "verdict_xy" in r]
    if not rows:
        return None
    flips = [r["verdict_xy"] != r["verdict_yx"] for r in rows]
    first_slot = [s for r in rows for s in (r["slot_xy"], r["slot_yx"]) if s != "tie"]
    consistent = [r for r in rows if r["verdict_xy"] == r["verdict_yx"]]
    agree_each = [v == r["human_winner"] for r in rows for v in (r["verdict_xy"], r["verdict_yx"])]
    longer_wins = [
        ((r["verdict_xy"] == r["model_x"]) == (r["len_x"] > r["len_y"]))
        for r in consistent
        if r["verdict_xy"] != "tie" and r["len_x"] != r["len_y"]
    ]
    return {
        "pairs": len(rows),
        "flip_rate": round(float(np.mean(flips)), 4),
        "flip_rate_ci95": _boot([float(f) for f in flips]),
        "first_slot_win_share_of_non_tie": round(float(np.mean([s == "A" for s in first_slot])), 4)
        if first_slot
        else None,
        "agreement_with_human_per_judgment": round(float(np.mean(agree_each)), 4),
        "agreement_with_human_consistent_only": round(
            float(np.mean([r["verdict_xy"] == r["human_winner"] for r in consistent])), 4
        )
        if consistent
        else None,
        "consistent_pairs": len(consistent),
        "longer_answer_wins_share_consistent": round(float(np.mean(longer_wins)), 4) if longer_wins else None,
    }


def selfpref_summary() -> dict[str, Any] | None:
    rows = [r for r in read_jsonl(RES / "bias" / "selfpref_verdicts.jsonl") if "winner_gptoss_first" in r]
    if not rows:
        return None
    g, q = "openai/gpt-oss-20b", "qwen/qwen3.8-27b"
    by_judge: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        by_judge[r["judge"]][r["question_id"]] = r
    common = sorted(set(by_judge[g]) & set(by_judge[q]))

    def share(r: dict[str, Any], fam: str) -> float:
        vals = [
            1.0 if w == fam else 0.5 if w == "tie" else 0.0 for w in (r["winner_gptoss_first"], r["winner_qwen_first"])
        ]
        return float(np.mean(vals))

    out: dict[str, Any] = {"pairs_judged_by_both": len(common)}
    for judge, own, other in ((g, g, q), (q, q, g)):
        own_by_self = [share(by_judge[judge][i], own) for i in common]
        own_by_other = [share(by_judge[other][i], own) for i in common]
        diffs = [a - b for a, b in zip(own_by_self, own_by_other, strict=True)]
        flips = [by_judge[judge][i]["winner_gptoss_first"] != by_judge[judge][i]["winner_qwen_first"] for i in common]
        out[judge] = {
            "own_family_win_rate_by_self": round(float(np.mean(own_by_self)), 4),
            "same_answers_win_rate_by_other_judge": round(float(np.mean(own_by_other)), 4),
            "self_preference_delta": round(float(np.mean(diffs)), 4),
            "self_preference_delta_ci95": _boot(diffs),
            "position_flip_rate_on_these_pairs": round(float(np.mean(flips)), 4),
        }
    lens = [(by_judge[g][i]["len_gptoss"], by_judge[g][i]["len_qwen"]) for i in common]
    out["mean_chars"] = {
        "gpt-oss-20b": round(float(np.mean([a for a, _ in lens])), 1),
        "qwen3.8-27b": round(float(np.mean([b for _, b in lens])), 1),
    }
    return out


def length_summary() -> dict[str, Any]:
    """(a) correlational: within each human-score bucket, Spearman(judge score, length) on TEST/DEV judgments;
    (b) controlled: neutral-verbosity perturbation of the same response (results/bias/length_perturb.jsonl)."""
    sample = {s["item_id"]: s for s in load_sample()}
    out: dict[str, Any] = {}
    final = _final_rubric()
    rows = _ok(read_jsonl(JUD / f"calib_{final}_test_reason_first.jsonl")) if final else []
    if rows:
        corr: dict[str, Any] = {}
        for c in CRITERIA:
            per_bucket = []
            for b in range(5):
                sub = [r for r in rows if sample[r["item_id"]]["human"][CRITERION_TO_HS2[c]] == b]
                if len(sub) >= 8:
                    rho = spearman([r["scores"][c] for r in sub], [len(sample[r["item_id"]]["response"]) for r in sub])
                    if rho == rho:
                        per_bucket.append((b, len(sub), round(rho, 4)))
            resid_j = [r["scores"][c] - sample[r["item_id"]]["human"][CRITERION_TO_HS2[c]] for r in rows]
            lens = [len(sample[r["item_id"]]["response"]) for r in rows]
            corr[c] = {
                "within_bucket_spearman": per_bucket,
                "n_weighted_mean_within_bucket_rho": round(
                    float(np.average([x[2] for x in per_bucket], weights=[x[1] for x in per_bucket])), 4
                )
                if per_bucket
                else None,
                "spearman_residual_vs_length": round(spearman(resid_j, lens), 4),
            }
        out["correlational_test_split"] = {"rubric": final, "n": len(rows), "criteria": corr}
    pert = [r for r in read_jsonl(RES / "bias" / "length_perturb.jsonl") if "scores_padded" in r]
    if pert:
        d: dict[str, Any] = {}
        for c in CRITERIA:
            diffs = [r["scores_padded"][c] - r["scores_original"][c] for r in pert]
            d[c] = {
                "mean_delta": round(float(np.mean(diffs)), 4),
                "ci95": _boot([float(x) for x in diffs]),
                "share_up": round(float(np.mean([x > 0 for x in diffs])), 4),
                "share_down": round(float(np.mean([x < 0 for x in diffs])), 4),
            }
        out["controlled_perturbation"] = {
            "n": len(pert),
            "mean_added_chars": round(float(np.mean([r["padded_chars"] - r["original_chars"] for r in pert])), 1),
            "criteria": d,
        }
    return out


def _final_rubric() -> str | None:
    cands = sorted(
        {
            FILE_RE.match(p.stem).group(1)
            for p in JUD.glob("calib_*_test_reason_first.jsonl")  # type: ignore[union-attr]
            if FILE_RE.match(p.stem)
        },
        key=lambda v: int(v[1:]),
    )
    return cands[-1] if cands else None


def order_effect() -> dict[str, Any] | None:
    """Reason-first vs score-first, same rubric, same DEV items, temperature 0.

    Reason-first rows come from the DEV run of the rubric; score-first rows from
    calib_<rubric>_dev_score_first.jsonl (run on results/order_experiment_ids.jsonl).
    """
    sample = {s["item_id"]: s for s in load_sample()}
    out: dict[str, Any] = {}
    for sf_path in sorted(JUD.glob("calib_v*_dev_score_first.jsonl")):
        rubric = sf_path.stem.split("_")[1]
        rf = {r["item_id"]: r for r in _ok(read_jsonl(JUD / f"calib_{rubric}_dev_reason_first.jsonl"))}
        sf = {r["item_id"]: r for r in _ok(read_jsonl(sf_path))}
        common = sorted(set(rf) & set(sf))
        if len(common) < 10:
            continue
        o: dict[str, Any] = {"n_items": len(common), "criteria": {}}
        for c in CRITERIA:
            h = [sample[i]["human"][CRITERION_TO_HS2[c]] for i in common]
            a = [rf[i]["scores"][c] for i in common]
            b = [sf[i]["scores"][c] for i in common]
            o["criteria"][c] = {
                "reason_first_qwk_vs_human": round(qwk(a, h, 4), 4),
                "score_first_qwk_vs_human": round(qwk(b, h, 4), 4),
                "reason_first_spearman_vs_human": round(spearman(a, h), 4),
                "score_first_spearman_vs_human": round(spearman(b, h), 4),
                "agreement_between_orders_qwk": round(qwk(a, b, 4), 4),
                "exact_same_score_share": round(float(np.mean([x == y for x, y in zip(a, b, strict=True)])), 4),
                "mean_reason_first": round(float(np.mean(a)), 3),
                "mean_score_first": round(float(np.mean(b)), 3),
            }
        o["mean_qwk_reason_first"] = round(
            float(np.mean([v["reason_first_qwk_vs_human"] for v in o["criteria"].values()])), 4
        )
        o["mean_qwk_score_first"] = round(
            float(np.mean([v["score_first_qwk_vs_human"] for v in o["criteria"].values()])), 4
        )
        out[rubric] = o
    return out or None


def bias() -> dict[str, Any]:
    out: dict[str, Any] = {"position": {}}
    for p in sorted((RES / "bias").glob("position_*.jsonl")):
        s = position_summary(p)
        if s:
            out["position"][p.stem.removeprefix("position_")] = s
    out["self_preference"] = selfpref_summary()
    out["length"] = length_summary()
    out["reason_vs_score_order"] = order_effect()
    (RES / "bias_summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("calibration", "all"):
        print(json.dumps({k: (v["mean_qwk"], v["n_items"]) for k, v in calibration()["runs"].items()}, indent=1))
    if cmd == "disagreements":
        disagreements(sys.argv[2])
    if cmd in ("bias", "all"):
        print(json.dumps(bias(), indent=1)[:4000])


if __name__ == "__main__":
    main()
