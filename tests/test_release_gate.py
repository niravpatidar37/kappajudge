"""Release gate wired into pytest + DeepEval.

Offline (always, no secrets): the committed run record must be fresh (hash of the current prompt file)
and must not regress vs the baseline (judgecal.gate). Each item's safety score is also asserted with
DeepEval (`assert_test` + RecordedScoreMetric): safety may not drop on any item.
Live (only when GROQ_API_KEY is set): the calibrated judge runs as a DeepEval metric.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from judgecal.deepeval_metric import CalibratedJudgeMetric, RecordedScoreMetric
from judgecal.gate import BASELINE_PATH, RECORD_PATH, check, load_config
from judgecal.sut import EVAL_SET_PATH, PROMPT_PATH, file_sha256, load_eval_set


def _load(p: Path) -> dict[str, Any] | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


RECORD = _load(RECORD_PATH)
BASELINE = _load(BASELINE_PATH)
pytestmark = pytest.mark.skipif(BASELINE is None, reason="no baseline committed yet")


def test_gate_evidence_fresh_and_no_regression() -> None:
    res = check(
        RECORD,
        BASELINE,
        prompt_sha256=file_sha256(PROMPT_PATH),
        eval_set_sha256=file_sha256(EVAL_SET_PATH),
        eval_ids=[str(i["question_id"]) for i in load_eval_set()],
        config=load_config(),
    )
    assert res.ok, "release gate failed:\n" + "\n".join(res.failures)


def _safety_cases() -> list[tuple[str, dict[str, Any], int]]:
    if RECORD is None or BASELINE is None:
        return []
    base = {it["question_id"]: it for it in BASELINE["items"]}
    return [
        (it["question_id"], it, base[it["question_id"]]["scores"]["safety"])
        for it in RECORD["items"]
        if it["question_id"] in base
    ]


@pytest.mark.parametrize(("qid", "item", "base_safety"), _safety_cases(), ids=lambda x: x if isinstance(x, str) else "")
def test_safety_never_drops_per_item(qid: str, item: dict[str, Any], base_safety: int) -> None:
    mx = int((RECORD or {})["max_scores"]["safety"])
    case = LLMTestCase(input=qid, actual_output=item["answer"], metadata={"scores": item["scores"]})
    assert_test(case, [RecordedScoreMetric("safety", mx, threshold=base_safety / mx)], run_async=False)


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="live judge needs GROQ_API_KEY")
def test_live_calibrated_judge_metric() -> None:
    from judgecal.evalrun import release_judge
    from judgecal.llm import LLMClient
    from judgecal.sut import format_contexts, reference_text

    item = next(i for i in load_eval_set() if i["category"] == "out_of_scope")
    judge = release_judge(LLMClient("groq", max_wait_s=600))
    case = LLMTestCase(
        input=item["question"],
        actual_output="I can't answer that from the provided filings.",
        expected_output=reference_text(item),
        retrieval_context=[format_contexts(item["contexts"])],
        metadata={"item_id": item["question_id"], "kind": "rag"},
    )
    assert_test(case, [CalibratedJudgeMetric(judge, "safety", threshold=1.0)], run_async=False)
