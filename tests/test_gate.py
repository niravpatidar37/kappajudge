from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from judgecal.gate import build_record, canonical_sha256, check
from judgecal.sut import file_sha256

MAX = {"correctness": 4, "completeness": 4, "coherence": 4, "groundedness": 4, "safety": 2}
CFG = {
    "max_drop_normalized": {
        "correctness": 0.05,
        "completeness": 0.05,
        "coherence": 0.05,
        "groundedness": 0.05,
        "safety": 0.0,
    }
}
JUDGE = {"model": "qwen/qwen3.8-27b", "rubric_version": "v3", "order": "reason_first"}
IDS = [f"q{i:03d}" for i in range(10)]


def _items(**override: int) -> list[dict[str, Any]]:
    out = []
    for i in IDS:
        s = {"correctness": 4, "completeness": 4, "coherence": 4, "groundedness": 4, "safety": 2}
        out.append({"question_id": i, "category": "x", "answer": "a", "scores": s})
    for k, v in override.items():  # lower one item's score
        out[0]["scores"][k] = v
    return out


def _rec(items: list[dict[str, Any]], prompt: str = "p" * 64, judge: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_record(
        items=items,
        prompt_sha256=prompt,
        eval_set_sha256="e" * 64,
        judge_config=judge or JUDGE,
        sut={"model": "openai/gpt-oss-20b"},
        max_scores=MAX,
        usd=0.01,
    )


def _check(rec: dict[str, Any] | None, base: dict[str, Any] | None, prompt: str = "p" * 64) -> Any:
    return check(rec, base, prompt_sha256=prompt, eval_set_sha256="e" * 64, eval_ids=IDS, config=CFG)


BASE = _rec(_items())


def test_identical_run_passes() -> None:
    res = _check(_rec(_items()), BASE)
    assert res.ok, res.failures


def test_missing_record_or_baseline_fails() -> None:
    assert not _check(None, BASE).ok
    assert not _check(_rec(_items()), None).ok


def test_stale_prompt_hash_fails() -> None:
    res = _check(_rec(_items()), BASE, prompt="q" * 64)
    assert not res.ok and any("stale evidence" in f for f in res.failures)


def test_quality_regression_beyond_threshold_fails() -> None:
    # one item correctness 4 -> 1 over 10 items: normalized drop 0.075 > 0.05
    res = _check(_rec(_items(correctness=1)), BASE)
    assert not res.ok and any("regression: correctness" in f for f in res.failures)


def test_small_quality_drop_within_threshold_passes() -> None:
    res = _check(_rec(_items(correctness=3)), BASE)  # drop 0.025
    assert res.ok, res.failures


def test_any_safety_drop_fails() -> None:
    res = _check(_rec(_items(safety=1)), BASE)
    assert not res.ok and any("regression: safety" in f for f in res.failures)


def test_edited_scores_without_rehash_fail() -> None:
    rec = _rec(_items())
    forged = copy.deepcopy(rec)
    forged["items"][0]["scores"]["safety"] = 2
    forged["summary"]["correctness"]["normalized"] = 1.0
    forged["items"][1]["scores"]["correctness"] = 9
    res = _check(forged, BASE)
    assert not res.ok
    assert any("record_sha256" in f for f in res.failures)
    assert any("invalid score" in f for f in res.failures)


def test_summary_must_match_items() -> None:
    rec = _rec(_items(correctness=0))
    rec["summary"]["correctness"]["normalized"] = 1.0
    rec["record_sha256"] = canonical_sha256({k: v for k, v in rec.items() if k != "record_sha256"})
    res = _check(rec, BASE)
    assert any("summary does not match" in f for f in res.failures)


def test_dropping_items_from_record_fails() -> None:
    items = _items()[:-1]
    res = _check(_rec(items), BASE)
    assert any("ids differ" in f for f in res.failures)


def test_judge_change_requires_rebaseline() -> None:
    res = _check(_rec(_items(), judge={**JUDGE, "rubric_version": "v1"}), BASE)
    assert any("judge config differs" in f for f in res.failures)


def test_prompt_hash_ignores_line_endings(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_bytes(b"line1\nline2\n")
    b.write_bytes(b"line1\r\nline2\r\n")
    assert file_sha256(a) == file_sha256(b)
    b.write_bytes(b"line1\r\nline2 changed\r\n")
    assert file_sha256(a) != file_sha256(b)


@pytest.mark.parametrize("path", ["eval_runs/latest.json", "eval_runs/baseline.json"])
def test_committed_evidence_is_present(path: str) -> None:
    if not Path("eval_runs/baseline.json").is_file():
        pytest.skip("baseline not produced yet")
    assert Path(path).is_file()
