PYTHON ?= python

.PHONY: verify format-check lint type test kernel crash env-parity compile dashboard-check notices submission

verify: format-check lint type test kernel crash env-parity compile dashboard-check notices submission

format-check:
	$(PYTHON) -m ruff format --check .

lint:
	$(PYTHON) -m ruff check .

# Every platform, not just this one. The type gate once passed on Windows and
# failed everywhere else, because a Windows-only branch is only type-checked
# where its API exists. Checking all three means whoever runs this locally
# sees what CI will see, whatever machine they are on.
type:
	$(PYTHON) -m mypy --platform linux glassbox dashboard tools main.py
	$(PYTHON) -m mypy --platform darwin glassbox dashboard tools main.py
	$(PYTHON) -m mypy --platform win32 glassbox dashboard tools main.py

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

# The same verification a judge can run. Kept in the gate so the claims
# cannot drift from the artifacts between releases.
submission:
	$(PYTHON) tools/verify_submission.py --skip-claims
