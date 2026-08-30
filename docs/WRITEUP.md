# Glassbox — one-page write-up

**Autonomous options agent on Alpaca paper trading.** Deterministic code finds
and prices the trade; a bounded model may only choose among what already
exists, or decline. Every order that reaches the venue passed a deterministic
risk kernel first.

## AI logic — the model has exactly one decision

Most agents let a model author the trade. Ours cannot express one. The pipeline
is deliberately narrow:

1. Deterministic strategy code screens the SPY/QQQ event calendar and the
   option surface, then builds fully specified candidates: contract symbols,
   side, quantity, limit price, maximum loss, exits, evidence, and a stable
   content-addressed ID.
2. The candidate set is canonically ordered and hashed. The model receives only
   summarised immutable fields.
3. The model returns **one candidate ID, or null.** That is its entire output
   schema. Extra fields are rejected.
4. Anything else — timeout, malformed JSON, an unknown ID, an altered object, a
   missing credential — is an **abstention**, journalled with its reason.
5. The exact original candidate object, not a reconstruction, goes to the risk
   kernel.

So the worst a compromised or hallucinating model can do is pick a different
pre-approved trade, or nothing. It cannot invent a contract, widen a limit,
raise size, or remove a stop, because there is no field in which to say so. A
selection receipt binds the prompt, model, candidate-set hash, and response
hash, so a decision can be replayed and checked afterwards.

## Risk gates — deterministic, ordered, fail-closed

A **13-invariant kernel** reviews the selected object: symbol allowlist,
bounded max loss, sleeve budget, daily burn, concentration, position count,
gross exposure, drawdown kill switch, market hours, expiry guard, idempotency,
sanity band, and order frequency. It performs no network or model calls, so its
verdict is reproducible from its inputs.

Around it sit the gates that matter once real orders exist:

- **Account identity.** Literal paper endpoint, `PK` key prefix, and the
  returned account number matched against an environment-specific expected ID.
  Development and scored IDs must differ.
- **Typed venue failures.** An order lookup returns "absent" only for a
  verified 404. Auth, validation, rate-limit, server, and transport failures
  raise distinct errors, so *"no such order"* is never inferred from *"we could
  not ask"*. Unknown state fails closed.
- **Durable intent before mutation.** Intent is journalled, then one submit.
  An ambiguous timeout is resolved by looking up the deterministic
  `client_order_id` — never by sending a second order.
- **Exact position ownership.** Expected signed quantity per contract is
  derived only from confirmed fills and reconciled against the venue before any
  new risk. Foreign exposure, a mismatch, or an unreadable order list blocks
  new entries. Exits sell the exact owned quantity under a deterministic ID;
  symbol-wide liquidation is never used, and "flat" requires a terminal order
  **and** a zero venue quantity.
- **Latching kill switch** on drawdown, re-armed only by a human.
- **Crash recovery.** State is written atomically and checksummed; corruption
  refuses to heal to empty. One scheduler may own a state directory.

## Alpaca infrastructure

`alpaca-py` (pinned, hash-locked) against the **paper Trading and Data APIs**:
account and clock, server-authoritative active option contracts via
`GetOptionContractsRequest` with pagination, timestamped option quotes,
order submission with deterministic client order IDs, order and position
reconciliation, and `get_portfolio_history` for equity. A read-only **Alpaca
CLI** evidence capture tool builds commands from an allowlist and refuses any
mutating token before a process starts, so it cannot become a second order
path.

A read-only **MCP client** is implemented the inverse of a normal one. The
official server's default toolset includes `place_option_market_order`,
`close_position` and `cancel_orders`; rather than trusting configuration to
hide them, the client declares the only tools it will ever call, discovers what
the server actually exposes, and enforces three independent barriers: an exact
allowlist, a mutating-verb scan that runs even for allowlisted names, and a
discovery gate so no path reaches a call without inspection. Its tests run
against a real MCP server subprocess that advertises those dangerous tools on
purpose. It has not yet been run against the official Alpaca MCP server with
live credentials.

An optional trade-update stream is implemented as a *hint*: REST snapshots
always win, and a stream gap blocks new entries until REST reconciles.

## Verify it without trusting us

`python tools/verify_submission.py` re-derives what can be re-derived and
inspects what cannot, using local artifacts only. It checks the journal hash
chain, that **every AI selection names a candidate that was actually offered**,
that no recorded model response carried an executable field, release-manifest
integrity, ledger checksums, and dependency pinning. That second check turns
the central design claim into something falsifiable: if it ever fails on real
evidence, the model authored a trade. Every offered candidate also carries an
independent kernel verdict, including the ones the model declined, so the
alternatives are on the record too.

## Performance measurement

Scored on **total account equity**, read from Alpaca's own portfolio history
rather than reconstructed from our fill log. The dashboard reports return,
peak-to-trough drawdown, Sharpe, Sortino, Calmar and volatility — each labelled
with the sample size behind it. Ratios stay marked *indicative* below 20
observations, because an annualised Sharpe from five daily points is noise with
a Greek letter attached, and publishing it as a result would contradict the
premise of the project.

## What is proven, and what is not

Verified: 544 automated tests, a 13/13 crash-recovery drill, format, lint,
types, hash-locked dependencies, and a green CI on every commit.

Not claimed: no live paper order has been placed from this repository, no CLI
proof bundle has been captured against a real account, there is no MCP integration,
and no deployment or soak has run. Those gates are listed as open
in `handoff.md`, and a test suite fails the build if the public copy ever
claims otherwise.
