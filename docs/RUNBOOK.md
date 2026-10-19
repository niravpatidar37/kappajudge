# Runbook

Status: deploy-ready; not applied (no cloud account connected). Commands are written for the
Terraform in `infra/terraform` and the workflow in `.github/workflows/deploy.yml`; they have not been
run against a real project.

Names below use `ENV` = `staging` | `prod`, `REGION` (default `us-central1`), `PROJECT`.

## One-time bootstrap

```bash
gcloud config set project "$PROJECT"
# 1. State bucket
gcloud storage buckets create "gs://${PROJECT}-tfstate" --location="$REGION" --uniform-bucket-level-access
gcloud storage buckets update "gs://${PROJECT}-tfstate" --versioning
# 2. Deploy service account + Workload Identity Federation for GitHub Actions
gcloud iam service-accounts create judgecal-deployer --display-name="GitHub deployer (kappajudge)"
gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc github-oidc --location=global \
  --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='niravpatidar37/kappajudge'"
gcloud iam service-accounts add-iam-policy-binding \
  "judgecal-deployer@${PROJECT}.iam.gserviceaccount.com" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')/locations/global/workloadIdentityPools/github/attribute.repository/niravpatidar37/kappajudge"
# 3. IAP programmatic access for CI smoke tests: create an OAuth client (Web application) for
#    programmatic access and allowlist it (https://cloud.google.com/iap/docs/sharing-oauth-clients#programmatic_access);
#    its client id becomes the GCP_IAP_CLIENT_ID repo variable. Pass the deployer SA in iap_members.
# 4. Repo variables (Settings -> Secrets and variables -> Actions -> Variables)
gh variable set GCP_PROJECT_ID --body "$PROJECT"
gh variable set GCP_REGION --body "$REGION"
gh variable set GCP_WIF_PROVIDER --body "projects/<NUMBER>/locations/global/workloadIdentityPools/github/providers/github-oidc"
gh variable set GCP_DEPLOY_SA --body "judgecal-deployer@${PROJECT}.iam.gserviceaccount.com"
gh variable set GCP_IAP_CLIENT_ID --body "<oauth-client-id>"
# 5. GitHub environments: `staging`, and `production` with required reviewers (the manual gate).
```

## Deploy

Two-phase apply per environment (the service needs secret versions and an image first):

```bash
cd infra/terraform/envs/ENV
terraform init -backend-config="bucket=${PROJECT}-tfstate" -backend-config="prefix=judgecal/ENV"
terraform apply -var="project_id=$PROJECT" -var="deployer_service_account=judgecal-deployer@${PROJECT}.iam.gserviceaccount.com" \
  -var='iap_members=["user:owner@example.com","serviceAccount:judgecal-deployer@'"$PROJECT"'.iam.gserviceaccount.com"]'
# add the Groq key and the API key(s) (DB passwords are generated and written by Terraform, write-only)
printf '%s' "$GROQ_API_KEY" | gcloud secrets versions add judgecal-ENV-groq-api-key --data-file=-
openssl rand -hex 24 | tr -d '\n' | gcloud secrets versions add judgecal-ENV-ui-api-keys --data-file=-
# build + push one image, then the second apply creates Cloud Run service, jobs and scheduler
terraform apply ... -var="deploy_services=true" -var="image=REGION-docker.pkg.dev/$PROJECT/judgecal/kappajudge@sha256:<digest>"
gcloud run jobs execute judgecal-ENV-migrate --region "$REGION" --wait
```

Optional plan-time variables: `-var='alert_email=oncall@example.com'` (notification channel) and
`-var='operator_invokers=["user:owner@example.com"]'` (may use `gcloud run services proxy`).

After that, every push to `main` runs `deploy.yml`: build -> push -> keyless cosign signature ->
staging (verify signature, migrate job, no-traffic canary, `scripts/smoke.py`, eval-job preflight,
promote, smoke) -> prod after approval (verify, migrate, canary at 0% + smoke, 10% traffic, 5-minute
soak, smoke, 100%, smoke), with automatic rollback of the service and the eval job image on any failure.

## Access model (the UI holds labeling data: never public)

- `iap_enabled = true` on the Cloud Run service: every request needs a Google identity listed in
  `iap_members` (validated: only `user:`/`group:`/`serviceAccount:`/`domain:`; `allUsers` and
  `allAuthenticatedUsers` are refused by a Terraform validation and by `tests/test_infra_guards.py`).
- `roles/run.invoker` is held only by the IAP service agent and, optionally, the named
  `operator_invokers`. There is no public invoker binding anywhere in `infra/terraform`.
- Operator path without a browser session: an IAM-authenticated local tunnel,
  `gcloud run services proxy judgecal-ENV-ui --region "$REGION" --port 8504`, then
  `http://127.0.0.1:8504` (needs `operator_invokers`).
- Stricter option: `ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"` behind an external HTTPS load
  balancer with IAP on its backend service (not built here).
- `/api/v1/*` additionally requires `X-API-Key` (secret `judgecal-ENV-ui-api-keys`), so an IAP
  misconfiguration alone does not expose labels; the API can only write/read `api-*`/`smoke-*` labelers.

## Verify

```bash
gcloud run services describe judgecal-ENV-ui --region "$REGION" --format='value(status.url,status.latestReadyRevisionName)'
# full smoke test (22 checks; the compose-only and gate-CLI checks SKIP against the cloud)
SMOKE_ID_TOKEN="$(gcloud auth print-identity-token --audiences="$IAP_CLIENT_ID" --include-email)" \
API_KEY_SECRET=judgecal-ENV-ui-api-keys bash scripts/deploy_smoke.sh "https://<service-url>" smoke.json
# scheduled eval job preflight: config, baseline/judge-config match, Postgres + migrations, Groq key; no model calls
gcloud run jobs execute judgecal-ENV-eval --region "$REGION" --args=-m,judgecal.evalrun,--smoke --wait
gcloud run jobs executions list --job judgecal-ENV-eval --region "$REGION" --limit 5
gcloud logging read 'resource.type="cloud_run_revision" AND severity>=ERROR' --limit 20 --freshness 1h
```

## Rollback

```bash
# traffic back to the previous revision (instant, no rebuild)
gcloud run revisions list --service judgecal-ENV-ui --region "$REGION" --limit 5
gcloud run services update-traffic judgecal-ENV-ui --region "$REGION" --to-revisions "<previous-revision>=100"
# jobs: point back at the previous digest
gcloud run jobs update judgecal-ENV-eval --region "$REGION" --image "<previous image@sha256:...>"
```

Schema migrations are additive (new tables/columns). A migration that must be undone ships as a new
numbered forward migration; do not hand-edit `schema_migrations`. Cloud SQL PITR (7 days) is the last
resort: `gcloud sql instances clone judgecal-ENV-pg judgecal-ENV-pg-restore --point-in-time=<RFC3339>`.

A prompt or rubric regression is rolled back by reverting the commit: the release gate re-checks the
reverted prompt's committed evidence.

## Smoke test (`scripts/smoke.py`)

One script, standard library only, `--base-url` + `SMOKE_API_KEY` (and `SMOKE_ID_TOKEN` for IAP) from the
environment, PASS/FAIL per line, JSON summary with `--json-out`. Checks: liveness, readiness (Postgres +
0 pending migrations), Streamlit health and app shell, security headers, auth rejected/accepted,
UI kill-switch state, labeling queue from Postgres, input validation (422/403), oversize body (413),
errors without stack traces, label write/read round trip, eval-run listing, rate limit (429 +
Retry-After + recovery). With `--compose` (local/CI stack): migrations idempotent, eval-job preflight,
eval job in recorded LLM mode stores a run and passes the gate, drift detection on the weakened prompt
(exit 3 + `eval_gate_drift` event), and the kill switch stopping the eval job. With `--gate-cli`: the gate
passes the baseline record and fails `tests/fixtures/regressed_record.json`.

```bash
# local stack (what results/smoke_local.json records)
export POSTGRES_PASSWORD=... SMOKE_API_KEY="$(openssl rand -hex 24)"
C="docker compose -p p4-judge -f docker-compose.yml -f docker-compose.smoke.yml"
$C up -d --build app && $C run --rm -T migrate python -m judgecal.cli seed-queue
uv run --frozen python scripts/smoke.py --base-url http://127.0.0.1:8504 --compose "$C" --gate-cli \
  --json-out results/smoke_local.json
```

The write check upserts one label as labeler `smoke-probe` (excluded from counts and agreement).
Use `--read-only` to skip writes. Recorded LLM mode (`LLM_RECORDED_MODE=true`) replays the real
outputs in `tests/fixtures/recorded_llm` (re-export: `scripts/export_recorded_llm.py`) and is refused
when `ENV=prod`.

## Monitoring

Alerts, uptime check and SLO are in `infra/terraform/modules/judgecal/monitoring.tf`; the table is in
`docs/SLO.md`. On the first apply, confirm the `/healthz` uptime check passes through IAP (it uses the
Monitoring service agent's OIDC token, which is granted `roles/iap.httpsResourceAccessor`); if IAP rejects
it, the 5xx/burn-rate/job alerts still cover the service. Note: by default the Monitoring service agent
can invoke authenticated Cloud Run services (Tenable TRA-2026-05, https://tenable.com/security/research/tra-2026-05), so restrict who may create uptime
checks (`roles/monitoring.uptimeCheckConfigEditor`).

## On-call checklist

1. Acknowledge the alert; note the environment and the alert name.
2. `gcloud run services describe judgecal-ENV-ui --region "$REGION"`: which revision serves traffic, and
   was there a deploy in the last hour (`gh run list -R niravpatidar37/kappajudge -w deploy`)?
3. Run the smoke test (Verify, above). A failed check names the broken path.
4. Recent deploy + failing smoke: roll back first (Rollback, below), investigate second.
5. `/readyz` 503: Cloud SQL (`gcloud sql instances describe judgecal-ENV-pg`), password secret version,
   pending migrations (re-run the migrate job).
6. Eval job alerts: `gcloud logging read 'resource.type="cloud_run_job" AND jsonPayload.event:"eval_"' --limit 20`.
   `error_type=QuotaExhausted`: provider daily quota, re-run later. `BudgetExceeded`: spend cap.
   `eval_gate_drift`: read `jsonPayload.failures`; provider-side model drift is the usual cause.
7. Data incident (bad labels being written): flip the label-write kill switch (below).
8. Close with a short note: cause, action, follow-up issue.

## Rotate secrets

```bash
# Groq key: add a new version; new revisions/executions pick up `latest`
printf '%s' "$NEW_KEY" | gcloud secrets versions add judgecal-ENV-groq-api-key --data-file=-
gcloud secrets versions disable <old-version> --secret judgecal-ENV-groq-api-key
# DB passwords: bump db_password_version (write-only args: new random values go to Cloud SQL + Secret Manager)
terraform apply ... -var="db_password_version=2"
gcloud run services update judgecal-ENV-ui --region "$REGION" --update-labels=rotated="$(date +%s)"   # new revision reads the new secret
```

API keys (`/api/v1`): comma-separated, so rotation has no gap.

```bash
printf '%s,%s' "$NEW_KEY" "$OLD_KEY" | gcloud secrets versions add judgecal-ENV-ui-api-keys --data-file=-
gcloud run services update judgecal-ENV-ui --region "$REGION" --update-labels=rotated="$(date +%s)"
# move clients to NEW_KEY, then publish NEW_KEY alone and roll a new revision again
```

## Kill switch: label writes (read-only labeling)

```bash
gcloud run services update judgecal-ENV-ui --region "$REGION" --update-env-vars LABEL_WRITES_ENABLED=false
gcloud run services update-traffic judgecal-ENV-ui --region "$REGION" --to-latest
```

The Save button is disabled and `POST /api/v1/labels` returns 503 `writes_disabled`; reads keep working.

## Kill switch (stop all model calls)

The UI never calls a model (`LLM_CALLS_ENABLED=false` is set on it). The scheduled eval job does:

```bash
gcloud run jobs update judgecal-ENV-eval --region "$REGION" --update-env-vars LLM_CALLS_ENABLED=false
gcloud scheduler jobs pause judgecal-ENV-eval-schedule --location "$REGION"
# then reconcile Terraform: kill_switch = true
```

With the switch on, only cached results are served and every uncached call raises `LLMDisabled`.
Locally: `LLM_CALLS_ENABLED=false` in `.env`. The hard spend cap (`LLM_SPEND_CAP_USD`, per-project cap
$5) refuses calls before they are made regardless of the switch.

## Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| `/readyz` 503, `/healthz` 200 | Cloud SQL down, wrong password secret, migrations not applied | check `gcloud sql instances describe`, re-run the migrate job, check the secret version |
| Startup probe fails after deploy | new image cannot reach Postgres or crashes on import | automatic rollback fires in CD; otherwise `update-traffic` to previous revision |
| UI disconnects every few minutes | session affinity off or request timeout too short | template has `session_affinity = true`, `timeout = 3600s`; confirm on the revision |
| Eval job fails with HTTP 429 / long waits | Groq free-tier per-model daily token quota | job retries once; results are cached by content hash so a rerun only pays for unfinished items; or upgrade the Groq plan |
| Eval job `BudgetExceeded` | spend cap reached | inspect the ledger; raise `llm_spend_cap_usd` deliberately or wait |
| CI `release-gate` fails "stale evidence" | prompt changed without re-running `make eval` | run `make eval`, commit `eval_runs/latest.json` |
| CI `release-gate` fails "regression" | the prompt change made answers worse | fix the prompt; or, if the drop is intended and reviewed, `make baseline` in a separate PR |
| IAP 403 for a labeler | principal not in `iap_members` | add the user/group and apply |
| `eval_gate_drift` alert | the scheduled run's scores fail the gate vs `eval_runs/baseline.json` | re-run the job; if it persists, the provider model changed: investigate, then re-baseline deliberately (`make baseline`, reviewed PR) |
| Eval preflight fails in deploy | judge config no longer matches the baseline, DB unreachable, or Groq key missing | the deploy rolls back automatically; read the job execution log (`eval_preflight` event) |
| Smoke `rate_limit_429_retry_after` fails after deploy | several instances each with their own bucket | expected only if instances > burst/150; raise the burst or skip with `--skip-rate-limit` |
