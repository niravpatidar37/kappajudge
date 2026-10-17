"""Agreement against the owner's own labels (Postgres `labels` table).

- self_agreement: pass 1 vs blind pass 2 by the same labeler on the same item (the human ceiling).
- judge_vs_labels: judge scores vs the labeler's pass-1 scores, per criterion.
Both return QWK + Spearman + exact agreement per criterion and are unit-tested on synthetic fixtures.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from judgecal.metrics import agreement_report


def self_agreement(labels: list[dict[str, Any]], max_scores: dict[str, int], min_n: int = 2) -> dict[str, Any]:
    passes: dict[tuple[str, str], dict[int, dict[str, int]]] = defaultdict(dict)
    for lab in labels:
        passes[(lab["labeler_id"], lab["item_id"])][int(lab["pass_no"])] = dict(lab["scores"])
    pairs = [(p[1], p[2]) for p in passes.values() if 1 in p and 2 in p]
    out: dict[str, Any] = {"pairs": len(pairs), "criteria": {}}
    for crit, mx in max_scores.items():
        a = [x[crit] for x, y in pairs if crit in x and crit in y]
        b = [y[crit] for x, y in pairs if crit in x and crit in y]
        if len(a) >= min_n:
            out["criteria"][crit] = agreement_report(a, b, mx, ci=len(a) >= 10)
    return out


def judge_vs_labels(
    labels: list[dict[str, Any]],
    judge_scores: dict[str, dict[str, int]],
    max_scores: dict[str, int],
    labeler_id: str | None = None,
    min_n: int = 2,
) -> dict[str, Any]:
    first = {
        lab["item_id"]: dict(lab["scores"])
        for lab in labels
        if int(lab["pass_no"]) == 1 and (labeler_id is None or lab["labeler_id"] == labeler_id)
    }
    common = sorted(set(first) & set(judge_scores))
    out: dict[str, Any] = {"items": len(common), "criteria": {}}
    for crit, mx in max_scores.items():
        ids = [i for i in common if crit in first[i] and crit in judge_scores[i]]
        if len(ids) >= min_n:
            out["criteria"][crit] = agreement_report(
                [judge_scores[i][crit] for i in ids], [first[i][crit] for i in ids], mx, ci=len(ids) >= 10
            )
    return out
