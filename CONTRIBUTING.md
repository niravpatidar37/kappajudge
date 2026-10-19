# Contributing

Thanks for your interest. This is a small portfolio project; issues and focused pull requests are welcome.

1. `uv sync --frozen --python 3.12`
2. Before you push: `make lint type test gate` (ruff, mypy strict, pytest, the release gate).
3. Never commit secrets, `.env` files, LLM caches (`data/cache/`) or model weights. CI runs gitleaks
   on the full history.
4. Numbers in the README come from committed raw files. Edit `docs/README.template.md` and re-render
   with `uv run python scripts/render_readme.py`; do not type results by hand.
5. A prompt change (`prompts/rag_answer.md`) needs a fresh eval record from `make eval` committed with
   it, or the `release-gate` job fails. Changing the baseline (`make baseline`) is a deliberate,
   reviewed step.
6. Use conventional commit messages (`feat:`, `fix:`, `docs:`, `ci:` ...).
7. Smoke test the full stack before a release: see "Smoke test" in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Branch protection and CI on this repository

`main` is protected against force pushes and deletion (owner not locked out: `enforce_admins` off).
Required status checks are **not** enabled yet: GitHub Actions on this repository currently creates no
runs for `push` or `pull_request` events (only `workflow_dispatch` works), so a required check would never
report and every merge would be blocked. Until that is fixed, verify a branch by dispatching CI on it and
link the green run in the PR:

```bash
gh workflow run ci -R niravpatidar37/kappajudge --ref <branch>
gh run list -R niravpatidar37/kappajudge -w ci -b <branch> -L 1
```

When `push`/`pull_request` runs appear again, require the CI jobs (exact job names) with:

```bash
gh api -X PUT repos/niravpatidar37/kappajudge/branches/main/protection --input - <<'JSON'
{"required_status_checks": {"strict": true, "contexts": ["lint-type-test", "release-gate", "live-gate",
  "security", "docker", "smoke", "integration", "terraform", "tflint", "iac-scan"]},
 "enforce_admins": false, "required_pull_request_reviews": null, "restrictions": null,
 "allow_force_pushes": false, "allow_deletions": false}
JSON
```

Security issues: see [SECURITY.md](SECURITY.md), not the public issue tracker.
