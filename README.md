# llm-judge-calibration

An LLM judge (qwen3.8-27b, rubric v1) for Project 1's RAG answers, calibrated against real human
ratings: **mean quadratic-weighted kappa 0.49 vs HelpSteer2 annotators on a held-out TEST
split (n = 40)**, with position and length bias measured (self-preference only partly: see the
real n per table) and an evidence-based CI release gate that rejects a prompt regression.

Every number below is rendered by `scripts/render_readme.py` from committed raw files in `results/`
and `eval_runs/`. Experiments ran on the Groq free tier (about 200K tokens/day per model, shared with
other projects), so several sample sizes are smaller than planned; each table states the real n.

## Results

### 1. Judge vs human agreement (TEST split, never used for tuning)

Human gold standard: [nvidia/HelpSteer2](https://huggingface.co/datasets/nvidia/HelpSteer2) (CC-BY-4.0),
200 single-turn items stratified 40 per human helpfulness level 0-4 (including broken responses),
split by prompt into DEV 100 / TEST 100. The judge's TEST score is computed on the stratified TEST
subset that the quota allowed (8 per level planned; items are processed round-robin by level).
The human ceiling uses HelpSteer2's per-annotator ratings (`disagreements/`) on all 100 TEST items:
"pairwise" is annotator slot vs slot; "leave-one-out" is each annotator vs the rounded mean of the others.

| Criterion (HelpSteer2 attribute) | n | Judge QWK [95% CI] | Spearman | Exact | Within 1 | Human pairwise QWK | Human leave-one-out QWK |
|---|---:|---:|---:|---:|---:|---:|---:|
| correctness (correctness) | 40 | **0.63** [0.41, 0.79] | 0.64 | 0.40 | 0.75 | 0.62 | 0.69 |
| completeness (helpfulness) | 40 | **0.47** [0.17, 0.71] | 0.49 | 0.38 | 0.78 | 0.60 | 0.67 |
| coherence (coherence) | 40 | **0.37** [-0.04, 0.65] | 0.30 | 0.45 | 0.80 | 0.37 | 0.44 |

Raw: `results/judgments/calib_v1_test_reason_first.jsonl`, summary `results/calibration_summary.json`.

### 2. Rubric iterations (tuned on DEV only)

![iteration curve](results/iteration_curve.png)

| Rubric | Split | n | correctness QWK | completeness QWK | coherence QWK | mean |
|---|---|---:|---:|---:|---:|---:|
| v1 | DEV (all scored) | 100 | 0.42 | 0.40 | 0.60 | 0.48 |
| v1 | DEV subset (6 per level) | 30 | 0.36 | 0.20 | 0.58 | 0.38 |
| v1 | TEST | 40 | 0.63 | 0.47 | 0.37 | 0.49 |
| v2 | DEV (all scored) | 72 | 0.35 | 0.27 | 0.52 | 0.38 |
| v2 | DEV subset (6 per level) | 30 | 0.31 | 0.22 | 0.45 | 0.33 |

v1 -> v2 is documented in `rubrics/CHANGELOG.md` (patterns found in the 20 largest DEV disagreements,
`results/disagreements_v1_dev.md`). The DEV-subset rows compare both rubrics on the same items
(6 per human level). The final rubric is picked by the pre-declared rule "higher mean DEV-subset QWK".

### 3. Bias measurements

**Position bias** (pairwise, each pair judged in both orders; flip = verdict changes when the order is
swapped). MT-bench pairs come from [lmsys/mt_bench_human_judgments](https://huggingface.co/datasets/lmsys/mt_bench_human_judgments)
(turn 1, human majority verdict non-tie, both answers <= 1200 chars):

| Judge | Pairs scored in both orders (planned) | Flip rate [95% CI] | First-slot win share | Agreement with human (per judgment) | Longer answer wins (consistent verdicts) |
|---|---:|---:|---:|---:|---:|
| openai/gpt-oss-20b (MT-bench) | 27 of 40 | **11%** [0%, 26%] | 53% | 91% | 74% |
| openai/gpt-oss-20b (self-preference pairs) | 6 of 10 | 17% | | | |

openai/gpt-oss-20b: n = 27 of 40 planned pairs; 12 pairs excluded (every attempt so far ended in 3 provider `json_validate_failed` rejections; retried on resume), 1 not attempted (daily quota). Excluding failed pairs can bias the rates if failures correlate with hard pairs. Resume: `uv run python scripts/bias_position.py --model openai/gpt-oss-20b --n 40`.

**Self-preference** (both models answer the same MT-bench prompts; each model judges every pair in
both orders; delta = how much more a judge prefers its own family's answer than the other judge does
on the same pairs; ties count 0.5):

| Judge | Pairs scored (planned) | Pairs failed | Own family win rate (judged by itself) | Same answers judged by the other model | Self-preference delta [95% CI] |
|---|---:|---:|---:|---:|---:|
| openai/gpt-oss-20b | 6 of 10 | 4 | 58% | not yet measured | not yet measured |
| qwen/qwen3.8-27b | 0 of 10 | 0 | not yet measured | not yet measured | not yet measured |

Pairs judged by both judges: n = 0. The self-preference delta needs both judges on the same pairs, so it is not yet measured; the own-family win rate alone is not evidence of self-preference (the other family's answers may simply be worse). Resume: `uv run python scripts/bias_selfpref.py --stage judge --n 10` (qwen judge pending; failed pairs are retried on resume).

**Length bias:**

Correlational (TEST, rubric v1, n = 40): Spearman of judge score vs response length inside each human score bucket, and of (judge - human) vs length:

| Criterion | n-weighted within-bucket rho | rho(judge - human, length) |
|---|---:|---:|
| correctness | -0.38 | -0.41 |
| completeness | -0.02 | -0.03 |
| coherence | 0.19 | 0.09 |

Controlled (n = 10 TEST items, 2 per human level, reduced from a planned 30 for quota; +234 chars of neutral preamble/closing, content unchanged): mean score change padded - original:

| Criterion | Mean delta [95% CI] | Share up | Share down |
|---|---:|---:|---:|
| correctness | **+0.00** [-0.30, +0.30] | 10% | 10% |
| completeness | **-0.10** [-0.50, +0.30] | 20% | 30% |
| coherence | **-0.10** [-0.30, +0.00] | 0% | 10% |

**Reason-first vs score-first** (same rubric and items, only the JSON field order changes):

Rubric v1, n = 15 DEV items (planned 15), temperature 0:

| Criterion | Reason-first QWK | Score-first QWK | Agreement between orders (QWK) | Same score |
|---|---:|---:|---:|---:|
| correctness | 0.49 | 0.30 | 0.85 | 93% |
| completeness | 0.32 | 0.25 | 0.98 | 87% |
| coherence | 0.66 | 0.66 | 1.00 | 100% |

### 4. Release gate

The system under test is the RAG answer prompt `prompts/rag_answer.md` (gpt-oss-20b) on a fixed
8-item eval set built from Project 1's cached retrieval contexts (`data/p1_eval_set.json`: 7 benchmark
questions across single/multi-hop, aggregation and out-of-scope, plus 1 synthetic prompt-injection item).
The calibrated judge scores 5 criteria (correctness, completeness, coherence, groundedness, safety).

| Criterion | Baseline mean (normalized) | Allowed drop |
|---|---:|---:|
| correctness | 2.50 / 4 (0.62) | 0.05 |
| completeness | 1.50 / 4 (0.38) | 0.05 |
| coherence | 4.00 / 4 (1.00) | 0.05 |
| groundedness | 3.00 / 4 (0.75) | 0.05 |
| safety | 1.62 / 2 (0.81) | 0.00 |

Baseline run `c3467568`, 8 items, $0.0152.

Demo: a PR that weakens the prompt (drops citations, refusal and injection rules) with its honest eval
record. Link and CI result: not yet measured

### 5. Labeling UI (human labels: TODO)

![labeling UI](results/ui_labeling.png)

Streamlit app on Postgres: input, output, reference, retrieved context and the rubric with every point
defined; a score per criterion plus a note; labeler id and server timestamp; a blind double-label mode
(only items first labeled >= 20 h earlier, max 20) for self-agreement; an Agreement page (QWK of the
judge vs the owner's labels, and pass-1 vs pass-2 self-agreement) and an Eval-trend page.
The queue is pre-loaded with all 128 Project 1 answers (64 questions x hybrid/vector) and the 200
HelpSteer2 items.

- **TODO (human): 200 labels + 20 double labels by the owner.** Nothing in this repo is a human label
  produced by the build agent. The agreement code is tested on synthetic fixtures
  (`tests/test_metrics_agreement.py`) and runs with `uv run judgecal agreement` once labels exist.

## Architecture

```mermaid
flowchart LR
  subgraph data["Data (committed)"]
    HS2["HelpSteer2 200 items<br/>DEV 100 / TEST 100<br/>+ per-annotator ratings"]
    MTB["MT-bench human<br/>pairwise verdicts"]
    P1["Project 1: 128 answers<br/>+ cached retrieval contexts"]
  end
  subgraph judge["Judge (src/judgecal)"]
    R["rubrics/rubric_vN.json<br/>every point defined<br/>+ anchors (disjoint pool)"]
    J["Judge: reasoning-first JSON schema<br/>Pydantic validation + retry<br/>untrusted text fenced"]
    L["LLM client: Groq<br/>spend cap, content-hash cache,<br/>429 backoff, daily-quota fail-fast,<br/>kill switch"]
    R --> J --> L
  end
  HS2 --> J --> CAL["calibration: QWK, Spearman,<br/>human ceiling, iteration curve"]
  MTB --> PJ["pairwise judge<br/>both orders"] --> BIAS["position / self-preference /<br/>length bias"]
  P1 --> SUT["SUT: prompts/rag_answer.md<br/>(gpt-oss-20b)"] --> J
  J --> REC["eval_runs/latest.json<br/>prompt SHA-256, judge config,<br/>scores, record hash"]
  REC --> CI{"CI release gate<br/>(no secrets): fresh hash +<br/>no regression vs baseline<br/>gate code from BASE commit"}
  REC --> PG[("Postgres<br/>eval_runs, labels,<br/>label_items")]
  PG --> UI["Streamlit: label, double-label,<br/>agreement, trend"]
```

Deployment shape (deploy-ready; not applied): Cloud Run service (Streamlit behind IAP) + Cloud Run
Jobs (migrate, scheduled eval via Cloud Scheduler) + Cloud SQL Postgres (private IP) + Secret Manager.

## Design decisions (trade-offs)

1. **Public human labels instead of agent labels.** The build agent is not a human, so the gold standard
   is HelpSteer2's annotators. Cost: HelpSteer2 rates helpfulness/correctness/coherence of general chat
   answers, not RAG groundedness or refusal safety. Three rubric criteria map to HelpSteer2 attributes
   (correctness -> correctness, completeness -> helpfulness, coherence -> coherence); groundedness and
   safety stay uncalibrated until the owner labels Project 1 answers in the UI.
2. **Evidence-based gate instead of LLM calls in CI.** CI holds no LLM secret. `make eval` writes a run
   record pinned to the prompt file's SHA-256 and the judge config; CI fails on a stale or missing record
   or a regression beyond thresholds (no criterion may drop more than 0.05 of its scale; safety may not
   drop at all), loading the gate code, thresholds, baseline and eval set from the PR's base commit.
   Residual risk: a developer can forge a record. The `live-gate` job re-runs the eval with real models
   when a `GROQ_API_KEY` secret exists, which closes that gap.
3. **Quota-shaped sample sizes.** The free tier allows ~200K tokens/day per model and a judge call costs
   0.9-2.8K tokens. Every script is resumable (content-hash cache, checkpointed rows, round-robin by
   stratum) and fails fast on a daily-quota 429 with the exact resume command.

## What did not work

- **Rubric v2 did not beat v1 on DEV** (see the iteration table). Two v2 rules backfired when reading the
  per-item changes: "work out arithmetic yourself" made the judge confidently re-derive a wrong total and
  call a wrong answer correct (`hs2-train-15036`, correctness 1 -> 4, humans 0), and the exception
  "underspecified request -> a clarifying question is complete" was over-applied (`hs2-train-01124`,
  completeness 1 -> 4, humans 0). Stricter wording also lowered the judge's mean on low-quality items
  without improving rank agreement.
- **Coherence is noisy for humans too**: HelpSteer2 annotators agree with each other at pairwise QWK
  well below their agreement on correctness/helpfulness (see the ceiling columns), so coherence kappa
  has a low ceiling.
- **Quota**: gpt-oss-20b's daily quota was shared with another project and exhausted early, which cut
  the position-bias and self-preference sample sizes; qwen refilled at roughly one judge call every
  10-15 minutes after the first burst.
- gpt-oss-20b often failed Groq's server-side JSON-schema validation (`json_validate_failed`) on the
  pairwise schema; those items are retried and, if they fail 3 times, recorded as errors (excluded).

## Security

Threat model: model outputs (judge and SUT) and all dataset text are untrusted; labelers' notes are
sensitive; a PR author may try to weaken the release gate.

| Control | Where | Test |
|---|---|---|
| Judge output parsed as JSON only, validated by a per-criterion Pydantic model (`extra="forbid"`, scores bounded by the scale), retried with the error | `judge.py` | `test_scores_outside_scale_are_rejected_then_retried`, `test_extra_keys_rejected` |
| Untrusted task/response/context fenced; look-alike tags neutralized so text cannot close the fence | `judge.neutralize`, `sut.format_contexts` | `test_untrusted_response_cannot_close_the_fence`, `test_sut_messages_fence_untrusted_context` |
| Hard spend cap checked before every paid call (global $25, project $5; corrupt ledger fails closed) | `spend.py`, `llm.py` | `test_budget_checked_before_any_http`, `test_corrupt_ledger_fails_closed` |
| Kill switch `LLM_CALLS_ENABLED=false` refuses every uncached call | `llm.py` | `test_kill_switch_refuses_uncached_calls` |
| Daily-quota fail-fast (no long blind retries) | `llm.py`, `runs.py` | `test_daily_quota_fails_fast_without_sleeping` |
| Gate tamper checks: prompt hash, judge config hash, record hash, summary recomputed, id coverage, safety per item | `gate.py`, `tests/test_release_gate.py` | `tests/test_gate.py` |
| Labels: labeler id regex + score bounds in code and DB CHECK constraints; bound SQL parameters only | `db.py`, `migrations/` | `tests/test_db_integration.py` |
| Untrusted text rendered with `st.text`/`st.code` only (no Markdown/HTML) | `webapp/main.py` | review |
| PII and keys redacted from JSON logs | `logjson.py` | `test_redacts_pii_and_keys` |
| UI behind IAP; UI service account cannot call models or read the Groq key | `infra/terraform` | checkov, tflint |

Residual risk: a forged eval record passes the evidence gate (closed only by the live gate); prompt
injection inside judged text can still bias a score (fencing is defence in depth); same-repo PR code
runs on CI runners.

OWASP Top 10 for LLM Applications (2025):

| Risk | Status |
|---|---|
| LLM01 Prompt injection | Fenced untrusted text; injection eval item with safety criterion; residual risk above |
| LLM02 Sensitive information disclosure | Labels in private Cloud SQL behind IAP; PII redaction in logs; no secrets in repo/images/CI |
| LLM03 Supply chain | `uv.lock`, pip-audit, Trivy, digest-pinned images, SHA-pinned actions |
| LLM04 Data and model poisoning | Public datasets pinned by revision + SHA-256 (`data/datasets_manifest.json`) |
| LLM05 Improper output handling | Outputs validated by Pydantic, never executed or rendered as HTML |
| LLM06 Excessive agency | No tools; the judge only returns scores |
| LLM07 System prompt leakage | Prompts hold no secrets; the injection item tests leakage |
| LLM08 Vector and embedding weaknesses | Not applicable (no retrieval here; P1 contexts are cached text) |
| LLM09 Misinformation | This repo measures it: calibrated judge, human ceiling, bias numbers |
| LLM10 Unbounded consumption | Spend cap, kill switch, fail-fast on quota, bounded retries/timeouts |

## Cost

LLM spend for this project: **$0.4560** across 490 paid calls (qwen3.8-27b $0.4035, gpt-oss-20b $0.0525), read from the shared ledger at 2026-10-18T15:19:04Z. Groq calls are priced at Groq's published paid rates
(`src/judgecal/pricing.json`, an estimate to verify) although the key is free tier. Monthly cloud cost
estimate: `docs/SLO.md`.

## Deployment

Status: **deploy-ready; not applied (no cloud account connected).** Terraform in `infra/terraform`
(module + `envs/staging` / `envs/prod`): Cloud Run v2 service with IAP, autoscaling, probes on
`/readyz` (startup) and `/healthz` (liveness), session affinity; Cloud Run Jobs for migrations and the
scheduled eval (Cloud Scheduler); Cloud SQL Postgres 16 (private IP, TLS, PITR); Secret Manager
(values never in Terraform state); Artifact Registry; dedicated least-privilege service accounts.
CD: `.github/workflows/deploy.yml` (build -> push digest -> staging canary + smoke -> prod after manual
approval, 10% canary, automatic rollback), skipped until the repo variables exist.

- One-time bootstrap (WIF pool, deploy SA, repo variables `GCP_PROJECT_ID`, `GCP_REGION`,
  `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`, `GCP_IAP_CLIENT_ID`): `docs/RUNBOOK.md`
- Runbook (deploy, verify, rollback, rotate secrets, kill switch, incidents): `docs/RUNBOOK.md`
- SLOs and monthly cost estimate: `docs/SLO.md`

Demo video: TODO (human): record 90 s. Shot list: (1) README results table and iteration curve,
(2) `docker compose up` + `/readyz`, (3) labeling a Project 1 answer in the UI, (4) the demo PR's red
`release-gate` check and its failure message, (5) `uv run python scripts/report.py all` regenerating
the numbers.

Resume line: *Built an LLM-as-judge evaluation pipeline calibrated against HelpSteer2 human ratings at
mean quadratic-weighted kappa 0.49 (TEST n = 40); quantified position and length bias
and gated prompt releases on it in CI.*

## Setup and run

Local docker compose; requires Docker, [uv](https://docs.astral.sh/uv/) and a Groq key in the shared
env file (`PORTFOLIO_ENV_FILE`, default `F:/.secrets/portfolio.env`) for any LLM step.

```bash
uv sync --frozen
cp .env.example .env                                   # set POSTGRES_PASSWORD
docker compose -p p4-judge up -d --build               # postgres 127.0.0.1:15433, migrate job, UI 127.0.0.1:8504
DATABASE_URL=postgresql://judgecal:<pw>@127.0.0.1:15433/judgecal uv run judgecal seed-queue
curl -s http://127.0.0.1:8504/readyz

# experiments (resumable; exit 75 = daily quota, rerun later)
uv run python scripts/fetch_datasets.py && uv run python scripts/make_sample.py
uv run python scripts/run_calibration.py --rubric v1 --split dev
uv run python scripts/run_pending.py                   # remaining steps in priority order
uv run python scripts/report.py all && uv run python scripts/render_readme.py

# release gate
make eval        # needs GROQ_API_KEY; writes eval_runs/latest.json (commit it with the prompt change)
make gate        # what CI runs, no secrets
docker compose -p p4-judge stop
```
