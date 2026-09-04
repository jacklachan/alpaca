# Glassbox — one-page write-up

**Autonomous options agent on Alpaca paper trading, scored account
`PA3XT8QFJZAQ`.** Deterministic code finds and prices every trade; a bounded
model may only choose among what already exists, or decline. Every order that
reached the venue passed a deterministic risk kernel first. Detail beyond one
page: [WRITEUP-FULL.md](WRITEUP-FULL.md).

## AI logic — the model has exactly one decision

Most agents let a model author the trade. Ours cannot express one. Deterministic
strategy code screens the SPY/QQQ event calendar and the option surface, then
builds fully specified candidates: contract symbols, side, quantity, limit
price, maximum loss, exits, evidence, and a content-addressed ID. The set is
canonically ordered and hashed; the model receives only summarised immutable
fields and returns **one candidate ID, or null**. That is its entire output
schema, and extra fields are rejected.

Anything else — timeout, malformed JSON, an unknown ID, an altered object, a
missing credential — is an abstention, journalled with its reason. The exact
original candidate, not a reconstruction, goes to the kernel. So the worst a
compromised or hallucinating model can do is pick a different pre-approved
trade, or nothing: there is no field in which to say bigger, or naked, or no
stop. A selection receipt binds prompt, model, candidate-set hash and response
hash, so any decision can be replayed.

The model itself is `Qwen/Qwen2.5-72B-Instruct`, served through **Featherless
AI**, reached over an OpenAI-compatible endpoint (`glassbox/thesis.py`). Nothing
about the bound depends on which model sits behind it -- the schema is the
enforcement -- but that is the one that ran all week.

## Risk gates — deterministic, ordered, fail-closed

A **13-invariant kernel** reviews the selected object: symbol allowlist, bounded
max loss, sleeve budget, daily burn, concentration, position count, gross
exposure, drawdown kill switch, market hours, expiry guard, idempotency, sanity
band, order frequency. It makes no network or model call, so its verdict is
reproducible from its inputs. Around it:

- **Account identity** — literal paper endpoint, `PK` key prefix, and the
  returned account number matched against an expected ID. Dev and scored differ.
- **Typed venue failures** — *"no such order"* is never inferred from *"we could
  not ask"*. Unknown state fails closed.
- **Exact position ownership** — expected quantity derives only from confirmed
  fills, reconciled against the venue before any new risk. Exits sell the exact
  owned quantity; symbol-wide liquidation is never used.
- **Option-surface gates** — a long strangle that aggregates net short gamma or
  vega is mislabelled and refused, as is convexity bought at implied volatility
  rich enough that the post-event collapse outweighs a correct directional call.
  Missing Greeks abstain rather than pass.
- **Measurement-aware exit** — near the scoring snapshot, a contract too wide to
  mark honestly is flattened, and one with no two-sided quote is treated as the
  worst case. Cash has no marking ambiguity.
- **Latching kill switch** on drawdown, re-armed only by a human.

## Alpaca infrastructure

`alpaca-py` (pinned, hash-locked) against the **paper Trading and Data APIs**:
account and clock, server-authoritative active option contracts via
`GetOptionContractsRequest` with pagination, timestamped option quotes and
Greeks, orders with deterministic client order IDs, order and position
reconciliation, and `get_portfolio_history` for equity — we are scored on
Alpaca's number, not one we reconstruct. Trading sessions come from Alpaca's
calendar rather than weekday arithmetic, which is silently wrong on holidays.

A read-only **Alpaca CLI** evidence tool builds commands from an allowlist and
refuses any mutating token before a process starts, so it cannot become a second
order path. The **MCP client** is built inverted: the official server's default
toolset includes `place_option_market_order`, `close_position` and
`cancel_orders`, so rather than trusting configuration to hide them, the client
declares the only tools it will ever call and enforces an exact allowlist, a
mutating-verb scan, and a discovery gate. Run against the official Alpaca MCP
Server **3.4.7** with live credentials: three authenticated read-only calls
against the scored account, and four mutating tools **refused** by attempting
them.

## Verified, and not claimed

`python tools/verify_submission.py` re-derives what can be re-derived from local
artifacts, with no credentials and no network: the journal hash chain, that
every AI selection named a candidate actually offered, that no recorded model
response carried an executable field, release-manifest integrity, and dependency
pinning. That second check makes the central claim falsifiable — if it ever
fails on real evidence, the model authored a trade. Behind it sit
747 automated tests, a 14/14 crash-recovery drill, and green CI on every
commit.
`python tools/calibration.py` prints the decay the risk model forecast before
each order against what actually happened.

**Not claimed:** the agent runs on a laptop under a watchdog, not a deployed
host, so no `deployment_soak` evidence exists and none is asserted. P&L is a
mark on an open position, not a realised result. Four days does not prove an
edge, and the hash chain detects edits to the recorded history without proving
the history was never regenerated — Alpaca's own order IDs are what a third
party reconciles against.
