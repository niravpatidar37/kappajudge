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

Security issues: see [SECURITY.md](SECURITY.md), not the public issue tracker.
