"""Dataset loaders and the stratified calibration sample.

HelpSteer2 (nvidia/HelpSteer2, CC-BY-4.0): human Likert 0-4 ratings per response. The released
label is the rounded mean of the 3 most-agreeing annotators; `disagreements/` has the individual
annotator ratings, which give the human inter-annotator ceiling.

MT-bench human judgments (lmsys/mt_bench_human_judgments, CC-BY-4.0): pairwise human verdicts.
"""

from __future__ import annotations

import gzip
import json
import random
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from judgecal.config import REPO_ROOT

RAW = REPO_ROOT / "data" / "raw"
HS2 = RAW / "nvidia__HelpSteer2"
MTB = RAW / "lmsys__mt_bench_human_judgments" / "data" / "human-00000-of-00001-25f4910818759289.parquet"
SAMPLE_PATH = REPO_ROOT / "data" / "calibration_sample.jsonl"
ANCHOR_POOL_PATH = REPO_ROOT / "data" / "anchor_pool.jsonl"
ATTRS = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")

# Mapping from rubric criterion -> HelpSteer2 human attribute (documented in README).
CRITERION_TO_HS2 = {"correctness": "correctness", "completeness": "helpfulness", "coherence": "coherence"}

MAX_PROMPT_CHARS = 600
MAX_RESPONSE_CHARS = 1200
MIN_RESPONSE_CHARS = 20


def _read_jsonl_gz(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


def helpsteer2_candidates() -> list[dict[str, Any]]:
    """Single-turn HelpSteer2 rows with >= 3 individual annotations and bounded length."""
    per_annot = {
        (d["prompt"], d["response"]): d for d in _read_jsonl_gz(HS2 / "disagreements" / "disagreements.jsonl.gz")
    }
    out: list[dict[str, Any]] = []
    for split in ("train", "validation"):
        for i, r in enumerate(_read_jsonl_gz(HS2 / f"{split}.jsonl.gz")):
            if "<extra_id_1>" in r["prompt"]:
                continue  # multi-turn
            if not (
                len(r["prompt"]) <= MAX_PROMPT_CHARS and MIN_RESPONSE_CHARS <= len(r["response"]) <= MAX_RESPONSE_CHARS
            ):
                continue
            d = per_annot.get((r["prompt"], r["response"]))
            if d is None or len(d["helpfulness"]) < 3:
                continue
            out.append(
                {
                    "item_id": f"hs2-{split}-{i:05d}",
                    "prompt": r["prompt"],
                    "response": r["response"],
                    "human": {a: int(r[a]) for a in ATTRS},
                    "annotators": {a: [int(x) for x in d[a]] for a in ATTRS},
                }
            )
    return out


def stratified_sample(cands: list[dict[str, Any]], per_level: int = 40, seed: int = 4) -> list[dict[str, Any]]:
    """40 items per helpfulness level 0-4, oversampling low-coherence items so that scale has variance.

    Within each level: up to 6 items with coherence <= 1, up to 6 with coherence == 2, rest random.
    Then a grouped DEV/TEST split by prompt (both responses to one prompt land in the same half),
    stratified by helpfulness level.
    """
    rng = random.Random(seed)
    by_level: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for c in cands:
        by_level[c["human"]["helpfulness"]].append(c)
    chosen: list[dict[str, Any]] = []
    used_prompts: set[str] = set()
    for level in range(5):
        pool = sorted(by_level[level], key=lambda x: x["item_id"])
        rng.shuffle(pool)
        picked: list[dict[str, Any]] = []
        for cond, cap in (
            (lambda x: x["human"]["coherence"] <= 1, 6),
            (lambda x: x["human"]["coherence"] == 2, 6),
            (lambda x: True, per_level),
        ):
            for x in pool:
                if len(picked) >= per_level or sum(1 for p in picked if cond(p)) >= cap:
                    break
                if cond(x) and x["prompt"] not in used_prompts and x not in picked:
                    picked.append(x)
                    used_prompts.add(x["prompt"])
        chosen.extend(picked)
    # grouped stratified split: alternate prompts within each level
    for level in range(5):
        items = [c for c in chosen if c["human"]["helpfulness"] == level]
        rng.shuffle(items)
        for j, c in enumerate(items):
            c["split"] = "dev" if j % 2 == 0 else "test"
    return chosen


def write_sample(items: list[dict[str, Any]], path: Path = SAMPLE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_sample(split: str | None = None) -> list[dict[str, Any]]:
    items = load_jsonl(SAMPLE_PATH)
    return [i for i in items if split is None or i["split"] == split]


def mtbench_pairs() -> list[dict[str, Any]]:
    """Turn-1 human pairwise verdicts with a non-tie winner, aggregated per (question, model pair)."""
    import pandas as pd

    df = pd.read_parquet(MTB)
    df = df[df["turn"] == 1]
    rows: list[dict[str, Any]] = []
    records = cast(list[dict[str, Any]], df.to_dict("records"))
    for r in records:
        conv_a: list[dict[str, Any]] = list(r["conversation_a"])
        conv_b: list[dict[str, Any]] = list(r["conversation_b"])
        rows.append(
            {
                "question_id": int(r["question_id"]),
                "model_a": r["model_a"],
                "model_b": r["model_b"],
                "winner": r["winner"],
                "judge": r["judge"],
                "question": conv_a[0]["content"],
                "answer_a": conv_a[1]["content"],
                "answer_b": conv_b[1]["content"],
            }
        )
    return rows
