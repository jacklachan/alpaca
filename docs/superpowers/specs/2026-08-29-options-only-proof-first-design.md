# Options-Only Proof-First Safety Design

**Status:** Approved for implementation on 2026-08-29. This document records
Approach A from the review handoff; it does not reopen that product decision.

## Goal

Make Glassbox's scored path an options-only, proof-first agent whose AI can
choose or abstain only among deterministic, already-priced candidates, while
making every broker write restart-safe, bounded, and reconcilable.

## Scope and non-goals

This change hardens the local implementation and its release artifacts. It does
not place a live order, push or merge a branch, submit the project, or deploy a
VPS. Those actions remain externally gated by credentials, explicit account
numbers, a reviewed commit SHA, and a VPS target.

The scored account runs only the options strategy. The existing equity and
crypto strategies remain available for offline tests and historical context,
but the scheduler cannot register them when `ALPACA_ENV=scored`. Crypto is used
only by the separately invoked development-account venue check. There is no MCP
claim unless an MCP transport is actually added; the implemented integrations
are the Alpaca Trading/Data APIs, Anthropic's API, and local CLI tools.

## Requirements

### R1 — Bind credentials to account identity

`ALPACA_EXPECTED_DEV_ACCOUNT_ID` and `ALPACA_EXPECTED_SCORED_ACCOUNT_ID` are
required non-secret configuration. Broker startup reads the account from
Alpaca and compares the returned `account_number` to the explicit ID for the
selected environment. A missing ID, equal dev/scored IDs, or mismatch is fatal
before any order path can run. `ALPACA_ENV` selects a policy; it is not proof of
identity.

### R2 — Make the live venue check bounded and reversible

Trade mode is development-account only and has a hard USD ceiling of `$50.00`.
It refuses a non-positive or larger CLI value, a non-empty position baseline,
or any pre-existing open order. The entry is a test-owned BTC/USD quantity with
a deterministic client-order namespace. On timeout or partial fill, the entry
remainder is canceled and observed in a terminal state before cleanup begins.
Cleanup submits a sell for exactly the final entry fill; it never calls a
symbol-wide close. Success requires the entry and exit orders to be terminal,
no test-owned open order, and the exact original position quantity. Any timeout,
cancel uncertainty, residual position, or cleanup exception returns nonzero.

### R3 — Confirm cancellation before replacement

A cancel request is not a cancellation. The broker exposes a bounded
`cancel_and_confirm` operation that requests cancellation and polls the same
client order until it reaches a terminal state. Execution copies the terminal
fill quantity and average price into its leg ledger before banking it. A
replacement is submitted only after terminal cancellation and only for the
true remaining quantity. If terminal state cannot be proven, the engine does
not replace and reports manual intervention.

### R4 — Leave no residual entry order

Every incomplete option, equity, or crypto entry is canceled and confirmed
before execution returns. A partial single-order fill may be reported as an
owned partial position only after its residual is terminal; an uncertain
residual makes the result unsuccessful. Option failure cleanup first terminally
cancels every working leg, refreshes final fills, then unwinds the known filled
quantity. No success result may coexist with a live entry remainder.

### R5 — Reconcile ambiguous submissions

Plan IDs and client order IDs are deterministic for the same opportunity.
Immediately before a submit, the durable journal records the full submission
intent, including plan ID, client order ID, symbol, side, quantity, price, and
instrument. If submit raises, execution looks up that client order ID for a
bounded period. An observed broker order is adopted as the submission result;
absence or read uncertainty is reported as ambiguous and is never followed by
a second ID or blind retry.

### R6 — Persist safety state atomically and fail closed

JSON safety state is written to a same-directory temporary file, flushed,
`fsync`ed, atomically replaced, and followed by a best-effort directory `fsync`
where the platform supports it. Missing files have explicit initial values.
Malformed or structurally invalid files raise `StateCorrupt`; they never become
an empty target map or positioned set. Write failures raise `StateWriteError`.
The scheduler latches a local state fault and blocks new entries while still
allowing broker reconciliation and position management.

### R7 — Bound AI to selection or abstention

Deterministic option strategies own the allowed underlyings, OCC contract
symbols, quantities, limits, max loss, exits, and evidence. They emit immutable
`TradePlan` candidates with stable IDs. The model receives a numbered list of
those candidates and may return exactly one candidate ID or abstain. Unknown
IDs, malformed output, timeout, or missing credentials mean abstention. The
selected object is retrieved from the original candidate map—not rebuilt from
model output—and still passes through `RiskKernel.review()` and the single
execution path. `ThesisLayer.propose()` is removed.

### R8 — Enforce an options-only scored path

Scored construction registers only deterministic option candidate generators.
The scored scheduler ignores and journals any non-option candidate as a policy
refusal. At most one selected option candidate is submitted per selection
cycle. Development connectivity proof remains an explicit CLI action rather
than an autonomous crypto sleeve.

### R9 — Deploy an immutable release

`deploy/setup.sh` requires a full 40-character reviewed commit SHA. It fetches
and checks out that exact object, verifies `HEAD`, and installs from a committed
fully pinned transitive lock file. It never pulls a moving default branch.
Runtime tooling exposes one local verification command covering formatting,
lint, type checking, tests, kernel tests, crash drill, compilation, and
dashboard body checks. Deployment still requires a user-provided VPS/SSH
target and is not performed by this implementation run.

### R10 — Keep every public claim true

README, architecture, dashboard, demo script, social copy, and handover text
describe the actual options-only scored path, bounded candidate selection,
Alpaca API/CLI integration, durable reconciliation, and the unperformed live
and deployment gates. Claims such as unrestricted model proposals, MCP usage,
active equity/crypto scored sleeves, completed venue execution, or completed
deployment are removed unless backed by code and evidence.

## Components and interfaces

### `glassbox/state.py`

- `StateCorrupt(RuntimeError)` identifies unreadable or invalid durable state.
- `StateWriteError(RuntimeError)` identifies a failed durable replacement.
- `read_json(path, *, default, validate)` returns the validated value only;
  missing files return the explicit default.
- `atomic_write_json(path, value)` implements temp → flush → fsync → replace.

The persistence helper owns mechanics only. Callers own schemas: kill-switch
records, exit-target maps, and positioned-day records.

### `glassbox/broker.py`

- `assert_ready()` proves returned account identity before other readiness
  checks.
- `cancel_and_confirm(order_id, client_order_id, *, timeout, poll_seconds)`
  returns the final terminal broker order or raises `OrderStateUncertain`.
- Exact-quantity exits use normal `submit()` with a sell side and a dedicated
  deterministic client order ID; symbol-wide `close_position()` remains only
  for production position-management policy, never the live check.

### `glassbox/execute.py`

- `_submit_with_reconciliation(...)` journals intent, submits once, then adopts
  an order found under the same client order ID after an exception.
- `_cancel_leg(...)` confirms terminal status and refreshes the leg ledger.
- `_reprice(...)` cannot submit until `_cancel_leg(...)` succeeds.
- `_execute_single(...)` cancels and confirms every incomplete remainder.

### `glassbox/schema.py` and `glassbox/ids.py`

`TradePlan` is frozen after validation. `stable_plan_id(namespace, *parts)`
creates a short deterministic ID. Deterministic strategies pass a semantic
opportunity key; a schema fallback hashes canonical plan content when callers
omit an ID.

### `glassbox/thesis.py` and `glassbox/scheduler.py`

`ThesisLayer.select(candidates, state, journal)` returns the exact selected
`TradePlan` or `None`. The scheduler collects option candidates, calls select,
journals selection or abstention, then calls its existing kernel-first method.
State corruption stops startup; a later persistence write fault blocks new
entries and is visible in journal/Discord while management continues.

## Data flows

### Normal scored selection

1. Reconcile account, positions, orders, market clock, and prices from Alpaca.
2. Manage existing positions mechanically.
3. Generate immutable, pre-priced option candidates deterministically.
4. Ask the bounded model for one existing candidate ID or abstention.
5. Retrieve the exact original candidate object.
6. Run all deterministic kernel invariants.
7. Journal submission intent durably.
8. Submit once, reconcile ambiguity by client order ID, and monitor fills.
9. Confirm every cancellation before replacement or return.

### Live development proof

1. Prove the returned account equals the expected dev account ID.
2. Prove positions and open orders are empty and notional is within `$50.00`.
3. Submit one BTC/USD entry and reach a terminal entry state.
4. Submit a sell for exactly the entry fill and reach a terminal exit state.
5. Re-read positions/open orders and prove exact baseline restoration.
6. Return zero only when every proof succeeds.

## Failure policy

- Unknown account identity: fatal before orders.
- Model unavailable or malformed: abstain, no fallback invention.
- Submit response unknown: reconcile only by the same client order ID.
- Cancel state unknown: no replacement and no success result.
- Durable state corrupt at startup: fatal.
- Durable state write fails while running: latch state fault; manage and
  reconcile existing risk, but create no new entries.
- Live-check cleanup incomplete: nonzero exit and explicit manual-action text.

## Verification strategy

Every behavior change follows red → green → refactor. Fakes model late fills,
partial fills, terminal cancellation, missing order lookups, ambiguous submit
acceptance, corrupt JSON, failed replace, unknown model IDs, and scored sleeve
registration. Focused tests run after each slice. Final evidence includes the
full pytest suite, the 32 kernel tests, `pip check`, `compileall`, crash drill,
dashboard response-body checks, formatting/lint/type gates, and the repository's
Linux CI command. No live test or deployment is represented as complete.

## External gates

The minimal live venue proof requires dev-only paper credentials plus both
explicit expected account IDs. Scored credentials are not used for that proof.
Deployment requires a user-provided VPS/SSH target and the exact reviewed SHA.
Neither gate can be substituted with mocks, environment labels, or prose.
