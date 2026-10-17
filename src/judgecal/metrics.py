"""Agreement metrics: quadratic-weighted Cohen's kappa, Spearman, exact/adjacent agreement, bootstrap CIs."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from itertools import combinations
from typing import Any

import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import cohen_kappa_score


def qwk(a: Sequence[int], b: Sequence[int], max_score: int) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("qwk needs two equal-length non-empty sequences")
    if len(set(a)) == 1 and len(set(b)) == 1:
        return 1.0 if list(a) == list(b) else 0.0
    return float(cohen_kappa_score(list(a), list(b), weights="quadratic", labels=list(range(max_score + 1))))


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    if len(set(a)) < 2 or len(set(b)) < 2:
        return float("nan")
    rho = spearmanr(list(a), list(b)).statistic
    return float(rho)


def exact(a: Sequence[int], b: Sequence[int]) -> float:
    return float(np.mean([x == y for x, y in zip(a, b, strict=True)]))


def within_one(a: Sequence[int], b: Sequence[int]) -> float:
    return float(np.mean([abs(x - y) <= 1 for x, y in zip(a, b, strict=True)]))


def bootstrap_ci(
    a: Sequence[int], b: Sequence[int], max_score: int, n: int = 1000, seed: int = 0, alpha: float = 0.05
) -> tuple[float, float]:
    rng = random.Random(seed)  # nosec B311 - seeded bootstrap, not crypto
    idx = list(range(len(a)))
    vals: list[float] = []
    for _ in range(n):
        s = [rng.choice(idx) for _ in idx]
        k = qwk([a[i] for i in s], [b[i] for i in s], max_score)
        if not math.isnan(k):
            vals.append(k)
    vals.sort()
    lo = vals[int(alpha / 2 * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return float(lo), float(hi)


def agreement_report(judge: Sequence[int], human: Sequence[int], max_score: int, ci: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "n": len(judge),
        "qwk": round(qwk(judge, human, max_score), 4),
        "spearman": round(spearman(judge, human), 4),
        "exact": round(exact(judge, human), 4),
        "within_1": round(within_one(judge, human), 4),
        "mean_judge": round(float(np.mean(judge)), 3),
        "mean_human": round(float(np.mean(human)), 3),
    }
    if ci:
        lo, hi = bootstrap_ci(judge, human, max_score)
        out["qwk_ci95"] = [round(lo, 4), round(hi, 4)]
    return out


def inter_annotator(annotations: Sequence[Sequence[int]], max_score: int) -> dict[str, Any]:
    """Human ceiling from per-item annotator lists (each item has >= 2 ratings, annotator order arbitrary).

    pairwise: mean QWK over annotator slot pairs (slot i vs slot j across items having both).
    leave_one_out: every rating vs the rounded mean of the other ratings for the same item, pooled.
    """
    pair_vals: list[float] = []
    for i, j in combinations(range(3), 2):
        xs = [a[i] for a in annotations if len(a) > max(i, j)]
        ys = [a[j] for a in annotations if len(a) > max(i, j)]
        if len(xs) >= 10:
            pair_vals.append(qwk(xs, ys, max_score))
    one: list[int] = []
    rest: list[int] = []
    for a in annotations:
        for k in range(len(a)):
            others = [a[m] for m in range(len(a)) if m != k]
            one.append(a[k])
            rest.append(math.floor(float(np.mean(others)) + 0.5))
    return {
        "items": len(annotations),
        "pairwise_qwk_mean": round(float(np.mean(pair_vals)), 4) if pair_vals else float("nan"),
        "pairwise_qwk_each": [round(v, 4) for v in pair_vals],
        "leave_one_out_qwk": round(qwk(one, rest, max_score), 4),
        "leave_one_out_spearman": round(spearman(one, rest), 4),
    }
