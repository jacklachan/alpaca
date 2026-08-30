# Alpaca Hackathon — Options-Only Proof-First Master Plan

**Status date:** 2026-08-30

**Repository:** `C:\Users\Utkarsh\Desktop\Project\Trading\alpaca`

**Branch:** `review`

**Approved direction:** Approach A — proof-first, options-only scored execution with bounded AI

This is the execution-level status document for the approved design. The detailed
test-by-test plan remains in
`docs/superpowers/plans/2026-08-29-options-only-proof-first.md`; the governing
architecture is in
`docs/superpowers/specs/2026-08-29-options-only-proof-first-design.md`.

## 1. Product Contract

The scored account runs one deliberately narrow path:

1. Deterministic strategy code discovers option opportunities.
2. Deterministic code chooses the contracts, quantity, maximum loss, entry
   limits, exits, and evidence.
3. Bounded AI may select exactly one supplied candidate ID or abstain.
4. The selected, original candidate object passes unchanged through the
   deterministic risk and execution kernel.
5. The executor journals intent, reconciles ambiguous submissions, confirms
   terminal cancellation before replacement, and cleans up residual orders.

The scored path does not run the equity or crypto sleeves. Crypto remains a
separate, development-account connectivity proof only.

## 2. Non-Negotiable Safety Boundaries

- [x] Credentials are bound to explicit expected Alpaca account IDs.
- [x] Development and scored account IDs must be distinct.
- [x] No cancel/reprice replacement occurs before terminal cancellation is
  observed.
- [x] Partial and timed-out entries cannot leave an accepted residual GTC order
  without surfacing a failure.
- [x] Plan and client-order identities are deterministic across restart.
- [x] Submission intent is durable before the broker call and ambiguous submits
  reconcile by the same client-order ID.
- [x] Safety state uses atomic write, file fsync, replace, and directory fsync
  where supported.
- [x] Corrupt or unwritable safety state fails closed.
- [x] AI cannot invent a contract, size, price, exit, or complete trade plan.
- [x] The scored scheduler accepts option candidates only.
- [x] Deployment requires a full reviewed 40-character commit SHA and exact
  dependency locks.
- [ ] No live venue proof until development credentials and both explicit
  expected account IDs are supplied.
- [ ] No deployment until a VPS/SSH target is supplied.
- [ ] No scored activation until the venue proof, deployment, and soak gates
  have succeeded.

## 3. Checkpoint Map

| Checkpoint | Scope | State | Acceptance evidence |
| --- | --- | --- | --- |
| C0 | Baseline, design, and detailed plan | Complete | Clean `f2d25c7` baseline; design and implementation plan committed |
| C1 | Account and live-execution safety | Complete | Account mismatch, notional, dirty-baseline, partial-fill, cancellation-race, exact-cleanup tests |
| C2 | Restart and durable-state safety | Complete | Stable IDs, intent journal, ambiguous-submit reconciliation, atomic fail-closed state tests |
| C3 | Bounded AI and options-only scored policy | Complete | Selection/abstention and scored scheduler policy tests |
| C4 | Reproducible release and deploy discipline | Complete | Exact locks, quality gates, SHA-pinned deploy harness tests |
| C5 | Truthful product, UI, demo, and practice artifacts | Complete | Dashboard assertions, claim scan, read-only practice regression, deeply immutable candidate test |
| C6 | Full local verification and hostile self-review | Complete | Fresh verification matrix recorded below; committed at `3fac4c6` |
| A | Preserve and attest Approach A boundary | Complete | Audit focus suite: 43 passed on 2026-08-30 |
| B | Candidate provenance and canonical manifest | Complete | 56 focused and 257 full locked tests; committed rollback boundary |
| C | Typed Alpaca failures and ambiguity discipline | Pending | Begins only after B is committed |
| D | Pure order-lifecycle reducer | Pending | Begins only after typed outcomes in C |
| E | Strategy-owned ledger and restart-safe exits | Pending | Begins only after lifecycle semantics stabilize |
| G | Release/account/evidence manifest | Pending | Built on stable candidate, lifecycle, and state schemas |
| H | Genuine read-only Alpaca CLI proof | Pending external executable/profile proof | No CLI integration claim until captured evidence exists |
| F | Optional trade-update stream with REST healing | Deferred Should | Polling correctness and paper soak remain prerequisites |
| I | Dependency, legal, and deployment closure | Partially gated | License holder/year requires user confirmation; no deployment authorized |
| J | Evidence-derived docs, dashboard, and demo | Pending | Regenerate only from completed evidence schemas |
| K | Clean verification, soak, and release gate | Offline portion pending | Credentialed/order/deploy portions remain external gates |
| C7 | Minimal development live venue proof | External gate | Needs dev-only credentials and explicit expected dev/scored account IDs |
| C8 | Exact-SHA VPS deployment and soak | External gate | Needs VPS/SSH target after C7 and reviewed SHA |
| C9 | Scored-account activation | Pending | Requires C7–C8 evidence and explicit user direction |

## 4. Detailed Work Plan and Progress

### C0 — Baseline, Design, and Planning

- [x] Verify `review` at `f2d25c7`, matching `origin/review`, with a clean tree.
- [x] Confirm the isolated baseline: 166 tests, 32 kernel tests, dependency,
  compile, dashboard, and CI-equivalent checks.
- [x] Record the approved options-only, proof-first design.
- [x] Record a TDD implementation plan with files, interfaces, commands, and
  acceptance conditions.
- [x] Self-review the design against every reported audit finding.

**Checkpoint commits:**

- `5d807ee` — design and detailed plan

### C1 — Execution Safety

- [x] Require `ALPACA_EXPECTED_DEV_ACCOUNT_ID` and
  `ALPACA_EXPECTED_SCORED_ACCOUNT_ID`.
- [x] Refuse equal expected IDs and fail account readiness on returned-ID
  mismatch.
- [x] Add a broker `cancel_and_confirm` primitive with bounded polling and an
  explicit uncertain-state failure.
- [x] Refresh final fills after cancellation before calculating replacements.
- [x] Refuse replacements when terminal cancellation cannot be proven.
- [x] Cancel and confirm residual single equity/crypto working orders.
- [x] Enforce a hard `$50.00` live-check notional ceiling before submission.
- [x] Require a clean position and open-order baseline.
- [x] Close only the exact test-created filled quantity.
- [x] Reconcile exact flat state and absence of residual test orders.
- [x] Return nonzero on every timeout, cleanup, and uncertainty path.

**Checkpoint commits:**

- `6c58dac` — expected account identity
- `3e6e7c3` — terminal cancellation
- `ff29e4b` — bounded live venue proof
- `75cca48` — residual and overlapping order prevention

### C2 — Restart and Persistence Safety

- [x] Generate deterministic plan IDs from canonical semantic inputs.
- [x] Derive stable client-order IDs from stable plans.
- [x] Journal `ORDER_SUBMIT_INTENT` before broker submission.
- [x] Reconcile an accepted-but-exceptional submission only by its original
  client-order ID; never blindly resubmit.
- [x] Add one atomic JSON state primitive using a same-directory temporary file,
  fsync, atomic replace, and cleanup.
- [x] Validate manage and scheduler state schemas on read.
- [x] Fail closed on state corruption and write failure.

**Checkpoint commits:**

- `8b5b9fd` — deterministic submission intent and reconciliation
- `9810390` — atomic fail-closed state

### C3 — Bounded AI and Options-Only Scoring

- [x] Remove plan-generating `ThesisLayer.propose()` behavior.
- [x] Expose an ID-only selection response: one existing candidate or abstain.
- [x] Treat malformed output, unknown IDs, missing credentials, and model errors
  as abstention.
- [x] Return the exact original candidate object rather than constructing a plan
  from model output.
- [x] Make candidate collections deeply immutable with tuples.
- [x] Register only deterministic SPY/QQQ options strategies on the scored path.
- [x] Omit equity, crypto, and crypto scheduling from scored construction.
- [x] Enforce an option-only policy gate immediately before kernel review.
- [x] Execute at most one selected candidate per scored cycle.

**Checkpoint commits:**

- `6c0ea74` — bounded candidate selection
- `a225c80` — options-only scored scheduler

### C4 — Release Discipline

- [x] Add exact transitive runtime and development dependency locks.
- [x] Make the compatibility requirements file install the runtime lock.
- [x] Require a full reviewed SHA in `deploy/setup.sh`.
- [x] Fetch, detach, and verify that exact object before installation.
- [x] Install only the runtime lock on the server.
- [x] Add Ruff formatting/linting and mypy gates.
- [x] Add a single `make verify` contract shared by local work and CI.
- [x] Test deployment behavior in a command-stubbed executable harness.

**Checkpoint commit:**

- `973edf8` — locked dependencies, pinned release, and verification gates

### C5 — Truthful Artifacts and Safe Demonstration

- [x] Rewrite current-facing README, plan, decisions, handover, and social copy
  around the actual options-only scored path.
- [x] Describe Alpaca Trading/Data APIs and CLI usage accurately; make no claim
  of an unimplemented integration.
- [x] Show bounded selection/abstention, deterministic pricing, kernel review,
  and external gates in the dashboard.
- [x] Add dashboard body assertions for those claims.
- [x] Remove the duplicate live-order path from `tools/practice.py`; keep it a
  read-only development rehearsal.
- [x] Add a regression that forbids live submission and symbol-wide close calls
  in the practice tool.
- [x] Scan public artifacts for stale generative-AI, multi-sleeve, old test-count,
  and completed-live/deploy claims.
- [x] Add and pass deep candidate immutability coverage.

### C6 — Final Verification and Review

Run every item from a cleanly understood working tree and record the output in
`handoff.md`:

- [x] CPython 3.12 install from `requirements-dev.lock` in an isolated
  environment.
- [x] Full pytest suite.
- [x] Deterministic risk-kernel suite.
- [x] Crash-recovery drill.
- [x] Environment parity checks.
- [x] Dashboard response-body checks.
- [x] Deploy harness tests.
- [x] `compileall` across production entry points.
- [x] `pip check`.
- [x] Ruff format check.
- [x] Ruff lint check.
- [x] mypy production-source check.
- [x] `git diff --check` and secret/claim scans.
- [x] Confirm no order, deployment, push, merge, or submission occurred.
- [x] Commit the completed C5/C6 checkpoint (`3fac4c6`).
- [x] Create and commit `handoff.md` with exact final state and next commands.

**Fresh local evidence (2026-08-30):**

- Target runtime: uv-managed CPython 3.12.11 plus
  `requirements-dev.lock` — `232 passed in 18.09s`.
- Existing Windows project environment: CPython 3.10.11 —
  `232 passed in 102.79s`.
- Risk kernel — `33 passed`.
- Deployment harness — `8 passed`.
- Dashboard response/body suite — `12 passed`.
- Crash drill — `13/13 checks` across eight real kill/restart cycles.
- Crash-drill startup synchronization regression — `2 passed` after the
  pre-fix test failed because the readiness helper did not exist.
- Environment parity — systemd and python-dotenv agree on all 9 variables.
- Dependency check — no broken requirements.
- Ruff — 56 files formatted; all lint checks passed.
- mypy — no issues in 31 production source files.
- `compileall`, `git diff --check`, stale-claim scan, and secret scan passed.
- Public matches for “deployed” are an explicitly gated evidence template and
  an explicit “nothing has been deployed” statement, not completion claims.
- No live broker order, deployment, push, merge, or submission occurred.

### Audit Task A — Preserve and Attest Approach A

- [x] Refresh the repository rather than trusting the audit's concurrent
  `973edf8` snapshot.
- [x] Confirm C5/C6 is already closed at `3fac4c6` with repository handoff
  documentation above it.
- [x] Preserve exact candidate-ID-or-abstain authority and original-object
  retrieval.
- [x] Preserve SPY/QQQ options-only scored construction and omission of the
  crypto job.
- [x] Re-run thesis, scheduler, and audit policy tests: `43 passed`.

### Audit Task B — Candidate Snapshot Provenance and Canonical Manifest

- [x] Query active option contracts through alpaca-py request objects with
  explicit underlying, active status, expiry, contract type, and pagination.
- [x] Reuse one option historical-data client and request an explicit feed.
- [x] Capture contract ID/symbol, source/feed, venue timestamp, observation
  time, age, bid/ask/spread, rule/schema versions, and candidate content hash.
- [x] Reject inactive, untradable, missing, zero, crossed, stale, future-dated,
  inconsistent, or excessively wide quotes before selection.
- [x] Derive executable limits with Decimal arithmetic only.
- [x] Canonically order and content-address each candidate set, including its
  schema version.
- [x] Bind selection evidence to prompt, model, candidate-set, input, and
  response hashes.
- [x] Journal one safe refusal reason and submit nothing for invalid data.
- [x] Run focused tests and commit B as one rollback boundary.

**Verification evidence (2026-08-30):**

- Task B focus: `56 passed`.
- Full exact-lock Python 3.12 suite: `257 passed`.
- Kernel: `33 passed`; crash drill: `13/13`; dashboard: `12 passed`.
- Ruff format/lint, mypy (33 source files), compileall, environment parity,
  dependency integrity, and `git diff --check` passed.
- No credentialed request, order, deployment, push, merge, or submission was
  performed.

### Audit Task C — Typed Alpaca Failures and Ambiguity Discipline

- [ ] Return absent only for verified order-not-found.
- [ ] Distinguish auth, validation, rate-limit, server, network, timeout, and
  decode failures from absence.
- [ ] Attempt every mutation once and reconcile ambiguous acceptance only by
  its original deterministic client-order ID.
- [ ] Latch unresolved ambiguity and block new risk.
- [ ] Add the audit's 404/401/403/422/429/500/connect/read-timeout/malformed
  boundary tests and commit C atomically with executor handling.

### Audit Task D — Pure Order-Lifecycle Reducer

- [ ] Model accepted through terminal/unknown Alpaca states as pure
  observations.
- [ ] Enforce monotonic cumulative fill and explicit replacement lineage.
- [ ] Treat cancel acknowledgement as nonterminal.
- [ ] Reduce successor quantity after every late fill.
- [ ] Prove engine return leaves no strategy entry order open unless unknown
  state has latched the global risk fault.
- [ ] Add state-machine/property coverage and commit D independently.

### Audit Task E — Strategy-Owned Position Ledger and Restart-Safe Exits

- [ ] Derive signed expected per-contract quantities only from confirmed fills.
- [ ] Persist versioned/checksummed account, plan, leg, client-ID, fill, and
  reconciliation state.
- [ ] Reconcile exact owned quantities against venue positions/open orders.
- [ ] Fail closed on foreign, unknown, missing, or corrupt exposure.
- [ ] Route exits through durable intent and the same ambiguity/cancel/partial
  lifecycle as entries.
- [ ] Report flat only after terminal orders and exact zero venue quantity.
- [ ] Enforce singleton ownership of the state directory.

### Audit Tasks F–K — Evidence and Release Sequence

- [ ] **G:** build a versioned release/account/evidence manifest binding the
  clean commit, locks, redacted config/account, options-only allowlist,
  candidate/selector/kernel/order evidence, and journal/state heads.
- [ ] **H Must:** build a pinned, read-only Alpaca CLI proof pack that refuses
  every mutation token and makes no claim until real output is captured.
- [ ] **H Should:** add restricted read-only MCP discovery only after the CLI
  proof, and only if its exact tool surface can be allowlisted and evidenced.
- [ ] **F Should:** add a default-off trade-update stream only after polling,
  restart, and position correctness is proven; REST remains authoritative.
- [ ] **I:** add SBOM/notices, immutable action pins, dependency/license checks,
  restrictive deploy ownership, and singleton service evidence.
- [ ] **I legal gate:** do not create an author-owned MIT license until the user
  confirms the copyright holder and year.
- [ ] **J:** generate truthful public evidence, demo, security, and provenance
  documentation plus a redacted proof drawer from completed artifacts.
- [ ] **K offline:** run the complete clean Python 3.12 verification/release
  matrix and leave the repository clean at an immutable local SHA.
- [ ] **K external:** do not run credentialed checks, orders, deployment, soak,
  push, merge, or submission until their existing authority gates open.

### C7 — Minimal Development Venue Proof (Externally Blocked)

**Required inputs:**

- Dev-only Alpaca key and secret.
- `ALPACA_EXPECTED_DEV_ACCOUNT_ID`.
- `ALPACA_EXPECTED_SCORED_ACCOUNT_ID`, distinct from the dev ID.
- Explicit user direction to perform the already-bounded check.

**Execution sequence after the gate opens:**

1. Export secrets only in the operator environment; never commit them.
2. Run read-only account identity and readiness checks.
3. Run the bounded venue tool with notional no greater than `$50.00`.
4. Require clean baseline, terminal order states, exact quantity close, exact
   flat reconciliation, and zero residual test orders.
5. Preserve the journal and command output as evidence.
6. Stop on any mismatch or uncertainty; do not proceed to deployment.

### C8 — Exact-SHA Deployment and Soak (Externally Blocked)

**Required inputs:**

- VPS hostname/IP, SSH user, and approved authentication method.
- Exact reviewed 40-character commit SHA.
- Runtime configuration and secret-delivery method.
- Successful C7 evidence.

**Execution sequence after the gate opens:**

1. Verify the reviewed commit and a clean source tree.
2. Run `deploy/setup.sh <reviewed-40-character-sha>` on the named target.
3. Confirm detached `HEAD` equals the requested SHA.
4. Install the exact runtime lock and verify the service starts fail-closed.
5. Run `deploy/soak.sh` and collect logs, journal verification, and health output.
6. Do not enable the scored account until soak evidence is accepted.

### C9 — Scored Activation (Pending Explicit Direction)

- [ ] Reconfirm the expected scored account ID immediately before activation.
- [ ] Confirm organizers have not required a composite strategy; otherwise keep
  the approved options-only registration.
- [ ] Confirm all live and deploy gates are green.
- [ ] Start with bounded AI selection enabled and deterministic options-only
  policy enforcement.
- [ ] Monitor journal integrity, state-fault latch, broker identity, residual
  orders, and exact risk limits.
- [ ] Preserve evidence for submission without overstating live duration or
  performance.

## 5. Definition of Done

Implementation work is complete when C0–C6 are green, committed, and documented
with fresh evidence. Operational delivery is complete only when the user supplies
the external inputs for C7–C9 and those gates succeed. Until then, the correct
state is **implementation complete, live proof and deployment externally gated**,
not “deployed” or “live-tested.”

## 6. Immediate Next Actions

1. Begin audit Task C with typed Alpaca failure tests before implementation.
2. Continue C → D → E → G in dependency order with focused tests and review at
   every boundary.
3. Record the license holder/year and operational proofs as external gates;
   continue only safe local work while they are absent.
