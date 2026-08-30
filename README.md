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

Do not describe either gate as complete until its broker or VPS evidence is in
the journal. The repository uses Alpaca's Trading/Data APIs and local CLI
tools. No additional broker integration is claimed.

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
- The journal is append-only and SHA-256 chained. This detects edits to the
  recorded file; it is not called tamper-proof. Broker IDs and timestamps make
  order claims reconcilable against records we do not control.

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
  state.py        atomic fail-closed JSON persistence
  scheduler.py    options-only scored policy and development schedule
  strategies/     deterministic option generators plus dev-only fixtures
dashboard/app.py  credential-free, read-only journal dashboard
tools/live_check.py  bounded dev venue proof
deploy/setup.sh   exact-SHA deployment
```

The approved design and task-level implementation plan are preserved under
`docs/superpowers/`.
