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

`main` is protected: the ten CI jobs (`lint-type-test`, `release-gate`, `live-gate`, `security`, `docker`,
`smoke`, `integration`, `terraform`, `tflint`, `iac-scan`) are required and the branch must be up to date;
force pushes and deletion are blocked; `enforce_admins` is off so the owner is never locked out.

For a while GitHub Actions created no runs for `push`/`pull_request` events on this repository (only
`workflow_dispatch` worked; four old dispatch runs are still stuck `queued`). Both triggers worked again
on 2026-10-19 (PR #2). If they break again, a required check never reports and merges block: verify the
branch with a dispatched run, link it in the PR, and either merge as admin or drop the required checks
temporarily:

```bash
gh workflow run ci -R niravpatidar37/kappajudge --ref <branch>
gh run list -R niravpatidar37/kappajudge -w ci -b <branch> -L 1
# temporary: keep force-push/deletion protection, drop the required checks
gh api -X DELETE repos/niravpatidar37/kappajudge/branches/main/protection/required_status_checks
# restore (exact job names, GitHub Actions app id 15368)
gh api -X PATCH repos/niravpatidar37/kappajudge/branches/main/protection/required_status_checks --input - <<'JSON'
{"strict": true, "checks": [{"context": "lint-type-test", "app_id": 15368}, {"context": "release-gate", "app_id": 15368},
  {"context": "live-gate", "app_id": 15368}, {"context": "security", "app_id": 15368}, {"context": "docker", "app_id": 15368},
  {"context": "smoke", "app_id": 15368}, {"context": "integration", "app_id": 15368}, {"context": "terraform", "app_id": 15368},
  {"context": "tflint", "app_id": 15368}, {"context": "iac-scan", "app_id": 15368}]}
JSON
```

The open demo PR #1 is meant to stay blocked: its `release-gate` fails by design.
