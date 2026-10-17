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
gcloud iam service-accounts create judgecal-deployer --display-name="GitHub deployer (llm-judge-calibration)"
gcloud iam workload-identity-pools create github --location=global
gcloud iam workload-identity-pools providers create-oidc github-oidc --location=global \
  --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='niravpatidar37/llm-judge-calibration'"
gcloud iam service-accounts add-iam-policy-binding \
  "judgecal-deployer@${PROJECT}.iam.gserviceaccount.com" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')/locations/global/workloadIdentityPools/github/attribute.repository/niravpatidar37/llm-judge-calibration"
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
# add the Groq key (DB passwords are generated and written by Terraform, write-only)
printf '%s' "$GROQ_API_KEY" | gcloud secrets versions add judgecal-ENV-groq-api-key --data-file=-
# build + push one image, then the second apply creates Cloud Run service, jobs and scheduler
terraform apply ... -var="deploy_services=true" -var="image=REGION-docker.pkg.dev/$PROJECT/judgecal/llm-judge-calibration@sha256:<digest>"
gcloud run jobs execute judgecal-ENV-migrate --region "$REGION" --wait
```

After that, every push to `main` runs `deploy.yml`: build -> push -> staging (migrate job, no-traffic
canary, smoke `/healthz` + `/readyz`, promote, eval job image) -> prod after approval (migrate, canary,
10% traffic, 5-minute soak, smoke, 100%), with automatic rollback on a failed smoke test.

## Verify

```bash
gcloud run services describe judgecal-ENV-ui --region "$REGION" --format='value(status.url,status.latestReadyRevisionName)'
bash scripts/smoke_deploy.sh "https://<service-url>"   # with SMOKE_ID_TOKEN for IAP
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

## Rotate secrets

```bash
# Groq key: add a new version; new revisions/executions pick up `latest`
printf '%s' "$NEW_KEY" | gcloud secrets versions add judgecal-ENV-groq-api-key --data-file=-
gcloud secrets versions disable <old-version> --secret judgecal-ENV-groq-api-key
# DB passwords: bump db_password_version (write-only args: new random values go to Cloud SQL + Secret Manager)
terraform apply ... -var="db_password_version=2"
gcloud run services update judgecal-ENV-ui --region "$REGION" --update-labels=rotated="$(date +%s)"   # new revision reads the new secret
```

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
