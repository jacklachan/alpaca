# Glassbox

An autonomous options trading agent where a language model writes the thesis,
a deterministic risk kernel decides whether it becomes an order, and a
hash-chained journal records both.

Built for the Alpaca AI Trading Agents Hackathon, 28 Aug – 4 Sep 2026.
Paper trading only. Nothing here is investment advice.

## The separation

```
ingest → features → thesis (LLM) → RISK KERNEL → execution → Alpaca
                                        ↓
                              decision journal (hash-chained)
```

The model proposes. It never executes. There is no code path from the thesis
layer to the broker that bypasses `kernel.py`.

## The thirteen invariants

Every plan is checked against all thirteen. Any failure refuses the plan and
writes the reason to the journal. One named test per invariant in
`tests/test_kernel.py`.

| # | Invariant | What it stops |
|---|---|---|
| 01 | Symbol allowlist | A hallucinated ticker reaching the broker |
| 02 | Bounded maximum loss | Any position whose worst case is not finite. Naked short options are refused unconditionally |
| 03 | Sleeve budget | Total option premium outstanding exceeding the convex cap |
| 04 | Daily burn | One bad day spending the whole week's ammunition |
| 05 | Concentration | Over-exposure to a single underlying |
| 06 | Position count | A book too large to reason about |
| 07 | Gross exposure | Equity margin leverage. All convexity is purchased, never borrowed |
| 08 | Drawdown kill switch | Continued risk-taking after the core sleeve breaks |
| 09 | Market-hours guard | Orders into a closed session. Options have no extended hours |
| 10 | Expiry guard | Holding into expiration mechanics |
| 11 | Idempotency | A network retry doubling a position |
| 12 | Sanity band | Fat-finger sizes and unit-confusion bugs |
| 13 | Order frequency | A runaway loop draining the account overnight |

### Honest notes on the risk model

Three places where the obvious claim would be an overclaim, and what is
actually true instead:

**Maximum loss is exact for options, estimated for everything else.**
For a long option it is the premium paid — knowable at entry, enforceable.
For equities and crypto a stop is *not* a bound, because price gaps through
stops. Those are estimated as stop distance × quantity × a gap multiplier and
labelled as estimates. The claim "every position had a computable maximum
loss" is true only of the options book, and is stated that way.

**The concentration limit for options is a sanity bound, not the risk
control.** Long options deliver large notional delta for small premium — that
is the leverage, and it is why we buy them. The binding constraints on the
convex sleeve are the premium caps (03 and 04), because loss is bounded by
premium regardless of delta.

**The kill switch is per-sleeve, deliberately.** The convex sleeve is
*permitted* to go to zero; that is a designed ~50% outcome, not a failure. A
single portfolio-wide switch tight enough to catch a broken core sleeve would
fire on the convex sleeve doing exactly what it was built to do. Core sleeve
drawdown trips at 6%, with a 15% portfolio backstop behind it.

**Kill-switch re-arm rule.** The switch latches. Re-arming is a human decision
requiring two team members to agree, and the decision plus its reasoning is
written to the journal before trading resumes. Decided in advance so it is not
decided under pressure.

## The journal, and what it does and does not prove

Append-only JSONL. Each entry carries the SHA-256 of the entry before it, so
editing history in place breaks the chain and `tools/verify_chain.py` detects
it.

What it does **not** prove: that we did not regenerate the entire chain from
altered content. We control every input to the hash, so a self-generated chain
is self-attestation, not third-party attestation.

Two mechanisms close most of that gap, and both are implemented:

1. **External anchoring.** The current head hash is published to Discord
   hourly. Those messages carry server-side timestamps we do not control, so
   an anchor accepted at a given time constrains every entry written before it.
2. **Broker reconciliation.** Every order entry carries Alpaca's own
   `broker_order_id` and broker-side timestamp. Judges hold the account ID and
   can reconcile the journal against records we cannot edit.

The defensible claim is "reconcilable against broker-side records", not
"tamper-proof". We make the former.

## Setup

```bash
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env    # then fill in your own keys
pytest -q
```

Two Alpaca paper accounts are required, and they must stay separate:

- **dev** — everything is tested here.
- **scored** — created fresh for the competition, never used for testing.
  Its trade history must be clean when the window opens.

`ALPACA_PAPER_TRADE=true` is asserted at startup. The process refuses to boot
against a live account.

Keys live in the environment. They are never committed, never written to the
journal, and are scrubbed from any recorded terminal output.

## Handover

- `HANDOVER.md` — architecture, how to run it, what the pieces do.
- `HANDOVER-2.md` — **start here.** What changed on 29 Aug, the fourteen audit
  fixes, what is still not done, and what to do next.
- `DECISIONS.md` — why every choice was made, including where we were wrong.
- `PLAN.md` — the competition plan.
- `SOCIAL.md` — the social criterion, with drafted posts.

## Running it

```bash
python -m glassbox.preflight     # refuses to start on a bad .env
python main.py --dry-run         # wire up, verify the account, print schedule
python main.py --once            # a single tick
python main.py                   # the agent
```

Operational checks, all runnable without a broker connection:

```bash
pytest -q                        # 166 tests
python tools/crash_drill.py      # 13 checks: real SIGKILL, real restarts
python tools/verify_chain.py     # re-hash the journal end to end
python tools/env_parity.py .env  # systemd vs dotenv agreement
python tools/live_check.py       # the checks that need real network access
sudo bash tools/soak.sh          # on the VPS: systemd in the loop
```

The dashboard — the Application URL a judge opens — is read-only, holds no
credentials, and has no write path:

```bash
uvicorn dashboard.app:app --host 0.0.0.0 --port 8080
```

## The measurement date, and why it reshaped the strategy

Alpaca's guidelines measure **total equity as of EOD Thursday 3 September**,
not at Friday's open. The August payrolls print (Fri 4 Sep, 08:30 ET) therefore
falls *outside* the scored window.

We originally built the flagship options trade around payrolls landing sixty
minutes *inside* it. `next_event()` now refuses to return any catalyst at or
after the measurement, and CI asserts it, because buying convexity for payrolls
would have meant paying for a payoff that arrives after the account has already
been photographed.

## Repository layout

```
glassbox/
  config.py     every threshold, in one place
  env.py        fail-closed env reads (systemd vs dotenv parity)
  preflight.py  startup gate; runs as systemd ExecStartPre
  schema.py     the trade-plan schema (Pydantic v2)
  kernel.py     the thirteen invariants
  journal.py    hash-chained append-only log + verifier + anchoring
  macro.py      the scored-window calendar and the measurement boundary
  ids.py        deterministic client_order_id
  strategies/   core (passive), crypto (24/7), event_vol (the options trade)
dashboard/
  app.py        read-only journal + equity view. No credentials, no writes.
tools/
  crash_drill.py  SIGKILL/restart drill, 13 checks, no broker needed
  soak.sh         the same on the VPS, with systemd in the loop
  env_parity.py   systemd vs python-dotenv disagreement detector
  verify_chain.py standalone journal verifier
  panic.sh        flatten everything via the Alpaca CLI
tests/            142 tests
```

## Disclosure of pre-event work

Required by the FAQ ("If pre-event work is permitted, must it be disclosed in
the README or final submission? Yes.").

All application code in this repository was written during the hackathon
window. No pre-existing private library is vendored or depended on. Third-party
dependencies are the public packages listed in `requirements.txt`.

## Status

Complete and tested: the full autonomous loop — reconcile → manage → propose →
kernel → execute → journal — plus the read-only dashboard, CI, and the
crash-recovery drill. 166 tests.

Proven: the recovery *logic*, via `tools/crash_drill.py` (real SIGKILL on real
child processes).

Not yet proven: the *host*. Until `tools/soak.sh` has run green on the VPS,
"runs unattended for four days" is a claim rather than a fact.
