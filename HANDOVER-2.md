# Handover — 29 August, evening

For Mohit, or whoever picks this up next. Assumes you have read `HANDOVER.md`
(the architecture) and `DECISIONS.md` (why the design is what it is). This file
covers only what changed today, what is still open, and what I would do next.

Read §1 and §5 if you read nothing else.

---

## 1 · The two things that matter most

### The measurement date moved, and it invalidated the flagship trade

Alpaca's official guidelines say:

> "We will be looking at the portfolio's total equity **as of EOD Thursday
> Sep 3rd**."

We had `MEASUREMENT_ET = Fri 4 Sep 09:30` with `MEASUREMENT_IS_CONFIRMED =
False`. The whole options thesis was built around the payrolls print landing 60
minutes *inside* the window. It lands about 16.5 hours *outside* it.

Worse, `next_event()` still returned payrolls, so on Wednesday the agent would
have spent its convexity budget buying a catalyst that resolves after the
account is photographed. That is the most expensive kind of wrong, because it
looks like a thesis right up until it scores nothing.

**The correction improved the trade rather than damaging it.** The selector now
picks the **4 Sep** expiry, and the reason is better than the old one:

> 4 Sep is the payrolls expiry, so its implied volatility peaks at Thursday's
> close — the exact moment we are measured. The event premium is fully priced
> and has not decayed, because the print is the next morning. We are marked on
> the run-up and flat before the risk.

That is worth saying out loud in the demo. It is the only part of the strategy
that could not have been arrived at without reading the rules properly.

### Fourteen real defects, all in code with 142 passing tests

A full audit found fourteen, twelve reproduced concretely. The five that would
have cost money:

| What | What it would have cost |
|---|---|
| A repriced leg lost its predecessor's fill | Ordered **14 contracts against an approved 10** — a 21.9% breach of the max-loss invariant — while unwinding correctly filled strangles |
| `event_premium_today` hard-coded to `0` | The daily event cap was a per-*order* cap. Two $16k strangles both passed an $18k limit |
| Working orders invisible to the budget | Six resting orders worth $21k reported as $0; a seventh plan approved on top |
| Catalyst de-dup compared `"SPY"` against `"ADP National Employment"` | Same print re-traded every tick: **$24,720, the whole convex sleeve, in two minutes** |
| Portfolio backstop at 15% | Fired on the convex sleeve doing exactly what it is designed to do, latched, flattened at the low, halted the week |

And three things that were dead code while looking alive: **the entire crypto
sleeve** (no data client, `crypto_tick` never proposed, quantities truncated to
zero by `int()`), **IWM** (reconcile priced only symbols already held, so a
symbol not in the book could never be bought — 30% of the core sleeve idle all
week), and **`ThesisLayer.propose()`**, which is still never called (§4).

Full detail is in the commit message for `2145d6a`.

---

## 2 · What is done

- **166 tests** pass, up from 142. The 23 new ones are regressions for the
  audit findings, in `tests/test_audit_regressions.py`.
- **Crash drill 13/13** — `python tools/crash_drill.py`. Real `SIGKILL` on real
  child processes, no broker needed.
- **CI** on every push: tests, kernel invariants, crash drill, dashboard boot,
  plus guards for the mistakes that cost the whole competition rather than one
  test (committed credentials, paper-only guards, sleeve budgets summing to
  $100k, the measurement date).
- **Dashboard** at `dashboard/app.py` — read-only, no credentials, no write
  routes, both asserted by tests. Never 500s on a missing, empty, torn or
  tampered journal. This is the Application URL.
- **`SOCIAL.md`** — five drafted posts. Social is one of five judged criteria.
- **Docs reconciled with the code.** `DECISIONS.md`, `PLAN.md` and
  `HANDOVER.md` all still argued from the Friday date; the expiry table was
  scored against it. Fixed, plus two cp1252 bytes that rendered as mojibake.

## 3 · What is NOT done — the gap that threatens Monday

**No order has ever been placed, and nothing is deployed.**

Every test in this repo runs against a fake broker. That was not a choice: the
environment this work was done in blocks `paper-api.alpaca.markets`, and so
does the VM behind the desktop bridge. Three environments, all blocked.

So the execution path is *proven in logic and unproven in fact*. Until an order
has gone to a venue and come back filled, "the execution path works" rests on
tests against a fake.

**Run this from an ordinary terminal, today:**

```bash
python tools/live_check.py            # read-only: account, options level, data
python tools/live_check.py --trade    # places and closes ONE ~$50 crypto order
```

It refuses to run against the scored account. Crypto trades 24/7, so `--trade`
works at the weekend. It checks the option chain too, though expect empty
quotes until Monday — weekend option spreads are meaningless.

**Then deploy and soak:**

```bash
bash deploy/setup.sh                  # on a $5 VPS, as root
sudo bash tools/soak.sh               # systemd in the loop, kills the process
```

`soak.sh` checks the unit file for the `StartLimitIntervalSec` misconfiguration
that would otherwise let a crash loop give up permanently. Until it passes on
the actual box, "runs unattended for four days" is a claim.

## 4 · Open decisions — yours, not mine

**The LLM proposal path does not run.** `ThesisLayer.propose()` is never called;
only `daily_review` is wired in. The architecture story — "the model proposes,
the kernel disposes" — is therefore half true today: the kernel is real, and
the only thing proposing is deterministic strategy code. Either wire it into
the 04:00 premarket job or say plainly in the write-up that the model's role is
research and review. **Do not claim the proposal loop runs when it does not.**
A judge who reads the scheduler will notice, and that costs more than the
feature is worth.

**Close the convex position into cash before Thursday's close?** Cash has zero
marking ambiguity; an option marked off an *indicative* feed does not. Against
that, closing forfeits exactly the peak event premium that makes the 4 Sep
expiry attractive. Current behaviour is to hold. Decide this by Wednesday, in
writing, not at 15:50 on Thursday.

**`CORE_DRAWDOWN_KILL_PCT = 6%`** on a passive $65k beta sleeve is tight for a
four-day window. A 2% down day in SPY takes you a third of the way there. Worth
a look before Monday.

## 5 · What I would do next, in order

1. **`python tools/live_check.py --trade`.** Fifteen minutes. It is the single
   highest-value thing left, and everything else assumes it passes.
2. **Deploy to a VPS and run `soak.sh`.** Not a laptop — a laptop sleeping on
   Tuesday night is the most common way these agents die.
3. **Create the scored account.** Fresh $100k, options enabled, level confirmed
   by *reading* `options_approved_level` rather than placing a test order, and
   then left completely untouched until Monday 09:30 ET.
4. **Post the systemd bug from `SOCIAL.md`.** It is a scored criterion, days
   are gone, and that post is useful to people who have never heard of this
   hackathon — which is what actually spreads.
5. **Decide the thesis-layer question in §4** and make the write-up match
   whatever you decide.

## 6 · Recommendations for making this genuinely better

**Record the demo mid-week, not Thursday night.** Filming on Wednesday while
the agent runs untouched *is* the proof of autonomy. Filming on Thursday after
a week of edits proves nothing, and Thursday is when the flagship trade goes on.

**Lead the demo with the kernel refusing a hostile plan.** Feed it *sell 400
naked SPY calls* live and show the refusal in the journal with its reason
string. Fifteen seconds, and no other team will do it. The equity curve is what
everyone shows; the refusals are what nobody has.

**Say "reconcilable against broker records", never "tamper-proof".** We control
every input to the hash chain, so it proves the file has not been casually
edited — not that we are honest. The Discord anchors and Alpaca's own order IDs
are what a judge can actually check. `journal.py` already says this; keep the
pitch matching it.

**Do not oversell the barbell.** With the convex sleeve at 25%, a bad week is
a −25% finish, and P&L is a judged criterion. The honest framing is that we
sized for probability-of-placing rather than expected return, and bounded the
downside deliberately. That reads as competent. "We're up X%" before the
snapshot reads as lucky, and may not survive to Thursday.

**Freeze the code Monday at 09:30 ET.** Everything after that is monitoring.
The audit above found fourteen defects in code that looked finished; assume the
next change you make under time pressure carries the same risk, and that
Wednesday afternoon is the worst possible time to discover it.

---

## Quick reference

```bash
python -m glassbox.preflight          # refuses to start on a bad .env
python main.py --dry-run              # wire up, verify account, print schedule
python -m pytest -q                   # 166
python tools/crash_drill.py           # 13/13, no broker needed
python tools/live_check.py --trade    # the checks that need real network
python tools/verify_chain.py          # re-hash the journal end to end
uvicorn dashboard.app:app --port 8080 # the Application URL
sudo bash tools/soak.sh               # on the VPS, systemd in the loop
```

Exit codes from `main.py`: `2` not paper trading · `3` journal chain broken ·
`4` cannot reach Alpaca, transient · `5` account check failed, needs a human.
