# Glassbox implementation handover

## Repository state

- Branch: `review`
- Approved product: Approach A, proof-first options-only scored execution
- Baseline before this implementation run: `f2d25c7`
- Work is committed in reviewable slices; inspect `git log --oneline` for the
  exact current head.
- No push or merge was performed.

## What is implemented

### Execution safety

- Distinct explicit expected dev/scored Alpaca account IDs are required and the
  returned account number is asserted before readiness.
- Dev venue proof is capped at $50, requires a clean baseline, cleans only its
  own exact quantity, confirms cancellations, reconciles flat, and returns
  nonzero on every uncertain/failure path.
- Cancel/reprice waits for terminal state, handles late fills, and never places
  overlapping replacement quantity.
- Partial or timed-out single-leg development orders cancel residual quantity.

### Restart and state safety

- Stable semantic plan IDs and deterministic client order IDs survive restart.
- Submission intent is journalled before the broker call; accepted-then-timeout
  outcomes reconcile by client ID without duplicate submission.
- Kill-switch, exit targets, and positioned catalysts use atomic durable writes
  and fail closed when corrupt.

### Product path

- `ThesisLayer.select()` accepts only one supplied candidate ID or abstention.
  It returns the exact original `TradePlan`; it cannot author a trade.
- Scored construction registers deterministic SPY and QQQ option strategies
  only. Non-option injection is policy-refused and no scored crypto job exists.
- Every selection passes through the deterministic kernel and hardened executor.
- Dependencies are exact-locked, deployment requires a full reviewed SHA, and
  CI mirrors `make verify`.
- Dashboard and public docs describe Alpaca Trading/Data APIs plus CLI usage
  only.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy glassbox dashboard tools main.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/test_kernel.py -q
.\.venv\Scripts\python.exe tools/crash_drill.py -n 8 --seed 1
.\.venv\Scripts\python.exe tools/env_parity.py .env.example
.\.venv\Scripts\python.exe -m compileall -q glassbox dashboard tools main.py
.\.venv\Scripts\python.exe -m pip check
```

Use fresh output rather than copying a historical test count into submission
materials.

## External blockers

1. **Dev venue proof pending.** No usable local Alpaca credentials or explicit
   expected account IDs were supplied. No live paper order has been placed.
2. **VPS soak pending.** No VPS/SSH target was supplied. Nothing has been
   deployed.
3. **Scored activation pending.** It must follow the dev proof, exact reviewed
   release selection, and explicit user direction.

When inputs arrive, first run `python tools/live_check.py` read-only. Only after
identity and cleanliness are proven should the user-authorized
`--trade --notional <value-at-most-50>` proof run. For deployment, pass the
chosen full SHA to `deploy/setup.sh`; never deploy a branch name.

## Claims safe to use

- “Deterministic code creates immutable, fully priced SPY/QQQ option candidates.”
- “Bounded AI selects one existing candidate or abstains.”
- “The exact selected object still passes 13 deterministic risk invariants.”
- “Order intent, ambiguous submission reconciliation, and terminal cancellation
  are journalled and reconcilable against Alpaca broker records.”
- “The scored path is options-only.”

Do not claim any additional broker integration, completed live execution or deployment,
multi-day unattended operation, active scored equity/crypto sleeves, or a model
that invents trade plans.
