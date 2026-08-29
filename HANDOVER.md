# Handover

Everything you need to pick this up cold. No prior context assumed.

Read `DECISIONS.md` for *why* things are the way they are — this file is
*what exists, how to run it, and what to do next.*

---

## 0 · The situation in one screen

We are building **Glassbox** for the Alpaca AI Trading Agents Hackathon
(28 Aug – 4 Sep 2026, $6,300 pool).

An autonomous agent trades a fresh $100,000 Alpaca **paper** account. It starts
Monday 31 Aug 09:30 ET and the account is measured at **EOD Thursday 3 Sep**
(Alpaca's guidelines; the window nominally runs to Fri 4 Sep 09:30). Judging is on
five criteria: **P&L, Technology Implementation, Creativity & Originality,
Presentation & Execution, and Social engagement.**

**The thesis:** an agent that trades *scheduled volatility events*. There is a
macro catalyst on four of five days in the scored window, implied volatility is
at 2026 lows, and the payrolls report lands Friday 08:30 ET — one hour before
measurement. We buy defined-risk convexity into catalysts and flatten after.

**The architecture:** an LLM proposes, a deterministic risk kernel decides, and
a hash-chained journal records both. The model has no path to the broker.

**Current state:** the autonomous loop is complete and tested. 98 tests pass.
Nothing is deployed and no real order has ever been placed.

Repo: <https://github.com/jacklachan/alpaca>

---

## 1 · Get it running in five minutes

```bash
git clone https://github.com/jacklachan/alpaca.git
cd alpaca
python -m venv .venv && . .venv/Scripts/activate      # Windows
pip install -r requirements.txt
cp .env.example .env          # then fill it in — see below
pytest -q                     # expect 98 passed
```

`.env` needs:

```
ALPACA_ENV=dev                # dev | scored  -- NEVER point at scored while testing
ALPACA_PAPER_TRADE=true
ALPACA_API_KEY=PK...          # from app.alpaca.markets -> API
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ANTHROPIC_API_KEY=            # optional; the agent trades fine without it
DISCORD_WEBHOOK_URL=          # heartbeat, alerts, journal anchors
```

`.env` is gitignored. **Never put real keys in `.env.example` — that file IS
tracked.** (This already happened once and was caught before it pushed.)

Then, in order:

```bash
python -m tools.smoke_test    # verifies the account; read-only
python main.py --dry-run      # wires everything up, prints the schedule, exits
python main.py --once         # one real tick
python main.py                # the agent
```

Python 3.14 works. `alpaca-py`, `pydantic`, `APScheduler` all install cleanly.

---

## 2 · What each file is

```
glassbox/
  config.py       186  every threshold in one place. Read this first.
  schema.py       131  TradePlan / OptionLeg / Verdict (Pydantic v2)
  kernel.py       362  THE RISK KERNEL — thirteen invariants
  journal.py      164  hash-chained append-only log + verifier + anchoring
  ids.py           16  deterministic client_order_id
  macro.py        131  the macro calendar + session/measurement arithmetic
  broker.py       ---  Alpaca wrapper, rate limiter, reconciliation
  execute.py      286  plans -> orders; legged strangle; partial-fill unwind
  manage.py       227  stops, targets, time exits, expiry close-out, kill switch
  scheduler.py    233  APScheduler jobs, heartbeat, the run loop
  data.py         230  market data + pure feature functions
  thesis.py       182  the LLM layer (optional)
  strategies/
    event_vol.py  329  THE STRATEGY — calendar-triggered, picks the expiry
    core.py        92  passive fixed-weight ETF allocation
    crypto.py     117  24/7 + the payrolls-window response
main.py           121  entry point
tools/
  smoke_test.py   230  hour-zero account verification
  verify_chain.py  49  standalone journal verifier (the demo)
  panic.sh        ---  flatten everything via the Alpaca CLI
deploy/
  glassbox.service     systemd unit, Restart=always
  setup.sh             VPS provisioning
tests/            98 tests
```

**The two files that matter most:** `kernel.py` (what judges will read) and
`strategies/event_vol.py` (the actual edge).

---

## 3 · How it works, in the order data flows

1. **`scheduler.equity_tick()`** fires every minute during market hours.
2. **`broker.reconcile()`** re-reads positions and orders from Alpaca and
   rebuilds `PortfolioState` from scratch. *Local state is never trusted.*
3. **`manager.tick()`** evaluates every open position: stops, targets, time
   exits, expiry close-out, and the kill switch.
4. Each strategy's **`propose_from_state()`** returns zero or more `TradePlan`s.
   Zero is a normal, healthy outcome.
5. **`kernel.review(plan, state)`** returns a `Verdict` — approved or refused,
   always with a reason string. **There is no code path around this.**
6. Approved plans go to **`ExecutionEngine.execute()`**.
7. Every step is written to the **journal**.

Jobs on the clock (all ET, UTC internally):

```
equity_tick    mon-fri 9-15, every minute
crypto_tick    every 5 min, 24/7
eod_manage     mon-fri 14:30
daily_review   mon-fri 16:15   (LLM writes a summary; cannot place orders)
heartbeat      every 15 min    -> Discord
anchor         hourly          -> posts journal head hash to Discord
```

---

## 4 · The thirteen invariants

In `kernel.py`, one named test each in `tests/test_kernel.py`.

| # | Invariant |
|---|---|
| 01 | Symbol allowlist |
| 02 | Bounded maximum loss — naked shorts refused unconditionally |
| 03 | Convex sleeve premium cap |
| 04 | Daily burn cap (with a separate event-trade allowance) |
| 05 | Concentration — capital for equity/crypto, delta sanity for options |
| 06 | Position count |
| 07 | Gross exposure — no equity margin leverage |
| 08 | Drawdown kill switch — **per sleeve** |
| 09 | Market-hours guard |
| 10 | Expiry guard |
| 11 | Idempotency |
| 12 | Sanity band |
| 13 | Order frequency — runaway-loop breaker |

**Three things not to "fix" without reading `DECISIONS.md` §4 first**, because
each looks wrong and is deliberate:

- The options delta bound (05) is *deliberately loose* at 200%. The premium
  caps (03/04) are the real risk control — long options carry huge notional
  delta for small premium, and that is the point.
- The kill switch (08) does **not** trip when the convex sleeve goes to zero.
  That is a designed ~50% outcome, not a failure.
- Long-only is enforced in the **kernel**, not the schema, so a hostile plan
  gets refused with a reason string and a journal entry instead of dying as a
  validation error. That refusal is the demo.

---

## 5 · The expiry decision (the actual edge)

`select_expiry()` in `strategies/event_vol.py`. Do not hard-code an expiry —
it reads the live term structure and decides.

Measured from the live chain on 29 Aug:

| Expiry | ATM IV | Spread | Sessions left at measurement | Verdict |
|---|---|---|---|---|
| 3 Sep | 9.9% | 4.0% | 1 | refused — expires on the measurement day |
| **4 Sep** | **10.7%** | 4.1% | **2** | **the pick, 3.0×** |
| 8 Sep | 9.4% | 5.3% | 3 | viable, 2.0× |
| 9 Sep | 9.7% | 6.0% | 4 | refused — wider than 5.5% |
| 11 Sep | 10.6% | 4.0% | 6 | viable, 1.0× — the baseline |

**This changed when the measurement date was corrected to EOD Thu 3 Sep.**
The old pick was 8 Sep, on the argument that the Labor Day weekend made it
cheap in business time. Measured Thursday, we never live to see the holiday,
so that discount is time we pay for and never use.

4 Sep wins on the arithmetic — 3.0× the convexity per dollar of the 11 Sep
contract. The better argument is that **4 Sep is the payrolls expiry, so its
IV peaks at Thursday's close, the exact moment we are measured**: the event
premium is fully priced and has not decayed, because the print is the next
morning. Marked on the run-up, flat before the risk.

**Re-run `python -m tools.smoke_test` Monday during market hours.** Saturday
spreads are meaningless. If 4 Sep quotes above 5.5% in session, the selector
falls back to 11 Sep on its own — that is intended, not a bug.

---

## 6 · What is NOT done

**Code:**

- [ ] **Dashboard** — FastAPI + one server-rendered HTML page. Equity curve,
      per-sleeve attribution, decision timeline. **Required**: "Application
      URL" is a submission field. No React, no build step.
- [ ] **Deployment** — `deploy/setup.sh` is written but has never been run.
- [ ] **Crash-recovery drill** — kill the process mid-tick, confirm it resumes
      with correct positions. Do it twice. Never done.
- [ ] The `_execute_single` path (equity/crypto) is tested against a fake
      broker only; the options path is better covered.

**Not code, and more urgent:**

- [ ] **Second paper account.** The current one (`PA3PB02CJ4F6`) is the **dev**
      account and will get dirty. Create a *fresh* one for scoring, never test
      against it, and cut over Monday morning. Reused accounts are explicitly
      ineligible.
- [ ] **Social posts.** A *scored criterion* plus a separate $1,000 track. Up
      to 5 links to X/LinkedIn posts tagging **@lablabai** and **@AlpacaHQ**.
      Most teams will score zero here. Several days of the window are already
      gone.
- [ ] **Submission package**: public repo (done), cover image, video, slide
      deck, one-page write-up covering "AI logic, risk gates, Alpaca
      infrastructure", and the **Alpaca account ID**.
- [ ] **Discord question is posted and awaiting an answer** (see §7).
- [ ] **Algo Trader Plus ($99)** — buys real OPRA quotes instead of the
      indicative feed. Undecided.

---

## 7 · Open questions that change decisions

**1. When exactly is equity measured?** ~~Asked on Discord.~~ **ANSWERED.**
Alpaca's guidelines: total equity **as of EOD Thursday 3 Sep**, with Sep 3
exercises and assignments reflected in that value. The code, the calendar and
`next_event()` all use this, and CI asserts it.

The open sub-question that remains: whether to **close the convex position into
cash before Thursday's close**. Cash has zero marking ambiguity; an option
marked off an indicative feed does not. Against that, closing forfeits exactly
the peak event premium that makes the 4 Sep expiry attractive. Current
behaviour is to hold. Decide this deliberately by Wednesday, not at 15:50 on
Thursday.

Until answered, `macro.MEASUREMENT_ET` assumes 09:30. Being early is
recoverable; being late is not.

**2. How does Alpaca mark paper option positions for the equity figure?**
Undocumented. This is the load-bearing unknown for the whole convex thesis. The
Monday validation trade (§8) answers it empirically.

---

## 8 · The Monday runbook

Go-live is **Mon 31 Aug 09:30 ET = 19:00 IST**.

**Before the open:**

1. Create the fresh scored account if not already done. Record the account ID.
2. Put its keys in `.env`, set `ALPACA_ENV=scored`.
3. `python -m tools.smoke_test` — with `ALPACA_ENV=scored` it additionally
   asserts equity is exactly $100,000.00 and the position list is empty.
4. `python main.py --dry-run` on the VPS.
5. Go-live checklist, all binary:
   - [ ] equity reads exactly $100,000.00, positions empty
   - [ ] 98/98 tests green
   - [ ] process survives SIGKILL and resumes with correct positions
   - [ ] 60 minutes of unbroken heartbeat in Discord
   - [ ] chain verifies seq 1 → head
   - [ ] account ID recorded in the journal and the submission draft

**09:30 ET — start the agent. CODE FREEZE.** From here, config values and the
panic script only. Most teams keep shipping into a live account and break
something Wednesday; our advantage is that the journal proves we did not.

**09:35 ET — the options path validation trade.** Buy one cheap ATM SPY call,
confirm the fill, confirm it appears with a mark, confirm the position manager
and expiry guard see it, then close it. Budget ~$400 as tuition. Options
markets are closed all weekend, so **the sleeve carrying the entire thesis will
have completed zero real orders before this**. It is a trade, not a code
change, so it does not violate the freeze — and it answers question 2 in §7.

**Tue–Thu:** monitoring rota only. Record the video, write the deck, post
daily. US market hours are 19:00–01:30 IST, so make the alerting good enough
that nobody has to be awake.

**Thu 14:00–15:30 ET:** the event trade establishes itself. It is
calendar-triggered and deterministic — nobody places it by hand. That was a
deliberate fix: the original plan had a human doing it, which would have broken
the autonomy claim on the most important trade of the week.

**Fri:** submit by 20:30 IST. Target 18:00 and do not use the margin.

---

## 9 · Gotchas that will bite you

- **`.env.example` is tracked.** Real keys go in `.env` only.
- **Pushing to GitHub through Claude may 403** with "not in this session's
  authorized repository set". That is Claude Code's git proxy, not GitHub --
  collaborator access will not fix it. Either add the repo to the session's
  sources, or push from a normal terminal after `gh auth login`.
- **The Alpaca CLI syntax is `cancel-all` / `close-all` and `--json`**, not
  `cancel --all` / `--output json`. panic.sh was written wrong first and would
  have failed exactly when it was needed.
- **The scored account must stay pristine.** One test order against it and it
  is arguably ineligible.
- **Do not "fix" the loose options delta bound** — see §4.
- **Expiry close-out is 14:30 ET, not 15:45.** Alpaca stops accepting options
  orders at 15:30 on expiry day and non-trade activity settles T+1, i.e. after
  the snapshot. There is a test guarding this.
- **Paper accounts partially fill ~10% of orders at random.** This is why the
  strangle is legged as two single-leg orders with an unwind rule. An
  unbalanced strangle into payrolls is a directional bet the risk model never
  approved.
- **APScheduler defaults are dangerous** on a one-minute tick. `max_instances=1,
  coalesce=True` are load-bearing.
- **Everything internal is UTC**, converted once at the edge. Market state comes
  from Alpaca's `get_clock`, never hand-rolled holiday logic.
- **The kill switch latches** and fails closed if its state file is unreadable.
  Only a human re-arms it, and the re-arm rule is in `README.md`.
- **Never put a naked short option anywhere in the code**, even in a comment
  that looks like an example. It is the one thing that would cost credibility
  instantly with judges from a brokerage.

---

## 10 · If you only do five things

1. **Create the scored account** and keep it clean.
2. **Post on X/LinkedIn today.** Scored criterion, $1,000 track, ~2 hours total,
   and most teams will score zero.
3. **Deploy to a VPS and run the crash-recovery drill.** A laptop going to
   sleep on Tuesday night is the single most common way these agents die.
4. **Build the minimal dashboard.** Application URL is a submission field.
5. **Run the smoke test Monday in session** and let the selector pick the
   expiry off real spreads.

---

## 11 · The pitch, so the story stays consistent

> Glassbox is an autonomous agent that trades scheduled volatility events. It
> buys convexity into catalysts when implied volatility is cheap, a
> deterministic kernel bounds every position's loss before it opens, and it is
> measured holding peak event premium the evening before payrolls lands.

**Say:** "The model proposes. It never executes." · "Every option position had
an exact maximum loss before it opened." · "We froze the code at Monday's open
and the journal reconciles against Alpaca's own broker-side records." · "Four
days with a ranked prize is a tournament, not a portfolio. We sized for the
tail and we bounded it."

**Don't say:** "multi-agent framework" (five other teams will) · "our AI
predicts the market" · any Sharpe ratio on four days of data · anything about
scaling to millions of users.

**The strongest 30 seconds of video:** feed the running system `sell 400 naked
SPY calls`, show the kernel refusing it in the live journal with the reason
string. Do this on a **dev instance running the identical kernel module**, not
against the scored account — injecting synthetic plans into production would
violate the freeze and imply a path exists to feed the agent arbitrary plans.
Say on camera that it is the same module.

**The closing shot:** run `tools/verify_chain.py`, show it pass. Edit one byte
of the log. Run it again. Show it fail.

---

Paper trading only. Nothing here is investment advice.
