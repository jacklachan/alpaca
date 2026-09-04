# Glassbox

Glassbox is an options-only paper-trading agent for the Alpaca AI Trading
Agents Hackathon. Deterministic strategy code creates fully priced SPY and QQQ
option candidates. A bounded language model may select one existing candidate
ID or abstain. It cannot invent contracts, quantities, sides, limit prices,
maximum loss, or exits.

Every selected candidate still passes through a deterministic 13-invariant
risk kernel and a restart-safe executor before the Alpaca Trading API can see
an order.

## The result, and the finding that outlived it

Scored account `PA3XT8QFJZAQ`, funded at $100,000. Alpaca's own portfolio
history records the measured close, end of Thursday 3 September 2026, at
**$94,207.02 — down 5.79%.** That is the number, read from the broker rather
than reconstructed from our fill log.

The more interesting number is from the same instant. We price options off
Alpaca's **indicative** feed, a derived estimate rather than OPRA. At 16:00 ET
our own honest reading of the book said **$99,642.35** and Alpaca's official
close said **$94,207.02** — the same account, the same second, **$5,435.33
apart, 5.77% of the account.**

Neither reading is a lie. An option mark is an opinion until it is cash. That
gap is the entire reason this agent has a measurement-aware exit, and it is
the one piece of evidence here that no equity curve could produce:

```bash
python tools/calibration.py
```

It is frozen at the measurement instant, so it prints the same result whenever
you run it.

## Evidence status

Captured against the scored account, and re-checkable with
`python tools/verify_submission.py` (11 checks, no credentials, no network):

- **Live options orders.** A 35-lot QQQ strangle, both legs filled, plus later
  entries and exits. Order IDs and timestamps are Alpaca's, not ours.
- **CLI proof captured.** Read-only Alpaca CLI evidence bundle, built from an
  allowlist that refuses any mutating token before a process starts.
- **MCP proof captured.** Run against the official Alpaca MCP Server **3.4.7**
  with live credentials: three authenticated read-only calls against the scored
  account, and four mutating tools **refused** by attempting them.
- **Dev venue proof captured** on a separate development account, kept distinct
  from the scored one by an account-identity gate.

Still open, and not claimed anywhere:

- **No VPS soak.** The agent ran on a laptop under a restart watchdog. No host
  target was ever provisioned, so no `deployment_soak` evidence exists.

The programmatic integration is `alpaca-py` against the Alpaca paper Trading
and Data APIs.

## Scored data flow

```text
Alpaca market data
       |
deterministic SPY/QQQ option candidate generators
       |
bounded AI: select one existing candidate ID or abstain
       |
exact original TradePlan object
       |
13-invariant risk kernel
       |
intent journal -> submit/reconcile -> cancel-and-confirm -> exact cleanup
       |
Alpaca paper Trading API
```

The scored account registers no core equity or crypto strategy and schedules
no crypto job. Equity and crypto strategies remain development fixtures only;
the bounded live connectivity proof is the explicit `tools/live_check.py` CLI.

If the model is missing, times out, returns malformed JSON, names an unknown
candidate, or attempts to add trade fields, the scored cycle abstains.

## Safety properties

- Credentials are bound to `ALPACA_EXPECTED_DEV_ACCOUNT_ID` or
  `ALPACA_EXPECTED_SCORED_ACCOUNT_ID`; the two IDs must differ.
- Paper trading is checked by configuration, key prefix, client construction,
  returned account identity, and paper endpoint.
- The venue proof requires a clean dev baseline, caps notional at $50, cancels
  and confirms residual orders, closes only test-created quantity, and requires
  exact flat reconciliation.
- Cancel/reprice never overlaps orders: terminal cancellation is observed
  before replacement, including late-fill reconciliation.
- Submission intent is durable before broker submission. Ambiguous timeouts are
  reconciled by deterministic client order ID and are never blindly retried.
- Kill-switch, exit-target, and positioned-event state use atomic
  flush/fsync/replace persistence and fail closed on corruption.
- Deployments require a full reviewed 40-character commit SHA and install the
  exact runtime lock.
- Alpaca failures are classified, not collapsed. An order lookup returns
  absent only for a verified 404; auth, validation, rate-limit, server, and
  transport failures raise typed errors, so "no such order" is never inferred
  from "we could not ask". Retries are bounded, jittered, and applied only to
  idempotent operations.
- Order state is folded from observations by a pure reducer. Cumulative fill
  never decreases, a cancel acknowledgement is never terminal, and an
  unrecognised status fails closed.
- Positions are owned per contract. Expected signed quantity is derived only
  from confirmed fills and reconciled exactly against the venue; unknown or
  foreign exposure blocks new entries. Exits sell the exact owned quantity
  under a deterministic client order ID registered on disk before the
  mutation. Symbol-wide liquidation is not used as an exit or as proof of
  flatness, and flat is reported only from a terminal order plus a zero venue
  quantity.
- A release manifest binds commit, lock hashes, policy hash, resolved paper
  endpoint, environment, and a redacted expected-account suffix. It refuses a
  scored start that is dirty, live-endpoint, unbound, or not options-only, and
  fails before writing if any credential value appears in it.
- Only one scheduler may own a state directory; a lock is reclaimed only when
  its recorded process is verifiably gone.
- Trading sessions come from Alpaca's own calendar, not from weekday
  arithmetic against a hardcoded holiday table. Every expiry decision rests on
  that count, and which calendar answered is recorded.
- A reconciliation gap heals itself once the venue and ledger agree exactly;
  durable state corruption never does and always needs a human. One transient
  failure to read open orders must not stop an unattended agent for a week.
- Performance is measured on total account equity from Alpaca's own portfolio
  history, not reconstructed from our fill log. Risk-adjusted ratios are
  reported with the sample size behind them and stay marked indicative below
  twenty observations: an annualised Sharpe from five daily points is not a
  result, and publishing it as one would contradict everything else here.
- Long-convexity candidates are gated on the option surface Alpaca
  publishes. A position that aggregates net short gamma or vega is not the
  trade its own thesis describes and is refused; so is convexity bought at
  implied volatility high enough that the post-event collapse outweighs the
  move, or decay that outruns the event. Missing Greeks record their absence
  and abstain rather than being rendered as healthy Greeks.
- The optional trade-update stream is a latency hint, never an authority. A
  REST snapshot always wins, and a stream gap blocks new entries until REST
  reconciles.
- The journal is append-only and SHA-256 chained. This detects edits to the
  recorded file; it is not called tamper-proof. Broker IDs and timestamps make
  order claims reconcilable against records we do not control. A local hash
  chain is not third-party attestation.

## Local setup and verification

Python 3.12 is the release target.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
make verify
```

On Windows, run the commands from `Makefile` with
`.venv\Scripts\python.exe`. The verification target covers formatting, lint,
typing, the full tests, the dedicated kernel suite, crash recovery, environment
parity, compilation, dashboard responses, and `pip check`.

Useful read-only commands:

```bash
python tools/env_parity.py .env.example
python tools/verify_chain.py
python tools/crash_drill.py -n 8 --seed 1
python main.py --dry-run
uvicorn dashboard.app:app --host 127.0.0.1 --port 8080
```

`main.py --dry-run` contacts Alpaca and proves account identity but does not
start the clock. Normal and `--once` modes are order-capable.

## External gate commands

Only after the user supplies dev-only credentials and explicit expected IDs:

```bash
python tools/live_check.py
python tools/live_check.py --trade --notional 25
```

The first command is read-only. `--trade` is intentionally explicit, refuses
the scored account, and enforces the $50 ceiling.

Only after the user supplies a VPS/SSH target and chooses a reviewed commit:

```bash
sudo bash deploy/setup.sh <full-40-character-reviewed-commit-sha>
sudo bash tools/soak.sh
```

This repository does not push, merge, place an order, or deploy automatically.

## Repository map

```text
glassbox/
  broker.py       Alpaca boundary, identity checks, reconciliation, cancellation
  schema.py       frozen TradePlan and option contract validation
  thesis.py       bounded select-or-abstain AI plus read-only daily summary
  kernel.py       deterministic 13-invariant risk review
  execute.py      intent journal, reconciliation, fill/cancel/reprice state machine
  order_lifecycle.py  pure reducer over observed order states
  market_calendar.py  trading sessions from Alpaca, with recorded provenance
  replay.py           rebuild recorded decisions from the journal alone
  greeks.py           deterministic option-surface gates (delta/gamma/theta/vega/IV)
  mcp_client.py       read-only MCP client that cannot place an order
  verification.py     checks a third party can run against our claims
  performance.py      equity-curve metrics with sample-size caveats
  trade_stream.py     optional trade-update hint; REST stays authoritative
  position_ledger.py  per-contract ownership and exact venue reconciliation
  candidates.py   canonical candidate sets, manifests, selection receipts
  option_data.py  Alpaca option contract and quote acquisition
  release.py      release/account identity manifest
  state.py        atomic fail-closed JSON persistence, singleton process lock
  scheduler.py    options-only scored policy and development schedule
  strategies/     deterministic option generators plus dev-only fixtures
dashboard/app.py  credential-free, read-only journal dashboard
tools/live_check.py           bounded dev venue proof
tools/capture_alpaca_proof.py read-only Alpaca CLI evidence capture
tools/verify_mcp_surface.py   MCP discovery, refusal proof, read-only capture
tools/verify_submission.py    one-command verification of every claim
tools/build_notices.py        regenerates THIRD_PARTY_NOTICES.md from the lock
deploy/setup.sh   exact-SHA deployment
```

## Verify it yourself

```bash
python tools/verify_submission.py
```

No credentials, no network, nothing mutated. It re-derives what can be
re-derived and inspects what cannot: the journal hash chain, that every AI
selection names a candidate that was actually offered, that no recorded model
response carried an executable field, release-manifest integrity, position
ledger checksums, dependency pinning, and whether the CLI/MCP proof bundles
exist. It also **replays** every recorded candidate set: the content address
the agent published is recomputed from the parts it recorded, so determinism
is something you run rather than something we assert. A `SKIP` means the evidence does not exist yet - it is never a waived
check. The same command runs in CI on every commit.

## Operations

`docs/OPERATIONS.md` is the runbook: exit codes, what each latch means,
how to capture integration evidence, and what not to do to a state file.

## Submission write-up

`docs/WRITEUP.md` is the one-page write-up covering AI logic, risk gates, and
Alpaca infrastructure. Its claims are enforced by `tests/test_claims.py`,
including the test count, so the document cannot quietly drift from the code.

## License

MIT, in `LICENSE`. Dependency licensing is recorded in
`THIRD_PARTY_NOTICES.md`, generated from `requirements.lock` by
`tools/build_notices.py`. No code, UI, prompt, or asset from any reviewed
third-party trading project is present in this repository.

The approved design and task-level implementation plan are preserved under
`docs/superpowers/`.
