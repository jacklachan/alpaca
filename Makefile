PYTHON ?= python

.PHONY: verify format-check lint type test kernel crash env-parity compile dashboard-check notices

verify: format-check lint type test kernel crash env-parity compile dashboard-check notices

format-check:
	$(PYTHON) -m ruff format --check .

lint:
	$(PYTHON) -m ruff check .

type:
	$(PYTHON) -m mypy glassbox dashboard tools main.py

test:
	$(PYTHON) -m pytest -q
	$(PYTHON) -m pip check

kernel:
	$(PYTHON) -m pytest tests/test_kernel.py -q

crash:
	$(PYTHON) tools/crash_drill.py -n 8 --seed 1

env-parity:
	$(PYTHON) tools/env_parity.py .env.example

compile:
	$(PYTHON) -m compileall -q glassbox dashboard tools main.py

dashboard-check:
	$(PYTHON) -m pytest tests/test_dashboard.py -q

notices:
	$(PYTHON) tools/build_notices.py --check
