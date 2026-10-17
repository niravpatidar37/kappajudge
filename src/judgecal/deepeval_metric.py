"""DeepEval integration: the calibrated judge as DeepEval metrics.

- `CalibratedJudgeMetric`: a DeepEval `BaseMetric` that calls the calibrated rubric judge (live, needs
  GROQ_API_KEY) for one criterion on an `LLMTestCase` and passes if the score >= threshold.
- `RecordedScoreMetric`: replays a score that the same judge produced in a committed run record, so
  `deepeval`/pytest can gate offline in CI (no secrets) on the evidence file.
Scores are normalized to 0-1 (score / max) as DeepEval expects.
"""

from __future__ import annotations

from typing import Any

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from judgecal.judge import Judge, JudgeItem


class CalibratedJudgeMetric(BaseMetric):  # type: ignore[misc]
    async_mode = False

    def __init__(self, judge: Judge, criterion: str, threshold: float) -> None:
        self.judge = judge
        self.criterion = criterion
        self.threshold = threshold
        self.include_reason = True
        self.strict_mode = False
        self.evaluation_model = judge.model

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        meta = test_case.metadata or {}
        item = JudgeItem(
            item_id=str(meta.get("item_id", test_case.input[:40])),
            task=test_case.input,
            response=test_case.actual_output or "",
            reference=test_case.expected_output,
            context="\n\n".join(str(c) for c in (test_case.retrieval_context or [])) or None,
            kind=str(meta.get("kind", "rag")),
        )
        res = self.judge.score(item, [self.criterion])
        mx = self.judge.rubric.criterion(self.criterion).max_score
        self.score = res.scores[self.criterion] / mx
        self.reason = res.reasoning[self.criterion]
        self.success = self.score >= self.threshold
        return float(self.score)

    async def a_measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return f"CalibratedJudge[{self.criterion}]"


class RecordedScoreMetric(BaseMetric):  # type: ignore[misc]
    """Replays the judge score stored in the run record (test_case.metadata['scores'])."""

    async_mode = False

    def __init__(self, criterion: str, max_score: int, threshold: float) -> None:
        self.criterion = criterion
        self.max_score = max_score
        self.threshold = threshold
        self.include_reason = False
        self.strict_mode = False

    def measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        scores = (test_case.metadata or {}).get("scores", {})
        raw = scores.get(self.criterion)
        if not isinstance(raw, int) or not 0 <= raw <= self.max_score:
            raise ValueError(f"record has no valid {self.criterion} score")
        self.score = raw / self.max_score
        self.success = self.score >= self.threshold
        self.reason = f"recorded {self.criterion}={raw}/{self.max_score}"
        return float(self.score)

    async def a_measure(self, test_case: LLMTestCase, *args: Any, **kwargs: Any) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return f"Recorded[{self.criterion}]"
