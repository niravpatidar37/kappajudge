from __future__ import annotations

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from judgecal.deepeval_metric import RecordedScoreMetric


def test_recorded_metric_passes_and_fails_through_deepeval() -> None:
    ok = LLMTestCase(input="q", actual_output="a", metadata={"scores": {"safety": 2}})
    assert_test(ok, [RecordedScoreMetric("safety", 2, threshold=1.0)], run_async=False)
    bad = LLMTestCase(input="q", actual_output="a", metadata={"scores": {"safety": 1}})
    with pytest.raises(AssertionError):
        assert_test(bad, [RecordedScoreMetric("safety", 2, threshold=1.0)], run_async=False)


def test_recorded_metric_rejects_missing_or_invalid_scores() -> None:
    m = RecordedScoreMetric("safety", 2, threshold=1.0)
    with pytest.raises(ValueError):
        m.measure(LLMTestCase(input="q", actual_output="a", metadata={"scores": {"safety": 7}}))
    with pytest.raises(ValueError):
        m.measure(LLMTestCase(input="q", actual_output="a", metadata={}))
