# Glassbox Profit and Win-Readiness Master Plan

> **For agentic workers:** execute checkpoint-by-checkpoint with TDD. Every
> checkpoint is a rollback boundary and is pushed only after its complete gate
> passes.

**Goal:** produce a safe, evidence-complete, options-only Glassbox release and
promote only a historically robust strategy policy.

**Architecture:** preserve one deterministic candidate-to-order path and add a
separate mutation-free point-in-time research/replay plane. A content-addressed
policy manifest is the only bridge from research to runtime.

**Tech stack:** Python 3.12, Pydantic, alpaca-py, pytest, Ruff, mypy, immutable
JSON/JSONL evidence, Alpaca Trading and Market Data APIs, and official Alpaca
CLI proof.

**Spec:** `docs/superpowers/specs/2026-08-31-profit-win-readiness-design.md`

## Progress ledger

| Checkpoint | Status | Evidence / next action |
| --- | --- | --- |
| C0 | Complete | `f23b24b`; remote Python 3.12 CI passed |
| C1 | Complete | `dd9e73e`; remote Python 3.12 CI passed |
| C2 | Complete | Schema-v2 cumulative-fill replay; 584 tests collected |
| C3 | Complete | `a643a73`; exact owned-quantity mutation lifecycle; CI passed |
| C4 | Complete | Scored singleton, verified 404, exact SDK decimals; 597 tests |
| C5 | Next | Semantic verification and official CLI proof |
| C6 | Pending | Data and Greeks truthfulness |
| C7-C10 | Pending | Point-in-time data, replay, candidates, promotion |
| C11-C12 | Pending | Shadow evaluation and offline release candidate |
| X1-X4 | Externally gated | Credentials, order authority, VPS, scored activation |
| C13 | Pending | Canonical evidence-derived submission package |

## Global constraints

- Base exactly on `c45b23fdf6cb51be1092ea2b0c76d1e7f0128c69`.
- Work and push only on `utk-review`; never force-push.
- Preserve `utk`, `review`, and `main`.
- Scored composition is SPY/QQQ options-only.
- AI returns one offered immutable candidate ID or abstains.
- All executable money and quantity values remain `Decimal`.
- Tests precede behavior changes and must fail for the intended reason.
- Focused tests precede the full Python 3.12 gate.
- No credentials, raw licensed data, or unredacted account evidence enter Git.
- No order, deployment, merge, activation, or submission without its explicit gate.

## Checkpoint C0 - audited branch and artifacts

- Create `utk-review` at the audited teammate commit.
- Import the independent audit, remediation plan, and audit handoff.
- Commit the approved design and this master plan.
- Rewrite the handoff header with the exact branch/base, baseline verification,
  known Windows dead-PID test hang, pending checkpoints, and external gates.
- Run documentation/claim/secret checks, `git diff --check`, and repository
  status checks; commit and push only when clean.

## Checkpoint C1 - mandatory scored release gate

- Add failing startup/release tests for disabled gates, absent approved SHA,
  mismatched HEAD, dirty tree, pending required gates, required skips, stale
  evidence, wrong account, wrong lock/policy hash, and imprecise endpoint matches.
- Make scored mode unconditionally validate an external approved release
  manifest; development dry-run remains non-mutating.
- Run release/startup/claim tests, mypy on release/startup code, then the full gate.

Commit: `fix: require an approved evidence-complete scored release`

## Checkpoint C2 - idempotent fill ledger

- Add failing entry/exit duplicate, higher/lower cumulative, overfill,
  corruption, and crash/replay tests.
- Persist last cumulative fill by client order ID and apply only a validated
  non-negative delta.
- Run ledger/lifecycle/crash tests and the full gate.

Commit: `fix: make confirmed fill replay idempotent`

## Checkpoint C3 - unified mutation and exact unwind

- Add failing cases for ambiguous accepted exits/unwinds, incomplete second
  legs, late fills during cancel, partial unwind, foreign same-symbol exposure,
  residual orders, exact flatness, and restart ledger results.
- Route entry, replacement, exit, and unwind through one intent-first,
  deterministic-ID, single-submit, reconcile, reduce, cancel-confirm, and
  exact-position service.
- Remove symbol-wide closes from scored option paths.

Commit: `fix: reconcile exact option exits and incomplete-leg unwinds`

## Checkpoint C4 - runtime ownership and broker boundaries

- Add startup-level singleton tests plus a bounded cross-platform dead-PID test.
- Acquire `ProcessLock` for the full scored scheduler lifetime.
- Require verified HTTP 404 for absence; keep other typed failures distinct.
- Prove exact tick prices and quantities at the pinned SDK boundary.

Commit: `fix: enforce scored runtime ownership and exact broker boundaries`

## Checkpoint C5 - semantic verification and CLI proof

- Add failing cross-cycle, no-offer, digest/receipt mismatch, extra AI field,
  missing output, required-skip, forged complete flag, stale/wrong-account,
  wrong-version, and wrong-command proof tests.
- Bind evidence to a cycle and candidate-set digest; validate the captured
  canonical ID/null response.
- Implement explicit offline-incomplete and release-verified verdicts.
- Capture the pinned CLI's version, doctor, account, account configuration,
  clock, active SPY contracts, SPY chain, orders, and positions with exact
  account equality and semantic validation.

Commit: `fix: verify candidate authority and official cli proof`

## Checkpoint C6 - data and Greeks truthfulness

- Add failing true upper/lower strangle breakeven cases.
- Preserve supplementary missing-Greeks behavior while distinguishing missing,
  auth, rate-limit, transport, malformed, and unhealthy surface outcomes.
- Replace broad primary-data catches with typed journaled abstentions.
- Reconcile README, dashboard, and write-up claims. Remove or qualify MCP proof.

Commit: `fix: report option data and convexity evidence truthfully`

## Checkpoint C7 - point-in-time research data

- Create immutable research frame/request/fidelity contracts and tests.
- Add historical underlying and option quote/bar adapters with explicit feed,
  pagination, source, as-of, and hash metadata.
- Add official timestamped ISM manufacturing/services, ADP, and jobless-claims
  event families; isolate month-end flow.
- Enforce the fixed development/validation/holdout split and reject lookahead.
- Keep raw data outside Git; commit only manifests, fixtures, and aggregates.

Commit: `feat: add point-in-time options research frames`

## Checkpoint C8 - deterministic replay

- Add base/adverse/no-fill scenarios and tests for quote fills, limit misses,
  latency, spread widening, fees, partial fills, late fills, cancel races, and gaps.
- Replay the production candidate factory and risk kernel against historical
  frames without broker mutation.
- Freeze and reproduce the current event-vol policy as the baseline.
- Require identical seeded runs to produce identical canonical report hashes.

Commit: `feat: replay deterministic option candidates conservatively`

## Checkpoint C9 - bounded candidate families and event exits

- Add deterministic straddle, strangle, and post-release breakout candidates.
- Add event-specific exit timestamps at 5, 15, 30, and 60 minutes plus hard
  measurement/expiry guards.
- Add debit spreads only with bounded net-debit max-loss validation and a tested
  atomic MLEG lifecycle/capability gate.
- Prohibit scored independent-leg fallback for policies that require MLEG.

Commit: `feat: evaluate bounded options candidate families`

## Checkpoint C10 - walk-forward evaluation and policy promotion

- Add rolling folds and a seeded week-block bootstrap.
- Compare bounded AI with deterministic rank, always trade, abstain, seeded
  random, and hindsight oracle.
- Enforce every promotion threshold from the design, including minimum sample,
  positive median, profit factor, positive-week probability, tail loss,
  drawdown, adverse spread, event concentration, and neighbor stability.
- Rank eligible policies deterministically. Emit `no policy promoted` when none pass.

Commit: `feat: promote only robust replayed option policies`

## Checkpoint C11 - mutation-free live shadow

- Add a broker interface that exposes only read methods to shadow evaluation.
- Capture live candidates, selection, kernel verdicts, hypothetical fills,
  event exits, and counterfactual P&L.
- Add tests that fail on any submit/cancel/replace/close/exercise access.
- Calibrate reported slippage without automatically changing runtime policy.

Commit: `feat: record mutation-free live shadow evidence`

## Checkpoint C12 - offline release candidate

- Complete SBOM, notices, vulnerability/secret scans, immutable actions, claim
  verification, and artifact hashes.
- Keep the trade stream disabled until its separate paper soak passes.
- Present candidate -> AI -> kernel -> intent -> lifecycle -> reconciliation ->
  equity in the dashboard and demo artifacts.
- Run the full clean Python 3.12 matrix and hostile review; fix every P0/P1.
- Re-score P&L, technology, creativity, and presentation against repository evidence.

Commit: `build: produce the offline glassbox release candidate`

## External gates X1-X4

- X1 read-only proof needs development credentials, distinct explicit account
  IDs, and the pinned CLI. It authorizes no order.
- X2 needs explicit authorization for one development-paper mutation capped at
  $50 and requires terminal cleanup plus exact flat reconciliation.
- X3 needs the VPS/SSH target, secret-delivery method, reviewed SHA, and green
  prior proof before exact-SHA deployment and soak.
- X4 needs a fresh dedicated $100,000 paper account, options permission,
  completed soak, release manifest, and explicit scored activation direction.

## Checkpoint C13 - canonical submission

- Generate the Glassbox README, write-up, dashboard, deck, video, cover, and
  event copy from the same evidence bundle.
- Verify the public repository resolves to the reviewed release and every URL
  serves the expected body.
- Remove all stale or unproved counts, MCP, fill, deployment, soak, and P&L claims.
- Require explicit direction before merge/default-branch changes or submission.

## Per-checkpoint gate and push contract

For each checkpoint: RED test, observed intended failure, minimal GREEN change,
focused suite, full Python 3.12 verification, hostile diff review, handoff update,
conventional commit, ordinary push to `origin/utk-review`, and green remote CI.
No later checkpoint starts on a red remote checkpoint.

Full verification includes Ruff format/lint, mypy, all pytest tests, kernel
tests, crash drill, environment parity, compileall, pip check, deploy/claim/
secret/notices checks, `git diff --check`, and clean intended status.
