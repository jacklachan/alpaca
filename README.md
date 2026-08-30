# Glassbox

Glassbox is an options-only paper-trading agent for the Alpaca AI Trading
Agents Hackathon. Deterministic strategy code creates fully priced SPY and QQQ
option candidates. A bounded language model may select one existing candidate
ID or abstain. It cannot invent contracts, quantities, sides, limit prices,
maximum loss, or exits.

Every selected candidate still passes through a deterministic 13-invariant
risk kernel and a restart-safe executor before the Alpaca Trading API can see
an order.

## Evidence status

The implementation and local verification suite are complete. Two external
gates are deliberately still open:

- **Dev venue proof pending.** No live paper order has been submitted from this
  repository. The proof requires user-supplied dev credentials and both
  explicit expected account IDs.
- **VPS soak pending.** No host target was supplied, so no deployment or
  multi-day soak has been performed.

- **CLI proof pending.** `tools/capture_alpaca_proof.py` builds and captures
  read-only Alpaca CLI evidence and is tested against fakes, but no proof
  bundle has been captured from a real CLI against a real account.

Do not describe any gate as complete until its broker or VPS evidence is in
the journal. The programmatic integration is `alpaca-py` against the Alpaca
paper Trading and Data APIs. No MCP integration exists or is claimed.

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
tools/build_notices.py        regenerates THIRD_PARTY_NOTICES.md from the lock
deploy/setup.sh   exact-SHA deployment
```

## License

MIT, in `LICENSE`. Dependency licensing is recorded in
`THIRD_PARTY_NOTICES.md`, generated from `requirements.lock` by
`tools/build_notices.py`. No code, UI, prompt, or asset from any reviewed
third-party trading project is present in this repository.

The approved design and task-level implementation plan are preserved under
`docs/superpowers/`.
