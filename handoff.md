# Glassbox Alpaca Hackathon — Build Handoff

**Prepared:** 2026-08-30

**Repository:** `C:\Users\Utkarsh\Desktop\Project\Trading\alpaca`

**Source branch:** `review`

**Remote baseline:** `origin/review` at
`f2d25c765b60e7e810a6ded7d5d3f64e270dc99f`

**Latest implementation checkpoint:**
`3c2e361baa7b52f54500d83d789b0f9f93ead8bf`

## 1. Current Outcome

The approved proof-first, options-only scored path is implemented through
reference-audit **Task D**.

The scored authority boundary is unchanged:

1. Deterministic code discovers and fully prices SPY/QQQ option candidates.
2. Active contracts and point-in-time quotes are validated before a candidate
   exists.
3. Candidates and the complete offered set are content-addressed.
4. Bounded AI may select one offered ID or abstain; it cannot invent or alter
   contracts, quantity, price, max loss, exits, or order type.
5. The exact original candidate still passes through the deterministic risk
   kernel and executor.
6. Equity and crypto are disabled on the scored path. Crypto remains a
   separate development-only connectivity proof.

Original checkpoints C0–C6 and audit Tasks A–D are complete. Audit Task E is
the next implementation checkpoint. The complete sequenced backlog, including
acceptance tests and external gates, is in `IMPLEMENTATION-PLAN.md`.

No Alpaca credentialed request, paper/live order, deployment, merge,
submission, or scored activation was performed. The user has authorized
creating and pushing branch `utk` to `jacklachan/alpaca`; that authorization
does not open the trading, deployment, legal-identity, or submission gates.

## 2. Governing Sources

Read in this order:

1. `IMPLEMENTATION-PLAN.md` — current status and detailed backlog.
2. `docs/superpowers/specs/2026-08-29-options-only-proof-first-design.md` —
   approved Approach A architecture.
3. `docs/superpowers/plans/2026-08-29-options-only-proof-first.md` — original
   C0–C6 TDD plan.
4. `C:\Users\Utkarsh\Desktop\Project\Trading\REfrences\audit-output\BUILD-THREAD-HANDOFF.md`.
5. `C:\Users\Utkarsh\Desktop\Project\Trading\REfrences\audit-output\GLASSBOX-REFERENCE-MASTER-PLAN.md`.

The other reports in `audit-output` support the backlog. They are research, not
permission for external actions. No reference implementation code was copied.

## 3. Checkpoint Map

| Checkpoint | State | Evidence |
| --- | --- | --- |
| C0–C6 | Complete | Approved design through full local proof checkpoint `3fac4c6` |
| Audit A | Complete | Approach A focus suite: 43 passed |
| Audit B | Complete | Venue quote/contract provenance and canonical manifests at `eea74d0` |
| Audit C | Complete | Typed Alpaca outcomes and ambiguity faulting at `ae6eb46` |
| Audit D | Complete | Pure monotonic order lifecycle integrated at `3c2e361` |
| Audit E | Next | Exact strategy-owned position ledger and restart-safe exits |
| Audit G | Pending | Immutable release/account/evidence manifest |
| Audit H Must | Pending/gated capture | Read-only CLI proof tool; genuine capture needs executable/profile/account proof |
| Audit F Should | Deferred | Optional stream only after polling correctness and paper soak |
| Audit I | Partially gated | SBOM/notices/deploy hardening local; license holder/year external |
| Audit J | Pending | Evidence-derived docs/dashboard/demo after schemas stabilize |
| Audit K | Offline pending | Final clean release proof; order/deploy/soak parts external |
| C7–C9 | External | Credentials/IDs/order authority, VPS, deployment/soak, activation |

Do not redo completed work. Re-run a completed focus only when a later task
touches that boundary.

## 4. Audit B — Candidate Provenance (`eea74d0`)

- `glassbox/candidates.py` validates active/tradable contracts, coherent fresh
  quotes, Decimal limits, content identity, canonical manifests, and selector
  receipts.
- `glassbox/option_data.py` reuses one Alpaca option client, requests the
  explicit indicative feed, and paginates exact active call/put contract
  requests for one underlying/expiry.
- `TradePlan` carries backward-compatible provenance/rule/schema fields;
  provenance-backed IDs derive from executable content.
- Event-vol construction refuses unverified legs.
- The bounded selector journals candidate-set and prompt/model/input/output
  receipt hashes.
- Invalid market data produces a safe journal refusal and no candidate.

Task B focus evidence: 56 passed. The full exact-lock suite at that checkpoint
was 257 passed.

## 5. Audit C — Typed Outcomes (`ae6eb46`)

- Verified HTTP 404 is the only client-order lookup result interpreted as
  absent.
- Auth/validation failures are typed terminal rejections.
- Rate limit, server, timeout, connection, and malformed-response failures are
  typed unknown outcomes rather than absence.
- Submit/cancel/close mutations are attempted once; bounded jittered retries
  are restricted to read calls.
- A submit is reconciled only through its original deterministic client order
  ID. Unresolved ambiguity raises `ExecutionStateUncertain`, a durable
  `StateError` that the scheduler fault wrapper latches.
- Journal evidence records safe error types/status codes, not raw upstream
  messages.

Task C evidence: 42 broker/executor tests, 83 boundary-regression tests, and
267 exact-lock full tests passed.

## 6. Audit D — Exact Lifecycle (`3c2e361`)

- `glassbox/order_lifecycle.py` is a pure reducer for accepted, pending, new,
  partial, cancel-pending, filled, canceled, expired, rejected, replaced, and
  other documented terminal Alpaca states.
- Cumulative fill cannot decrease or exceed requested quantity.
- Cancel request/pending-cancel is explicitly nonterminal.
- A late fill observed during cancellation reduces successor quantity.
- Replaced orders must identify their successor.
- Duplicate/out-of-order generated sequences preserve monotonic fill and
  terminality.
- Unknown statuses and unproven cancellation fail closed.
- The executor uses the reducer for every working-order observation and will
  not return across cancellation uncertainty.

Task D evidence: 48 lifecycle/executor/broker tests and 92
lifecycle-through-kernel regression tests passed. Ruff, mypy (34 source
files), and diff hygiene passed. The final isolated Python 3.12 exact-lock full
suite passed all `276` tests.

Final checkpoint verification also passed: 63-file Ruff format check, Ruff
lint, mypy across 34 production source files, dependency integrity, 33 kernel
tests, the 13/13 crash-recovery drill, environment parity for all nine
variables, compileall, 12 dashboard tests, and `git diff --check`.

## 7. Exact Local Commit Ledger

| Commit | Purpose |
| --- | --- |
| `5d807ee` | Approved design and original TDD plan |
| `6c58dac` | Explicit expected-account binding |
| `3e6e7c3` | Terminal cancellation confirmation |
| `ff29e4b` | Bounded exact development venue proof |
| `75cca48` | Residual/overlapping order prevention |
| `8b5b9fd` | Deterministic intent and ambiguous-submit reconciliation |
| `9810390` | Atomic fail-closed safety state |
| `6c0ea74` | Bounded option candidate selection |
| `a225c80` | Options-only scored composition |
| `973edf8` | Exact locks, verification, reviewed-SHA deployment harness |
| `3fac4c6` | Truthful artifacts and C6 proof checkpoint |
| `e3d0e85`, `11d6a69` | Prior handoff records |
| `3856f26` | Exhaustive reference-audit backlog integration |
| `eea74d0` | Candidate venue provenance and canonical manifests |
| `ae6eb46` | Typed Alpaca failure/ambiguity semantics |
| `3c2e361` | Pure monotonic order lifecycle integration |

## 8. Next Task — Exact Position Ownership

Audit Task E must land as its own state-schema rollback boundary:

- create `glassbox/position_ledger.py` with schema version, account/environment,
  plan/leg identity, signed expected quantity, deterministic entry/exit client
  ID families, cumulative confirmed fills, generation, checksum, and last
  reconciliation time;
- rebuild expected quantity idempotently from durable intents and confirmed
  fills after restart;
- reconcile exact per-contract owned quantity against venue positions and open
  orders;
- fail closed on unknown/foreign/missing/corrupt exposure;
- replace `PositionManager` symbol-wide `close_position` with exact-quantity,
  intent-first exits through the same typed submit/cancel/lifecycle engine;
- report flat only after exit orders are terminal and venue quantity is zero;
- add a cross-platform singleton state-directory lock; and
- extend the crash drill for ledger/state rename/fsync/restart cases.

Required focus:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_position_ledger.py tests/test_manage.py tests/test_state.py tests/test_execute.py -q
.venv\Scripts\python.exe tools/crash_drill.py
```

Use strict TDD and do not downgrade a state directory in place.

## 9. Remaining Authority and Legal Gates

Still absent:

- development Alpaca key/secret and distinct expected dev/scored account IDs;
- explicit authorization for the one hard-capped `$50.00` development paper
  order proof;
- VPS/SSH destination, authentication, and secret-delivery path;
- authority to deploy, soak, submit, merge, or activate scored mode;
- organizer direction for any composite strategy (otherwise options-only); and
- confirmed project copyright holder/year for a root MIT license.

Do not invent license identity. Do not claim CLI/MCP proof without captured
artifacts. Do not install or expose a write-capable Alpaca MCP surface to the
scored AI context.

## 10. Safe Continuation

```powershell
git status --short --branch
git rev-parse HEAD
git log --oneline --decorate -20
Get-Content IMPLEMENTATION-PLAN.md

.venv\Scripts\python.exe -m pytest tests/test_order_lifecycle.py tests/test_execute.py tests/test_broker.py -q
uv run --isolated --python 3.12 --with-requirements requirements-dev.lock -- python -m pytest -q
```

The Windows host has intermittently printed native access-violation diagnostics
during imports or pytest cleanup under both installed and uv Python runtimes.
Pytest has continued, completed assertions, and exited zero. Record the noise
honestly; do not count it as success or silently treat it as a failed test.

## 11. Do Not Regress

- Exact account ID, literal paper endpoint, and options-only scored policy.
- Candidate ID-or-null AI authority and exact original-object retrieval.
- Decimal, quote-derived executable prices and content-addressed manifests.
- Intent before mutation; one mutation attempt; deterministic reconciliation.
- Cancellation request is nonterminal; unknown state latches new risk.
- No symbol-wide proof cleanup or strategy-owned exit.
- Atomic fail-closed state; no blind corruption recovery.
- Exact locks and exact reviewed SHA deployment.
- No order/deployment/submission/performance/MCP claims without evidence.

## 12. Immediate Move

After verifying the pushed `utk` branch points at the final handoff commit,
continue locally or in a new task from Audit Task E. Keep the branch and state
schema boundaries reviewable; do not combine E with the release manifest or
public evidence rewrite.
