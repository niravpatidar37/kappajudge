"""Record the README demo as an asciicast v2 file, without a pty (works on Windows and Linux).

    python assets/demo/record.py assets/demo/demo.cast

Every command below really runs (through bash, from the repo root, with the project venv first on
PATH so `python` is the locked interpreter). stdout and stderr are captured as they arrive and
written with their real timestamps. The script itself only prints the violet `#` comments and the
`$` prompt, and types each command at a fixed pace.

No LLM is called: the summary is recomputed from committed raw files, the gate reads committed
evidence, and the PR's eval is replayed from the local content-hash cache (data/cache, git-ignored)
with the kill switch on (LLM_CALLS_ENABLED=false), so any uncached call would be refused.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLS, ROWS = 108, 30
NOTE, PROMPT, RESET = "\033[1;38;5;141m", "\033[1;32m", "\033[0m"

SESSION: list[tuple[str, str, float]] = [
    ("note", "1. how well does the LLM judge agree with human raters? (recomputed from raw files)", 0.8),
    ("run", "python assets/demo/kappa_summary.py", 3.0),
    ("note", "2. release gate on main's prompt + committed eval record (what CI runs, no LLM key)", 0.8),
    ("run", "python -m judgecal.gate", 2.0),
    ("note", "3. PR #1 weakens the RAG prompt", 0.6),
    ("run", "git diff main demo/weakened-prompt -- prompts/rag_answer.md | tail -n +6", 3.5),
    ("note", "4. replay that PR's eval from the content-hash cache: kill switch on, $0", 0.6),
    ("run", "mkdir -p .pr && git show demo/weakened-prompt:prompts/rag_answer.md > .pr/rag_answer.md", 0.3),
    (
        "run",
        "LLM_CALLS_ENABLED=false python -m judgecal.evalrun --prompt .pr/rag_answer.md --out .pr | sed -n 1,8p",
        2.0,
    ),
    ("note", "5. gate the PR's record against main's baseline", 0.6),
    ("run", 'python -m judgecal.gate --record .pr/latest.json --prompt .pr/rag_answer.md; echo "exit $?"', 4.0),
    ("note", "CI runs this gate on PR #1: release-gate fails, every other job passes.", 2.5),
]


class Cast:
    def __init__(self, path: Path) -> None:
        self.f = path.open("w", encoding="utf-8", newline="\n")
        self.t0 = time.monotonic()
        header = {
            "version": 2,
            "width": COLS,
            "height": ROWS,
            "timestamp": int(time.time()),
            "title": "kappajudge: kappa, bias and the release gate",
            "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        }
        self.f.write(json.dumps(header) + "\n")

    def out(self, data: str) -> None:
        data = data.replace("\r\n", "\n").replace("\n", "\r\n")  # no pty, so translate newlines here
        self.f.write(json.dumps([round(time.monotonic() - self.t0, 6), "o", data]) + "\n")
        sys.stdout.write(data)
        sys.stdout.flush()


def env() -> dict[str, str]:
    e = dict(os.environ)
    venv_bin = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    e["PATH"] = str(venv_bin) + os.pathsep + e["PATH"]
    e.update(
        PYTHONUNBUFFERED="1",
        PYTHONIOENCODING="utf-8",
        COLUMNS=str(COLS),
        GIT_CONFIG_PARAMETERS="'color.ui'='always'",  # coloured diff without a tty
        GIT_PAGER="cat",
    )
    return e


def run(cast: Cast, bash: str, cmd: str) -> None:
    cast.out(f"{PROMPT}${RESET} ")
    for ch in cmd:
        cast.out(ch)
        time.sleep(0.025)
    cast.out("\n")
    time.sleep(0.3)
    # Fixed argv (bash + a command from SESSION above), no untrusted input.
    proc = subprocess.Popen(  # noqa: S603  # nosec B603
        [bash, "-c", cmd], cwd=ROOT, env=env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read1(4096)  # type: ignore[attr-defined]
        if not chunk:
            break
        cast.out(chunk.decode("utf-8", errors="replace"))
    proc.wait()


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "assets/demo/demo.cast")
    bash = os.environ.get("DEMO_BASH") or shutil.which("bash")
    if not bash:
        sys.exit("bash not found (set DEMO_BASH)")
    shutil.rmtree(ROOT / ".pr", ignore_errors=True)
    cast = Cast(out)
    for kind, text, pause in SESSION:
        if kind == "note":
            cast.out(f"{NOTE}# {text}{RESET}\n")
        else:
            run(cast, bash, text)
        time.sleep(pause)
    cast.f.close()
    shutil.rmtree(ROOT / ".pr", ignore_errors=True)


if __name__ == "__main__":
    main()
