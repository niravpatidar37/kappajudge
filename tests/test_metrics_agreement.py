from __future__ import annotations

import math

import pytest
from sklearn.metrics import cohen_kappa_score

from judgecal.agreement import judge_vs_labels, self_agreement
from judgecal.metrics import agreement_report, inter_annotator, qwk, spearman

MAX = {"correctness": 4, "safety": 2}


def test_qwk_matches_sklearn_and_bounds() -> None:
    a = [0, 1, 2, 3, 4, 4, 3, 2]
    b = [0, 2, 2, 3, 3, 4, 1, 2]
    assert qwk(a, b, 4) == pytest.approx(cohen_kappa_score(a, b, weights="quadratic"))
    assert qwk(a, a, 4) == pytest.approx(1.0)
    assert qwk(a, [4 - x for x in a], 4) < 0


def test_qwk_penalizes_far_disagreement_more() -> None:
    gold = [0, 1, 2, 3, 4] * 4
    near = [min(4, g + 1) for g in gold]
    far = [4 if g < 2 else 0 for g in gold]
    assert qwk(near, gold, 4) > qwk(far, gold, 4)


def test_spearman_nan_on_constant() -> None:
    assert math.isnan(spearman([1, 1, 1], [1, 2, 3]))


def test_agreement_report_ci_contains_point() -> None:
    j = [0, 1, 2, 3, 4] * 10
    h = [0, 1, 2, 4, 4] * 10
    rep = agreement_report(j, h, 4)
    lo, hi = rep["qwk_ci95"]
    assert lo <= rep["qwk"] <= hi


def test_inter_annotator_perfect_and_noisy() -> None:
    perfect = [[s, s, s] for s in [0, 1, 2, 3, 4] * 5]
    assert inter_annotator(perfect, 4)["pairwise_qwk_mean"] == pytest.approx(1.0)
    noisy = [[s, min(4, s + 1), max(0, s - 1)] for s in [0, 1, 2, 3, 4] * 5]
    r = inter_annotator(noisy, 4)
    assert 0 < r["pairwise_qwk_mean"] < 1 and 0 < r["leave_one_out_qwk"] <= 1


# ---- synthetic fixtures for the owner's (future) labels: the code is tested before the labels exist
def _labels() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    first = {f"p1-q{i:03d}": {"correctness": i % 5, "safety": i % 3} for i in range(30)}
    for item, s in first.items():
        rows.append({"item_id": item, "labeler_id": "owner", "pass_no": 1, "scores": s})
    for i, (item, s) in enumerate(list(first.items())[:20]):  # 20 double labels, 4 of them off by one
        s2 = dict(s)
        if i % 5 == 0:
            s2["correctness"] = min(4, s2["correctness"] + 1)
        rows.append({"item_id": item, "labeler_id": "owner", "pass_no": 2, "scores": s2})
    rows.append(
        {"item_id": "p1-q000", "labeler_id": "someone_else", "pass_no": 1, "scores": {"correctness": 0, "safety": 0}}
    )
    return rows


def test_self_agreement_on_synthetic_double_labels() -> None:
    sa = self_agreement(_labels(), MAX)
    assert sa["pairs"] == 20
    assert sa["criteria"]["safety"]["qwk"] == pytest.approx(1.0)
    assert 0.8 < sa["criteria"]["correctness"]["qwk"] < 1.0
    assert sa["criteria"]["correctness"]["exact"] == pytest.approx(16 / 20)


def test_judge_vs_labels_on_synthetic_fixture() -> None:
    labels = _labels()
    perfect = {f"p1-q{i:03d}": {"correctness": i % 5, "safety": i % 3} for i in range(30)}
    out = judge_vs_labels(labels, perfect, MAX, labeler_id="owner")
    assert out["items"] == 30
    assert out["criteria"]["correctness"]["qwk"] == pytest.approx(1.0)
    shifted = {k: {"correctness": min(4, v["correctness"] + 1), "safety": v["safety"]} for k, v in perfect.items()}
    worse = judge_vs_labels(labels, shifted, MAX, labeler_id="owner")
    assert worse["criteria"]["correctness"]["qwk"] < 1.0
    assert worse["criteria"]["safety"]["qwk"] == pytest.approx(1.0)


def test_no_labels_yields_empty_report() -> None:
    assert self_agreement([], MAX) == {"pairs": 0, "criteria": {}}
    assert judge_vs_labels([], {"x": {"safety": 1}}, MAX)["items"] == 0
