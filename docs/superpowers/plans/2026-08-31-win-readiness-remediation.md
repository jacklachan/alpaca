# Glassbox Win-Readiness Remediation Plan

> **Base:** create the implementation branch from
> `c45b23fdf6cb51be1092ea2b0c76d1e7f0128c69` (`origin/review`). Do not merge
> `utk`; preserve it as an audit rollback point.

**Goal:** turn the improved teammate build into a safe, evidence-complete,
options-only Alpaca hackathon release without expanding AI authority or adding
unproven product scope.

**Method:** strict TDD, one safety boundary per commit, focused tests after each
slice, then the full release gate on Python 3.12. No order, deployment, merge,
default-branch change, or submission is authorized by this plan.

## Checkpoint R0 — Freeze the new base and encode the audit

### Tasks

1. Create a new local implementation branch from `origin/review`.
2. Confirm `HEAD == c45b23f...`, a clean tree, and the successful CI run.
3. Preserve `utk` and record the divergence; do not cherry-pick its competing
   broker/lifecycle implementations wholesale.
4. Add this audit and plan as the first documentation-only checkpoint.

### Verification

```powershell
git status --short --branch
git rev-parse HEAD
git merge-base utk origin/review
git rev-list --left-right --count utk...origin/review
git diff --check
```

### Exit condition

One clean, reviewable branch is based on the teammate build and the audit is in
the repository. No runtime behavior changes in this checkpoint.

## Checkpoint R1 — Make scored release gating mandatory

### Tests first

Add or modify:

- `tests/test_release.py`
- `tests/test_main.py` or a new `tests/test_startup.py`
- `tests/test_claims.py`

Cover:

1. `ALPACA_ENV=scored` refuses when the release gate variable is absent or `0`.
2. A scored release refuses unless an explicit full
   `GLASSBOX_APPROVED_COMMIT_SHA` equals current `HEAD`.
3. It refuses a manifest with any required pending gate.
4. It refuses required verification results that are missing, skipped, stale,
   wrong-account, or tied to another commit/lock/policy hash.
5. Dev dry-run remains possible without scored release evidence and cannot
   mutate unless the existing explicit live-check command is used.
6. The endpoint comparison is exact after URL normalization, not a substring.

### Implementation

Modify:

- `main.py`
- `glassbox/release.py`
- `.env.example`
- `docs/OPERATIONS.md`

Required behavior:

- scored mode always executes the release gate;
- an approved SHA is an input, not something the running checkout self-approves;
- the manifest binds commit, tree cleanliness, lock hashes, policy/candidate
  versions, account ID suffix, exact paper endpoint, proof artifact hashes,
  and evidence timestamps;
- required pending gates fail scored startup;
- release verification is separate from offline development verification.

### Focused gate

```powershell
.venv\Scripts\python.exe -m pytest tests/test_release.py tests/test_startup.py tests/test_claims.py -q
.venv\Scripts\python.exe -m mypy glassbox/release.py main.py
```

### Commit boundary

`fix: require an approved evidence-complete scored release`

## Checkpoint R2 — Make ledger fills idempotent

### Tests first

Modify `tests/test_position_ledger.py` and extend the crash drill.

Cover:

1. Applying the same cumulative entry fill twice changes quantity once.
2. The same rule holds for exit fills.
3. A higher cumulative observation applies only its delta.
4. A lower cumulative observation is ignored or faults according to the
   lifecycle contract; it never subtracts a confirmed fill.
5. Aggregate fill cannot exceed requested quantity.
6. Restart/replay rebuilds exactly the same ledger.
7. Corrupt or old-schema fill maps fail closed; migration is explicit.

### Implementation

Modify:

- `glassbox/position_ledger.py`
- `glassbox/order_lifecycle.py` only if one shared cumulative-fill type is
  needed
- `tools/crash_drill.py`

Persist per-order cumulative entry and exit fills. Update signed ownership by
`new_cumulative - previous_cumulative`, never by blindly adding the latest
snapshot.

### Focused gate

```powershell
.venv\Scripts\python.exe -m pytest tests/test_position_ledger.py tests/test_order_lifecycle.py -q
.venv\Scripts\python.exe tools/crash_drill.py -n 8 --seed 1
```

### Commit boundary

`fix: make confirmed fill replay idempotent`

## Checkpoint R3 — Unify entry, exit, replacement, and unwind submission

### Tests first

Modify:

- `tests/test_execute.py`
- `tests/test_manage.py`
- `tests/test_position_ledger.py`
- `tests/test_scheduler.py`
- `tests/test_broker.py`

Add scripted broker cases for:

1. exit accepted then response timeout; lookup adopts the original ID;
2. exit timeout and lookup unavailable; state latches uncertain and no second
   mutation occurs;
3. incomplete second leg after first-leg fill;
4. late fill during entry cancel;
5. partial exact unwind;
6. unwind accepted then response timeout;
7. unrelated same-symbol/account exposure is never closed;
8. executor reports flat only after terminal unwind and exact zero venue
   quantity;
9. scheduler ledger reflects entry minus unwind, including crash/restart;
10. no residual entry or unwind order remains live on return.

### Implementation

Refactor the smallest shared primitive in:

- `glassbox/execute.py`
- `glassbox/manage.py`
- `glassbox/broker.py`
- `glassbox/ids.py`
- `glassbox/position_ledger.py`
- `glassbox/scheduler.py`

The primitive must:

1. persist intent before mutation;
2. use a deterministic client order ID family and attempt number;
3. submit exactly once;
4. reconcile any exception by client order ID;
5. fold every observation through the lifecycle reducer;
6. cancel and confirm working residuals;
7. update the ledger idempotently;
8. prove terminal order state and exact venue quantity.

Delete symbol-wide `close_position` from option unwind and every scored exit.
It may remain only in clearly separate development tooling whose baseline and
owned quantity are proven.

### Focused gate

```powershell
.venv\Scripts\python.exe -m pytest tests/test_execute.py tests/test_manage.py tests/test_position_ledger.py tests/test_scheduler.py tests/test_broker.py -q
```

### Commit boundary

`fix: reconcile exact option exits and incomplete-leg unwinds`

## Checkpoint R4 — Enforce one scheduler per state directory

### Tests first

Add startup-level tests, not only `ProcessLock` unit tests:

1. first scored process acquires the lock before building order-capable loops;
2. second process exits nonzero without contacting Alpaca mutation methods;
3. normal shutdown releases the lock;
4. a killed owner is reclaimed only after PID death is proven;
5. corrupt or permission-denied lock fails closed;
6. the lock covers the entire scheduler lifetime, including `--once`.

### Implementation

Acquire `ProcessLock` in the top-level runtime around build/run using a lock
path derived from the authoritative state directory. Keep deterministic broker
IDs as the primary external idempotency mechanism; the process lock is defense
in depth.

### Commit boundary

`fix: enforce singleton ownership of scored state`

## Checkpoint R5 — Remove false absence and Decimal claims

### Tests first

Modify `tests/test_broker.py`:

1. only a response with verified HTTP 404 maps to `OrderNotFound`;
2. status-less `APIError("not found")` remains unknown;
3. auth, validation, rate limit, server, timeout, and transport behavior is
   unchanged;
4. order request serialization preserves exact tick price and fractional
   quantity at the pinned `alpaca-py` boundary;
5. no executable value round-trips through binary float.

### Implementation

Remove message-only absence classification. Pass Decimal or exact strings using
the type supported by the pinned SDK, and verify the outbound request model.
Update README claims only after the tests pass.

### Commit boundary

`fix: require verified absence and exact executable decimals`

## Checkpoint R6 — Make verification semantic and release-strict

### Tests first

Modify `tests/test_verification.py`, `tests/test_thesis.py`, and
`tests/test_candidates.py`.

Cover:

1. selection with no offered set fails;
2. selection of an ID offered only in an older cycle fails;
3. selected cycle/run ID and candidate-set digest must match;
4. selector receipt hashes must recompute from the captured bounded envelope;
5. absent `model_output` cannot pass an AI-field check;
6. any field beyond exact ID/null plus rationale fails schema validation;
7. release mode fails on each required skip;
8. development mode reports incomplete without claiming `VERIFIED`;
9. a hand-written proof file with `complete: true` fails semantic validation;
10. stale, wrong-account, wrong-commit, wrong-tool-version, and wrong-command
    proof files fail.

### Implementation

Modify:

- `glassbox/thesis.py`
- `glassbox/candidates.py`
- `glassbox/verification.py`
- `tools/verify_submission.py`
- `dashboard/app.py`

Add a cycle/run ID and candidate-set hash to every related event. Preserve a
redacted canonical selector response sufficient to validate exact schema and
receipt hashes. Expose two explicit verdicts:

- `OFFLINE CHECKS CLEAN — RELEASE EVIDENCE INCOMPLETE`;
- `RELEASE VERIFIED` only when every required artifact passes.

### Commit boundary

`fix: verify candidate authority per cycle and fail release on missing proof`

## Checkpoint R7 — Repair the official Alpaca CLI proof path

### Tests first

Modify `tests/test_proof_tools.py` with fixtures matching the pinned official CLI:

1. exact `version` and `doctor` commands accept documented plain text;
2. account/config/clock/contracts/chain/orders/positions commands use the
   installed CLI's actual syntax and `--quiet` structured output;
3. missing or blank expected account ID refuses before spawning;
4. returned account ID must equal expected ID structurally;
5. partial environment credentials fail rather than falling through to a
   profile;
6. binary version, release, and checksum are captured and verified;
7. option-contract request is SPY, active, and read-only;
8. option-chain proof is present;
9. any mutating token still refuses before spawn;
10. any drift from the captured CLI help/schema makes the proof incomplete.

### Implementation

Modify `tools/capture_alpaca_proof.py` and operations docs. Use the command set
recorded in `OFFICIAL-ALPACA-REPOS.md` only after reconfirming it against the
installed pinned binary:

```text
alpaca version
alpaca doctor
alpaca account get --quiet
alpaca account-config get --quiet
alpaca clock --quiet
alpaca option contracts --underlying-symbols SPY --status active --quiet
alpaca data option chain --underlying-symbol SPY --quiet
alpaca order list --status all --quiet
alpaca position list --quiet
```

Do not use CLI for autonomous execution.

### Commit boundary

`fix: capture pinned official Alpaca CLI proof`

## Checkpoint R8 — Keep Greeks supplementary, but make the math and evidence true

### Tests first

Modify `tests/test_greeks.py` and `tests/test_event_vol.py`:

1. OTM call and put strike gaps are included in breakeven distance;
2. both upper and lower breakevens are tested;
3. missing venue Greeks remains a recorded supplementary absence and does not
   block a candidate that passes primary gates;
4. authentication, transport, malformed schema, and genuine “no surface” are
   recorded as distinct typed outcomes;
5. a present unhealthy surface still vetoes the candidate;
6. README and write-up describe the same policy as the code.

### Implementation

Modify:

- `glassbox/greeks.py`
- `glassbox/strategies/event_vol.py`
- `glassbox/option_data.py`
- `README.md`
- `docs/WRITEUP.md`

Do not turn an optional surface into a primary outage. Correct the formula,
typed evidence, and public description.

### Commit boundary

`fix: calculate true strangle breakevens and report surface gaps honestly`

## Checkpoint R9 — Make primary data failures observable

### Tests first

Cover expiry quotes and chain acquisition for success, empty result, stale data,
auth failure, rate limit, timeout, malformed schema, and unknown failure. Every
path must create a typed stage event with source, as-of time, age, and impact.
No candidate remains a valid outcome, but “no opportunity” and “data failure”
must be distinguishable.

### Implementation

Modify `glassbox/strategies/event_vol.py`, `glassbox/option_data.py`, journal
schemas, dashboard projections, and tests. Keep retries limited to idempotent
reads with bounded backoff.

### Commit boundary

`fix: record typed option-data abstention evidence`

## Checkpoint R10 — Harden proof transport and supply chain

### Required before an MCP claim

- one long-lived MCP reader/dispatcher rather than one daemon reader per request;
- bounded message queue keyed by JSON-RPC request ID;
- continuously drained stderr with redaction and size cap;
- clean timeout/session teardown tests;
- exact official server version/SHA and discovered surface;
- mandatory exact account equality.

CLI already satisfies the event's lower-risk integration path. Defer official
MCP proof if this checkpoint risks the deadline; remove any “integration” claim
and retain “client built, official run pending.”

### Supply-chain additions

Add without destabilizing the runtime:

- hashed release requirements or a reproducible wheelhouse manifest;
- CycloneDX or SPDX SBOM;
- dependency vulnerability scan with reviewed exceptions;
- CodeQL or a minimal Python security scan;
- artifact hashes for dashboard/demo evidence.

Do not refactor all large files before the deadline. Split only the safety files
touched above when a clean boundary reduces review risk.

## Checkpoint R11 — Full offline release candidate

### Required commands

Run on clean Python 3.12 from `requirements-dev.lock`:

```bash
make verify
git diff --check
git status --short --branch
```

Also require:

- every P0/P1 regression test;
- a clean secret scan;
- release-mode verifier fails with a clear “external proof missing” verdict,
  not `VERIFIED`;
- no release artifact contains a credential;
- default scored composition remains options-only;
- trade stream remains disabled.

### Review checkpoint

Request an independent code review focused on order ambiguity, late fills,
ledger replay, release evidence semantics, and public claims. Fix all P0/P1
findings before declaring a release candidate.

## External Gate X1 — Real read-only account and CLI proof

**Needs from the user/team:**

- development paper API key and secret;
- explicit expected dev account ID;
- distinct expected scored account ID;
- pinned Alpaca CLI executable/release/checksum;
- confirmation of the canonical public project/submission identity.

Run the read-only broker check and repaired CLI proof. Preserve redacted output,
timestamps, hashes, commit SHA, account suffix, positions, open orders, active
SPY option contracts, and option-chain evidence.

No order is authorized by this gate.

## External Gate X2 — Minimal development venue mutation proof

Only after X1 is clean and the user explicitly authorizes the one bounded paper
write:

1. prove dev account identity and a clean baseline;
2. cap notional at or below $50;
3. submit once with deterministic identity;
4. exercise partial fill/cancel/cleanup paths as available;
5. close only test-created quantity;
6. prove terminal orders, exact zero position, and no residual working order;
7. retain the complete redacted journal and CLI reconciliation.

Any uncertainty returns nonzero and blocks scored activation.

## External Gate X3 — Deploy exact SHA and soak

**Needs:** VPS/SSH target, secret-delivery method, reviewed 40-character SHA,
and explicit deployment authorization.

Deploy only the reviewed SHA and runtime lock. Run dry-run identity checks,
health checks, restart/crash recovery, scheduler singleton checks, dashboard
read-only checks, and the polling-based soak. Do not wire the trade stream until
polling/restart correctness is proven on paper and the stream-specific soak is
clean.

## External Gate X4 — Scored account activation and forward evidence

**Needs:** fresh dedicated $100,000 paper account, correct options permissions,
X1–X3 proof, and explicit activation direction.

At activation:

- bind exact scored account ID;
- verify zero unmanaged positions and orders;
- verify release manifest and approved SHA;
- start options-only strategy;
- preserve account equity/portfolio history directly from Alpaca;
- show every abstention as a successful bounded decision, not a failure;
- monitor hard caps and stop taking risk by the verified measurement cutoff.

The objective is a trustworthy positive equity result, not trade count. Do not
force a trade merely to create P&L evidence.

## Checkpoint R12 — Canonical submission package

After real evidence exists:

1. choose one canonical product name and repository;
2. make the submitted GitHub URL resolve to the reviewed release;
3. update README, write-up, dashboard, demo, deck, video, cover, and event text
   from the same evidence bundle;
4. show the 60-second proof chain:
   candidate snapshot → bounded ID/null selection → 13 kernel checks → durable
   intent → Alpaca order/fill/cancel → exact reconciliation → account equity;
5. include a plain-language “what AI cannot do” slide;
6. show CLI proof and the exact account suffix;
7. disclose that Greeks are supplementary and the trade stream is disabled
   unless its soak completed;
8. remove every stale test count, atomic-MLEG claim, MCP claim, deployment
   claim, and P&L number not tied to the release artifact;
9. run release verification against the final public URLs and artifact hashes;
10. obtain explicit user confirmation before submission.

## Winning narrative

Lead with one sentence:

> Glassbox lets AI choose only among trades deterministic code has already
> priced and bounded—and proves every transition against Alpaca afterward.

Then demonstrate, rather than assert:

- **Technology:** immutable candidates, strict AI schema, risk invariants,
  restart-safe exact execution, real Trading API and CLI evidence.
- **Originality:** proof-carrying decisions instead of persona theater or
  LLM-authored portfolios.
- **Execution:** deployed read-only dashboard and one-command evidence bundle.
- **Performance:** total Alpaca account equity over the official forward window,
  with sample-size caveats and no synthetic fill claims.

That is the shortest credible path from the current 47/100 state to a
competitive submission.
