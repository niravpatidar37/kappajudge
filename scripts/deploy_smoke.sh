#!/usr/bin/env bash
# Post-deploy smoke test (used by .github/workflows/deploy.yml): runs scripts/smoke.py against a Cloud Run
# URL behind IAP. The IAP identity token comes from SMOKE_ID_TOKEN (google-github-actions/auth), the API key
# is read from Secret Manager here and masked, so it never lands in argv, outputs or GITHUB_ENV.
# Usage: API_KEY_SECRET=judgecal-ENV-ui-api-keys scripts/deploy_smoke.sh URL OUT.json [smoke.py flags...]
set -euo pipefail
url="${1:?usage: deploy_smoke.sh URL OUT.json [flags]}"
out="${2:?usage: deploy_smoke.sh URL OUT.json [flags]}"
shift 2
key="$(gcloud secrets versions access latest --secret="${API_KEY_SECRET:?}" | cut -d, -f1)"
echo "::add-mask::${key}"
# Cloud Run can take a few seconds to route a fresh revision tag: wait for /healthz before the full run.
for _ in $(seq 1 20); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Authorization: Bearer ${SMOKE_ID_TOKEN:-}" "${url}/healthz" || true)"
  [ "$code" = "200" ] && break
  sleep 3
done
SMOKE_API_KEY="$key" python3 scripts/smoke.py --base-url "$url" --json-out "$out" "$@"
