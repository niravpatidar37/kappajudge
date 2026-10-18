# Demo recording

`demo.gif` is a real session on Windows 11 (git-bash), recorded on 2026-10-18 with `record.py`,
rendered with [agg](https://github.com/asciinema/agg) 1.5.0 and framed with `frame.py` (Pillow).
asciinema itself does not run on Windows, so `record.py` is a small pty-less recorder: it runs each
command through bash from the repo root, captures stdout and stderr as they arrive, and writes an
asciicast v2 file (`demo.cast`) with the real timestamps. It prints only the violet `#` comments and
the `$` prompt and types each command at a fixed pace; every command and all of its output are real.

What is real, what is cached:

| Step | Source | LLM calls |
|---|---|---|
| 1. `kappa_summary.py` | QWK recomputed live from the committed raw judgments (`results/judgments/calib_v1_test_reason_first.jsonl`) and HelpSteer2 per-annotator ratings (`data/calibration_sample.jsonl`); bias lines read from `results/bias_summary.json` | none |
| 2. `python -m judgecal.gate` | the release gate on main's prompt and committed record `eval_runs/latest.json` | none |
| 3. `git diff` | the demo PR's prompt change (branch `demo/weakened-prompt`) | none |
| 4. `judgecal.evalrun` | the PR's eval **replayed from the local content-hash LLM cache** (`data/cache/`, git-ignored, filled when the record was first made with real Groq calls). The kill switch `LLM_CALLS_ENABLED=false` was on, so any cache miss would have been refused. The shared spend ledger had 1077 lines before and after the recording: $0. The scores match the committed record on the demo branch. | none (cached) |
| 5. `python -m judgecal.gate --record .pr/latest.json` | the gate on the replayed record, against main's baseline: exits 1 with the two regression lines | none |

Cosmetic settings in `record.py`: the project venv is put first on `PATH` (so `python` is the
locked interpreter), git colour is forced (`color.ui=always`) because there is no tty, and `.pr/`
is deleted before and after the run. agg caps idle gaps at 2 s (`--idle-time-limit 2`), so the
~12 s numpy/scipy/sklearn import in step 1 is shortened on screen; nothing else is sped up.

## Re-record

Needs the local LLM cache (`data/cache/`, produced by `make eval` on the demo branch; without it,
step 4 is refused by the kill switch), the `demo/weakened-prompt` branch, `uv sync`, `agg` and the
JetBrains Mono TTFs (extracted by hand from the font archive that `assets/brand/tools/build_brand.py`
downloads and checksums):

```sh
python assets/demo/record.py assets/demo/demo.cast          # DEMO_BASH=/path/to/bash if needed
agg --theme 120e1a,f4f1fa,120e1a,f47067,3fb950,f0b429,58a6ff,b197fc,39c5cf,c9d1d9,5c566b,ff7b72,56d364,f5c451,79c0ff,d2a8ff,56d4dd,f4f1fa \
    --font-dir assets/brand/tools/.cache/fonts --font-family "JetBrains Mono" \
    --font-size 16 --idle-time-limit 2 --last-frame-duration 5 assets/demo/demo.cast assets/demo/raw.gif
uv run --no-project --with pillow python assets/demo/frame.py assets/demo/raw.gif assets/demo/demo.gif "kappajudge  —  real session"
```

The theme is the brand palette (Ink `120e1a` background, Snow `f4f1fa` text) with GitHub-dark ANSI
colours; the comments use xterm-256 colour 141 (violet). The window chrome is 24 px padding, a 44 px
title bar and 18 px rounded corners, transparent outside so the card sits on light and dark pages.
Result: 1104×762, 239 frames, 41.4 s, about 0.8 MB.
