# Glassbox Profit and Win-Readiness Design

**Approved:** 2026-08-31
**Base:** `c45b23fdf6cb51be1092ea2b0c76d1e7f0128c69` (`origin/review`)
**Implementation branch:** `utk-review`

## Goal

Maximize Glassbox's probability of a top-three hackathon result while keeping
the scored account options-only, every executable field deterministic, AI
authority limited to one offered candidate ID or abstention, and every venue
mutation exactly attributable and reconciled.

Profit is not guaranteed. The design optimizes reproducible, risk-bounded
terminal-equity outcomes rather than trade count or a cherry-picked simulation.

## Authority boundary

The scored path has one direction:

```text
point-in-time Alpaca data
  -> deterministic immutable candidates
  -> canonical candidate-set manifest
  -> bounded AI: offered ID or null
  -> exact original candidate
  -> deterministic risk kernel
  -> durable intent and unified order lifecycle
  -> idempotent per-contract ledger
  -> exact venue reconciliation
  -> release/evidence manifest
```

The model cannot invent or alter a contract, side, quantity, order type, limit,
maximum loss, stop, target, time exit, or structure. Equity and crypto remain
development-only. A missing model credential, timeout, malformed response,
unknown ID, receipt mismatch, or stale candidate set means abstention.

## Runtime and research separation

The research plane imports deterministic candidate factories and the risk
kernel, but receives no broker mutation capability. It consumes immutable,
point-in-time frames and produces content-addressed experiment reports. Runtime
never tunes itself; only a reviewed policy manifest may activate a parameter
set.

The research contracts are:

- `ResearchMarketFrame`: UTC as-of time, underlying, contracts, quotes or bars,
  source, feed, fidelity, request lineage, and content hash.
- `CandidateFactory`: deterministic production of immutable `TradePlan` values.
- `FillScenario`: base, adverse, and no-fill execution assumptions.
- `ReplayConfig`: policy version, event universe, parameter grid, latency,
  spreads, fees, and deterministic seed.
- `ReplayResult`: fills, refusals, P&L, drawdown, exposure, slippage, and lineage.
- `ExperimentReport`: dataset/config hashes, fixed chronological split,
  aggregate metrics, bootstrap intervals, stress outcomes, and promotion verdict.
- `PolicyManifest`: exact enabled factories and parameters, source experiment,
  repository commit, approval state, and canonical hash.

Executable money and quantity values remain `Decimal`. Raw licensed market data
and credentials stay outside Git. Repository evidence contains only query
manifests, hashes, legally distributable fixtures, and aggregates.

## Data fidelity and anti-lookahead rules

- Tier A: Alpaca historical option quotes/bars with observed spreads.
- Tier B: recorded point-in-time Alpaca shadow snapshots.
- Tier C: underlying-only and synthetic safety/stress cases.

Tier C cannot promote a strategy or support a historical-options P&L claim.
Every frame rejects records after its decision timestamp. The fixed split is:

- development: February 2024 through December 2025;
- validation: January through April 2026;
- untouched holdout: May through August 2026.

Raw holdout data is not inspected during parameter selection. Re-running the
same dataset/config/seed must produce the same report hash.

## Simulation and candidate policy

Base fills buy at the next observable ask within the immutable limit and sell
at the next observable bid. A limit that never becomes executable is a no-fill.
Applicable fees are versioned in the replay config.

Adverse replay adds one-bar latency, widens the observed spread by 1.5 times,
assumes entry at the full limit, and prices exits one tick worse than the next
bid. Partial fill, late fill, cancel race, no-fill, and market-gap scenarios are
separate deterministic cases.

Research compares pre-event strangles and straddles, post-release breakout
calls and puts, and debit spreads only after bounded-loss kernel and atomic
Alpaca MLEG support are proven. Event positions receive an event-specific exit;
holding to the final measurement is a candidate to evaluate, not the default.

Any scored multi-leg policy requires an account capability proof and a tested
atomic MLEG lifecycle. It cannot silently fall back to independent legging.

## Promotion rule

A policy is eligible only with at least 30 quote-complete holdout events,
positive median net return, profit factor at least 1.20, bootstrap probability
of positive weekly terminal equity at least 60%, probability of a loss greater
than 5% at most 10%, maximum drawdown at most 8%, positive P&L under the 1.5x
spread scenario, no event family above 40% of holdout profit, and neighboring
parameter configurations in the top quartile.

Eligible policies rank by probability of beating the frozen current baseline,
then median return, then tail loss. When none pass, the result is `no policy
promoted`; safety controls are never weakened to force a trade.

## Release and evidence semantics

Scored startup always requires an approved full commit SHA and an
evidence-complete manifest. Dirty trees, mismatched lock/policy hashes, stale or
wrong-account proof, required skips, and pending mandatory gates fail closed.

Verification has two non-overlapping conclusions:

- `OFFLINE CHECKS CLEAN - RELEASE EVIDENCE INCOMPLETE`;
- `RELEASE VERIFIED` only after every mandatory artifact passes.

Alpaca CLI is the mandatory operational proof path. MCP remains a restricted
read-only client claim until the official server version, discovered surface,
exact account identity, and successful calls are captured. The trade stream
remains a disabled hint until polling/restart behavior completes a paper soak.

## Operational safety

One mutation service owns durable intent, deterministic client identity,
single-attempt submit, lookup/adoption after ambiguous responses, monotonic
lifecycle reduction, terminal cancellation, idempotent fill application, and
exact venue reconciliation for entries, replacements, exits, and unwinds.

Only verified HTTP 404 means absence. Process singleton ownership spans the
entire scored runtime. Symbol-wide option liquidation is prohibited. Flat means
terminal strategy orders plus exact zero owned venue quantity.

## External gates

Read-only account/data proof needs development credentials and distinct
expected development/scored account IDs. A development mutation additionally
needs explicit authorization and remains capped at $50. Deployment needs an
exact reviewed SHA, VPS target, secret-delivery method, and successful prior
proof. Scored activation needs a fresh dedicated $100,000 paper account,
options permissions, completed soak, and explicit direction.

No branch push authorizes an order, deployment, merge, default-branch change,
or event submission.
