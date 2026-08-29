# Options-Only Proof-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved options-only scored path with bounded AI, terminal order-state handling, restart-safe identity, durable state, immutable deployment, and truthful evidence.

**Architecture:** Deterministic strategy code creates immutable, fully priced option candidates; AI can only select one existing candidate ID or abstain. All selected candidates pass through the existing risk kernel and a hardened executor that journals intent, reconciles ambiguous submissions, and proves cancellation terminal before replacement or return. Safety state uses one atomic fail-closed JSON persistence primitive.

**Tech Stack:** Python 3.12, Pydantic 2, Alpaca Trading/Data APIs, Anthropic API, APScheduler, pytest, Ruff, mypy, Bash/systemd.

**Spec:** `docs/superpowers/specs/2026-08-29-options-only-proof-first-design.md`

## Global Constraints

- Work directly on `review` from `f2d25c7`; preserve unrelated user changes.
- The scored path is options-only; crypto is a separate dev connectivity proof.
- AI may select or abstain only among deterministic, pre-priced candidates.
- AI cannot create contracts, quantities, max loss, exits, or limit prices.
- The deterministic risk kernel remains the only path to execution.
- No live order, push, merge, submission, or deployment without its external gate.
- Every production behavior change must have a failing test observed first.
- A cancel request is insufficient; replacement and completion require observed terminal state.
- Durable safety-state corruption and write failures fail closed.

---

### Task 1: Account identity contract

**Files:**
- Modify: `glassbox/env.py`
- Modify: `glassbox/broker.py`
- Modify: `.env.example`
- Test: `tests/test_env.py`
- Test: `tests/test_broker.py`

**Interfaces:**
- Consumes: Alpaca account objects exposing `account_number`.
- Produces: `expected_account_id(environment: str) -> str` and a fatal identity check in `Broker.assert_ready()`.

- [ ] **Step 1: Write failing boundary tests**

  Add cases proving missing expected ID, equal dev/scored IDs, and returned-ID
  mismatch fail; matching dev and scored IDs pass their respective identity
  check. Use literal account numbers and a fake account response.

- [ ] **Step 2: Run the focused tests and observe the identity cases fail**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_env.py tests/test_broker.py -q`

- [ ] **Step 3: Implement explicit identity lookup and comparison**

  ```python
  def expected_account_id(environment: str) -> str:
      name = f"ALPACA_EXPECTED_{environment.upper()}_ACCOUNT_ID"
      expected = require(name)
      other = require("ALPACA_EXPECTED_SCORED_ACCOUNT_ID" if environment == "dev"
                      else "ALPACA_EXPECTED_DEV_ACCOUNT_ID")
      if expected == other:
          raise EnvError("dev and scored account IDs must be different")
      return expected
  ```

  Call this before readiness checks and compare it to
  `str(acct.account_number)` with a fatal `RuntimeError` on mismatch.

- [ ] **Step 4: Run the focused tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_env.py tests/test_broker.py -q`

### Task 2: Terminal cancellation primitive

**Files:**
- Modify: `glassbox/broker.py`
- Test: `tests/test_broker.py`

**Interfaces:**
- Produces: `OrderStateUncertain` and `Broker.cancel_and_confirm(order_id, client_order_id, *, timeout=15.0, poll_seconds=0.5)` returning a terminal order.

- [ ] **Step 1: Write failing cancellation race tests**

  Model `new → cancel requested → partially_filled → canceled` and prove the
  returned object carries the final fill. Add a timeout case that raises
  `OrderStateUncertain` and never reports cancellation as complete.

- [ ] **Step 2: Run the tests and observe the missing API fail**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_broker.py -q`

- [ ] **Step 3: Implement bounded state polling**

  Normalize enum/string statuses with one helper. Request cancel once, poll by
  client ID until a member of the broker terminal-state set appears, and raise
  on deadline. Journal `ORDER_CANCEL_REQUESTED`, `ORDER_CANCEL_CONFIRMED`, or
  `ORDER_CANCEL_UNCERTAIN` with both IDs and the final status/fill.

- [ ] **Step 4: Run cancellation tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_broker.py -q`

### Task 3: Safe live venue state machine

**Files:**
- Modify: `tools/live_check.py`
- Create: `tests/test_live_check.py`

**Interfaces:**
- Consumes: `Broker.assert_ready()`, `positions()`, `open_orders()`, `submit()`, `get_order_by_coid()`, and `cancel_and_confirm()`.
- Produces: `run_trade_check(broker, journal, notional: Decimal, *, sleep) -> LiveTradeResult` and CLI exit status derived from that result.

- [ ] **Step 1: Write failing live-check safety tests**

  Cover: `$50.01` refusal before submit; account mismatch; dirty position and
  open-order baselines; late entry fill captured after cancel; exact-quantity
  sell cleanup; exit partial timeout; residual position; residual test order;
  and nonzero CLI outcome for every cleanup uncertainty. The fake broker must
  fail if `close_position()` is called.

- [ ] **Step 2: Run tests and observe safety failures**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_live_check.py -q`

- [ ] **Step 3: Implement the bounded state machine**

  Parse notional as `Decimal`, enforce `0 < notional <= Decimal("50.00")`,
  prove a clean baseline, terminally settle the entry, submit a sell for exactly
  its final fill under a second deterministic ID, terminally settle the exit,
  and re-read exact baseline state. Convert every cleanup warning into failure.

- [ ] **Step 4: Run live-check tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_live_check.py -q`

### Task 4: Cancel-confirm repricing and residual cleanup

**Files:**
- Modify: `glassbox/execute.py`
- Modify: `tests/test_execute.py`

**Interfaces:**
- Consumes: `Broker.cancel_and_confirm(...)`.
- Produces: `_cancel_leg(leg: LegResult) -> bool`; `_reprice()` submits only after it returns true.

- [ ] **Step 1: Write failing executor race tests**

  Add a fake whose cancel request causes a late fill before terminal
  cancellation. Assert the replacement quantity equals the true remainder.
  Add an unconfirmed cancel case and assert no replacement is submitted. Add
  single equity and crypto partial/timeout cases asserting residual cancellation
  is confirmed before return.

- [ ] **Step 2: Run the executor tests and observe failures**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_execute.py -q`

- [ ] **Step 3: Implement terminal-state ledger refresh**

  Copy final `filled_qty`, `filled_avg_price`, and status from the confirmed
  order before `LegResult.bank()`. Cancel all incomplete working orders at the
  end of option attempts. In `_execute_single`, terminally cancel an incomplete
  remainder and make an uncertain cancel unsuccessful.

- [ ] **Step 4: Run executor and kernel regression tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_execute.py tests/test_kernel.py tests/test_audit_regressions.py -q`

### Task 5: Deterministic plan identity and ambiguous-submit reconciliation

**Files:**
- Modify: `glassbox/ids.py`
- Modify: `glassbox/schema.py`
- Modify: `glassbox/strategies/event_vol.py`
- Modify: `glassbox/strategies/core.py`
- Modify: `glassbox/strategies/crypto.py`
- Modify: `glassbox/execute.py`
- Test: `tests/test_execute.py`
- Test: `tests/test_strategies.py`

**Interfaces:**
- Produces: `stable_plan_id(namespace: str, *parts: object) -> str` and `_submit_with_reconciliation(...)`.

- [ ] **Step 1: Write failing stability and timeout tests**

  Prove identical event opportunity inputs across fresh strategy instances
  yield the same plan/client order IDs. Prove a changed contract or catalyst
  yields a different ID. Model submit raising after broker acceptance and
  assert the existing order is adopted with no second submit.

- [ ] **Step 2: Run focused tests and observe failures**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_strategies.py tests/test_execute.py -q`

- [ ] **Step 3: Implement stable IDs and intent-first submission**

  Hash a canonical JSON encoding of namespace and semantic parts. Event-vol
  uses catalyst, underlying, expiry, and both OCC symbols as its semantic key.
  Freeze `TradePlan`. Before submit, append `ORDER_SUBMIT_INTENT`; on exception,
  poll only the same client ID and adopt it if observed.

- [ ] **Step 4: Run focused tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_strategies.py tests/test_execute.py -q`

### Task 6: Atomic fail-closed safety state

**Files:**
- Create: `glassbox/state.py`
- Modify: `glassbox/manage.py`
- Modify: `glassbox/scheduler.py`
- Create: `tests/test_state.py`
- Modify: `tests/test_manage.py`
- Modify: `tests/test_audit_regressions.py`

**Interfaces:**
- Produces: `atomic_write_json`, `read_json`, `StateCorrupt`, and `StateWriteError`.

- [ ] **Step 1: Write failing persistence tests**

  Prove a replace failure preserves the previous valid file, successful writes
  leave no temp file, malformed targets and positioned state raise
  `StateCorrupt`, and a running positioned-state write failure latches the
  scheduler state fault and blocks a subsequent new entry.

- [ ] **Step 2: Run persistence tests and observe failures**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_state.py tests/test_manage.py tests/test_audit_regressions.py -q`

- [ ] **Step 3: Implement atomic persistence and caller schemas**

  Write JSON to a unique same-directory temp file, flush and `os.fsync`, call
  `os.replace`, then directory-fsync where supported. Raise typed failures.
  Validate record/container types and required keys in manage/scheduler; missing
  files alone receive defaults. Remove warning-and-continue writes.

- [ ] **Step 4: Run persistence tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_state.py tests/test_manage.py tests/test_audit_regressions.py -q`

### Task 7: Bounded AI candidate selection

**Files:**
- Modify: `glassbox/thesis.py`
- Modify: `glassbox/schema.py`
- Modify: `glassbox/scheduler.py`
- Create: `tests/test_thesis.py`
- Modify: `tests/test_audit_regressions.py`

**Interfaces:**
- Produces: `Selection(candidate_id: str | None, rationale: str)` and `ThesisLayer.select(candidates: list[TradePlan], state, journal) -> TradePlan | None`.

- [ ] **Step 1: Write failing selection-boundary tests**

  Prove a valid candidate ID returns the exact original object; abstention,
  unknown IDs, malformed JSON, timeout, and missing credentials return `None`;
  model-provided contract/size/price fields are ignored because no plan is
  constructed from output.

- [ ] **Step 2: Run thesis tests and observe failures**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_thesis.py -q`

- [ ] **Step 3: Replace generative proposals with ID-only selection**

  Send only immutable candidate summaries and require JSON
  `{"candidate_id": "<existing id>"}` or `{"candidate_id": null}`. Validate
  the response, look up in the original map, journal selection/abstention, and
  remove `ThesisLayer.propose()` plus its plan-generation prompt.

- [ ] **Step 4: Run thesis tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_thesis.py -q`

### Task 8: Options-only scored scheduler

**Files:**
- Modify: `main.py`
- Modify: `glassbox/scheduler.py`
- Modify: `glassbox/config.py`
- Modify: `tests/test_audit_regressions.py`
- Create: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: deterministic option candidates and `ThesisLayer.select()`.
- Produces: at most one kernel-reviewed selected option per scored cycle.

- [ ] **Step 1: Write failing scored-policy tests**

  Prove scored construction registers no core/crypto strategy or crypto job,
  two deterministic option candidates are offered to AI, abstention submits
  none, selection submits only one, and a non-option candidate is refused even
  if injected.

- [ ] **Step 2: Run scheduler tests and observe failures**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler.py tests/test_audit_regressions.py -q`

- [ ] **Step 3: Implement scored candidate collection and policy gate**

  Build SPY and QQQ `EventVolStrategy` instances for scored mode, omit core and
  crypto, omit `crypto_tick`, collect option candidates, select or abstain, then
  call `_review_and_execute()` exactly once. Journal policy refusals.

- [ ] **Step 4: Run scheduler tests to green**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler.py tests/test_audit_regressions.py -q`

### Task 9: Locked release and SHA-pinned deployment

**Files:**
- Create: `requirements.lock`
- Create: `requirements-dev.lock`
- Modify: `requirements.txt`
- Modify: `deploy/setup.sh`
- Modify: `.github/workflows/ci.yml`
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `tests/test_deploy.py`

**Interfaces:**
- Produces: `deploy/setup.sh <40-char-sha>` and `make verify`.

- [ ] **Step 1: Write failing deployment behavior tests**

  Execute setup in a command-stubbed temporary harness. Assert missing/short SHA
  exits before apt/git, exact SHA is fetched and detached, `HEAD` is verified,
  and install uses `requirements.lock` with exact `==` versions.

- [ ] **Step 2: Run deployment tests and observe failures**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_deploy.py -q`

- [ ] **Step 3: Pin the environment and rewrite deployment**

  Freeze the verified Python 3.12 environment into transitive exact pins,
  separate runtime from quality tooling, require the reviewed SHA argument,
  fetch that object explicitly, verify it, and install the runtime lock. Add
  Ruff format/lint and mypy settings plus a single verification target mirrored
  in Linux CI.

- [ ] **Step 4: Run release gates locally**

  Run: `.venv/Scripts/python.exe -m pip check`

  Run: `.venv/Scripts/python.exe -m ruff format --check .`

  Run: `.venv/Scripts/python.exe -m ruff check .`

  Run: `.venv/Scripts/python.exe -m mypy glassbox dashboard tools main.py`

### Task 10: Truthful product and judging evidence

**Files:**
- Modify: `README.md`
- Modify: `DECISIONS.md`
- Modify: `PLAN.md`
- Modify: `HANDOVER.md`
- Modify: `HANDOVER-2.md`
- Modify: `SOCIAL.md`
- Modify: `dashboard/app.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Produces: one consistent options-only, bounded-AI product narrative and visible external gates.

- [ ] **Step 1: Inventory claims with repository search**

  Run: `rg -n "MCP|model proposes|ThesisLayer\.propose|equity sleeve|crypto sleeve|deployed|live order|end.to.end" README.md DECISIONS.md PLAN.md HANDOVER.md HANDOVER-2.md SOCIAL.md dashboard glassbox`

- [ ] **Step 2: Add failing dashboard content tests**

  Assert the body identifies the options-only scored path, bounded candidate
  selection, and pending live/deploy gates; assert it makes no MCP claim.

- [ ] **Step 3: Reconcile documentation and UI**

  Explain Alpaca Trading/Data API plus CLI usage, deterministic candidates,
  AI selection/abstention, kernel enforcement, scored instrument scope, and
  exact unperformed gates. Preserve historical audit facts only when clearly
  labeled as history rather than current operation.

- [ ] **Step 4: Run dashboard and claim checks**

  Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -q`

  Run: `rg -n "MCP" README.md DECISIONS.md PLAN.md SOCIAL.md dashboard glassbox`

  Expected: no current implementation claim; historical negations are explicit.

### Task 11: Full verification and hostile self-review

**Files:**
- Review: all changed files

**Interfaces:**
- Produces: fresh evidence and an explicit list of any remaining external gates.

- [ ] **Step 1: Run the complete local bar**

  Run: `.venv/Scripts/python.exe -m pytest -q`

  Run: `.venv/Scripts/python.exe -m pytest tests/test_kernel.py -q`

  Run: `.venv/Scripts/python.exe tools/crash_drill.py`

  Run: `.venv/Scripts/python.exe -m compileall -q glassbox dashboard tools main.py`

  Run: `.venv/Scripts/python.exe -m pip check`

  Run: `.venv/Scripts/python.exe -m ruff format --check .`

  Run: `.venv/Scripts/python.exe -m ruff check .`

  Run: `.venv/Scripts/python.exe -m mypy glassbox dashboard tools main.py`

- [ ] **Step 2: Run dashboard response-body and Linux-CI-equivalent checks**

  Use the commands encoded in `.github/workflows/ci.yml`, including actual
  response-body assertions rather than status-only probes.

- [ ] **Step 3: Review the diff against every design requirement**

  Check R1–R10 one by one, scan for secrets and open-ended dependencies, inspect
  `git diff --check`, and confirm no live call/deploy/push/merge occurred.

- [ ] **Step 4: Report evidence and blockers**

  Report focused/full command outputs, changed artifacts, and only these
  expected external gates if still absent: dev credentials plus explicit IDs
  for the minimal venue proof, and VPS/SSH target plus reviewed SHA for deploy.
