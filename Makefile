.PHONY: install lint type test check eval baseline gate up down migrate seed report bias calib

UV ?= uv
RUN = $(UV) run --frozen

install:
	$(UV) sync --frozen --python 3.12

lint:
	$(RUN) ruff check . && $(RUN) ruff format --check .

type:
	$(RUN) mypy

test:
	$(RUN) pytest -q -rs

check: lint type test gate

# Release gate, step 1 (needs GROQ_API_KEY): run the system-under-test prompt + calibrated judge on
# the fixed eval set; writes eval_runs/latest.json (commit it with the prompt change).
eval:
	$(RUN) python -m judgecal.evalrun

# Promote the latest run to the baseline (deliberate; reviewed in the PR).
baseline:
	cp eval_runs/latest.json eval_runs/baseline.json

# Release gate, step 2 (what CI runs, no secrets): evidence fresh + no regression vs baseline.
gate:
	$(RUN) python -m judgecal.gate

up:
	docker compose -p p4-judge up -d --build

down:
	docker compose -p p4-judge stop

migrate:
	$(RUN) python -m judgecal.cli migrate

seed:
	$(RUN) python -m judgecal.cli seed-queue

calib:
	$(RUN) python scripts/run_calibration.py --rubric $(RUBRIC) --split $(SPLIT)

report:
	$(RUN) python scripts/report.py all
