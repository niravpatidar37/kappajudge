"""Rubric definitions (versioned JSON files in rubrics/) and their rendering into judge prompts.

Every criterion is an ordinal scale with every point defined in words. Anchors are short worked
examples attached to (criterion, score) pairs; they are drawn only from data outside the
calibration DEV/TEST sample (see scripts/build_anchors.py) to avoid leakage.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from judgecal.config import REPO_ROOT

RUBRIC_DIR = REPO_ROOT / "rubrics"


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z_]{3,30}$")
    question: str
    scale: dict[int, str]  # score -> definition in words
    applies_to: list[str] = Field(default_factory=lambda: ["general", "rag"])
    human_attribute: str | None = None  # HelpSteer2 attribute this criterion is calibrated against

    @model_validator(mode="after")
    def _contiguous(self) -> Criterion:
        keys = sorted(self.scale)
        if keys != list(range(len(keys))) or len(keys) not in (3, 5):
            raise ValueError(f"{self.name}: scale must be 0..2 or 0..4, every point defined")
        if any(len(v.strip()) < 10 for v in self.scale.values()):
            raise ValueError(f"{self.name}: every scale point needs a definition in words")
        return self

    @property
    def max_score(self) -> int:
        return max(self.scale)


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str
    score: int
    example: str = Field(max_length=700)
    why: str = Field(max_length=300)


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    notes: str = ""
    instructions: list[str] = Field(default_factory=list)
    criteria: list[Criterion]
    anchors: list[Anchor] = Field(default_factory=list)

    @model_validator(mode="after")
    def _anchors_valid(self) -> Rubric:
        by_name = {c.name: c for c in self.criteria}
        for a in self.anchors:
            if a.criterion not in by_name or a.score not in by_name[a.criterion].scale:
                raise ValueError(f"anchor refers to unknown criterion/score {a.criterion}={a.score}")
        return self

    def criterion(self, name: str) -> Criterion:
        for c in self.criteria:
            if c.name == name:
                return c
        raise KeyError(name)

    def names(self, kind: str | None = None) -> list[str]:
        return [c.name for c in self.criteria if kind is None or kind in c.applies_to]

    def sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()

    def render(self, criteria: list[str], with_anchors: bool = True) -> str:
        lines: list[str] = []
        for name in criteria:
            c = self.criterion(name)
            lines.append(f"### {c.name} (integer {min(c.scale)}-{c.max_score})")
            lines.append(c.question)
            for score in sorted(c.scale, reverse=True):
                lines.append(f"- {score}: {c.scale[score]}")
            anchors = [a for a in self.anchors if a.criterion == name] if with_anchors else []
            if anchors:
                lines.append("Anchor examples:")
                for a in sorted(anchors, key=lambda x: -x.score):
                    lines.append(f"  - [{a.score}] {a.example} -> {a.why}")
            lines.append("")
        return "\n".join(lines).strip()


def load_rubric(version: str, rubric_dir: Path | None = None) -> Rubric:
    path = (rubric_dir or RUBRIC_DIR) / f"rubric_{version}.json"
    return Rubric.model_validate(json.loads(path.read_text(encoding="utf-8")))
