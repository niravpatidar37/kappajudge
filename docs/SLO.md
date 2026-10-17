# SLOs and cost

Status: deploy-ready; not applied (no cloud account connected). Targets below are design targets for
the Cloud Run deployment, not measured production numbers.

## Service level indicators and objectives

| SLI | Measurement | SLO (28-day window) |
|---|---|---|
| UI availability | share of `/healthz` uptime checks and UI HTTP requests answered non-5xx (Cloud Monitoring uptime check through IAP + Cloud Run request metrics) | 99.5% (prod), 99% (staging) |
| UI readiness | share of 1-minute windows where `/readyz` is 200 (Postgres reachable, migrations applied) | 99.5% |
| UI latency | Cloud Run `request_latencies` for page/static requests (websocket session excluded) | p50 < 300 ms, p95 < 1.5 s, p99 < 3 s |
| Label write success | share of "Save label" submits that commit (app log `label_saved` vs errors) | 99.9% |
| Scheduled eval freshness | age of the newest `eval_runs` row with kind `release_gate` | < 26 h (prod, daily), < 8 days (staging, weekly) |
| Eval job success | share of eval job executions that finish and store a run | 95% (free-tier provider quotas cause retries) |
| Judge quality (guardrail) | quadratic-weighted kappa of the judge vs HelpSteer2 humans on the frozen TEST split, re-measured whenever the rubric or judge model changes | not below the value in the README results table minus 0.05; a drop blocks the change |
| Release quality | release-gate deltas per criterion vs baseline | no criterion drops > 0.05 normalized; safety drop = 0 (enforced in CI) |

Error budget policy: when the availability budget is exhausted, freeze UI feature deploys (prompt/rubric
changes still flow through the release gate) until the 28-day burn is back under budget.

Alerting (to configure with the project): uptime-check failure for 5 minutes; `/readyz` 503 for 5
minutes; eval freshness breach; eval job failure twice in a row; Cloud SQL CPU > 80% for 15 minutes;
LLM spend ledger near cap (job log `BudgetExceeded`).

## Monthly cost estimate (estimate, us-central1, list prices retrieved 2026-10-17)

Sources: Cloud SQL pricing https://cloud.google.com/sql/pricing , Cloud Run pricing
https://cloud.google.com/run/pricing , Secret Manager https://cloud.google.com/secret-manager/pricing ,
Artifact Registry https://cloud.google.com/artifact-registry/pricing , Cloud Scheduler
https://cloud.google.com/scheduler/pricing . Verify before relying on them.

| Item | Prod | Staging | Driver |
|---|---:|---:|---|
| Cloud SQL Enterprise `db-custom-1-3840` (1 vCPU, 3.75 GiB) | ~$98.6 (HA: $0.0826/vCPU-h + $0.014/GiB-h) | ~$49.3 (zonal: $0.0413 + $0.007/GiB-h) | always on; the largest line item |
| Cloud SQL SSD 10 GiB | ~$3.4 (HA) | ~$1.7 | grows with labels/run history (small) |
| Cloud Run UI, 1 min instance idle (1 vCPU, 1 GiB at $0.0000025/vCPU-s + $0.0000025/GiB-s) | ~$13.1 | $0 (min 0) | `min_instances` |
| Cloud Run UI active time (assume 20 labeling hours/month) | ~$2 | ~$1 | labeling sessions |
| Cloud Run eval job (15 items, ~10 min, 1 vCPU / 1 GiB) | ~$0.4 (daily) | ~$0.1 (weekly) | schedule |
| LLM calls per eval run (Groq paid rates; see `eval_runs/baseline.json` `usd`) | ~30 x run cost | ~4 x run cost | judge = qwen3.8-27b, SUT = gpt-oss-20b |
| Secret Manager (3 secrets), Artifact Registry (< 1 GB), Cloud Scheduler (1 job), logging (< 50 GiB) | ~$0.5 | ~$0.5 | near free tier |
| **Total (excluding LLM)** | **~$118 / month** | **~$53 / month** | |

Cost levers: drop prod Cloud SQL HA (-$50), share one Cloud SQL instance across environments with
separate databases (-$50 for staging), or set prod `min_instances = 0` (-$13, adds a cold start).
