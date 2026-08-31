# Go live: the exact sequence

Every command is run by you, from the repo root, with the venv active.
Expected output is under each one. Stop at the first thing that does not match.

**Before anything: rotate the API keys** that were pasted into chat. Treat that
pair as burned.

---

## 0. Environment

`.env` must contain the **fresh, dedicated** account -- the event disqualifies
reused accounts.

```
ALPACA_ENV=scored
ALPACA_PAPER_TRADE=true
ALPACA_API_KEY=<new key>
ALPACA_SECRET_KEY=<new secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_EXPECTED_SCORED_ACCOUNT_ID=<the account number Alpaca returns>
ALPACA_EXPECTED_DEV_ACCOUNT_ID=<your old dev account number>
```

Keep every comment on its own line. systemd keeps inline `#` in values, and
that has already defeated one guard in this repo.

```bash
python -m glassbox.preflight
```
Expect nothing printed and exit 0. Any refusal lists the exact variable.

---

## 1. Confirm the account is the one you think it is

```bash
python tools/account_probe.py
```
Expect the account number, `ACTIVE`, `equity 100000`, an options level of 2+,
and `VERDICT: PRISTINE (never traded)`.

**If it does not say PRISTINE, stop.** A used account is ineligible.

---

## 2. Capture the evidence the gate requires

Four checks. Three need a command; the journal chain is computed.

```bash
python tools/account_probe.py --emit state/account_proof.json
```
Expect `artifact ... (matches expected)`. If it says DOES NOT MATCH, your
`ALPACA_EXPECTED_SCORED_ACCOUNT_ID` is wrong.

```bash
python tools/capture_alpaca_proof.py --out state/cli_proof.json
```
Needs the Alpaca CLI installed and on PATH. Expect `COMPLETE`. If the CLI is
absent this exits non-zero -- install it first, or capture MCP proof instead
with `tools/verify_mcp_surface.py`.

```bash
# read-only first
ALPACA_ENV=dev python tools/live_check.py

# then ONE capped order, on the DEV account
ALPACA_ENV=dev python tools/live_check.py --trade --notional 25 \
  --emit state/dev_venue_proof.json
```
Expect `LIVE CHECK PASSED` and `artifact written`. This places one small real
paper order and closes it -- it proves the order path works before the scored
account uses it. Hard ceiling is $50.

---

## 3. Assemble the release manifest

```bash
python tools/build_release_manifest.py
```

Expect either a refusal naming exactly what is missing and the command that
captures it, or:

```
RELEASE VERIFIED  written to .../state/release.json

Start the scored run with:
  export GLASSBOX_RELEASE_GATE=1
  export GLASSBOX_APPROVED_COMMIT_SHA=<sha>
```

It refuses on a dirty working tree -- commit or stash first. Evidence expires
after 24 hours; rerun this after that, or after any commit.

---

## 4. Start

```bash
export GLASSBOX_RELEASE_GATE=1
export GLASSBOX_APPROVED_COMMIT_SHA=<the sha it printed>

python main.py --dry-run
```
Expect `release gate: commit <sha>, options-only, paper`, then the account
line and the schedule. **It does not trade.**

Exit codes: 2 not paper, 3 journal chain broken, 4 network (transient),
5 wrong account, 6 release gate refused.

```bash
python main.py
```
That runs the schedule. Leave it running.

---

## 5. Watch it

```bash
python tools/verify_chain.py        # journal integrity
python tools/verify_submission.py   # every claim, re-checked
uvicorn dashboard.app:app --port 8080
```

The dashboard needs no credentials. It shows the decision lineage, the
equity curve, and the verification report.

If it stops taking trades, the journal says why. `POSITION_RECONCILE_FAULT`
clears itself once the venue and ledger agree exactly; `STATE_FAULT_LATCHED`
needs a human. `docs/OPERATIONS.md` maps every latch to its cause.

---

## Running unattended

Nothing has proven the process survives 66 hours on a laptop, and
`deployment_soak` is only required of a deployed run. Mitigate what it would
have caught:

```bash
caffeinate -is python main.py
```

Watch the Discord heartbeat. If the process dies, restart it -- state is
durable and it reconciles against the venue on the next tick.
