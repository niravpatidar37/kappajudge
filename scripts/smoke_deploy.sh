#!/usr/bin/env bash
# Smoke test a deployed revision: /healthz (liveness) and /readyz (Postgres + migrations) must return 200.
# Usage: SMOKE_ID_TOKEN=<IAP OIDC token> scripts/smoke_deploy.sh https://<service-url>
set -euo pipefail
url="${1:?usage: smoke_deploy.sh URL}"
auth=()
if [ -n "${SMOKE_ID_TOKEN:-}" ]; then auth=(-H "Authorization: Bearer ${SMOKE_ID_TOKEN}"); fi
for path in /healthz /readyz; do
  ok=0
  for _ in $(seq 1 20); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${auth[@]}" "${url}${path}" || true)"
    if [ "$code" = "200" ]; then ok=1; break; fi
    sleep 3
  done
  if [ "$ok" != "1" ]; then echo "smoke failed: ${url}${path} -> ${code}"; exit 1; fi
  echo "ok ${path}"
done
