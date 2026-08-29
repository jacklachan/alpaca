# Glassbox — Plan v2

Supersedes the original plan. Rewritten after checking every claim against the
published event page, Alpaca's docs, and the BLS calendar — and after reading
what the field has actually submitted.

Two things changed the shape of this project:

1. **The event page carries requirements the original plan never saw**, including
   a scored criterion the plan was blind to.
2. **The risk-kernel architecture is no longer a differentiator.** Five visible
   competitors lead with it. It is now the price of entry, not the pitch.

---

## 1 · What is actually required

From the event page, not inferred:

| Requirement | Status |
|---|---|
| Autonomous agent on Alpaca's Trading API | Core requirement |
| **Must use Alpaca's MCP server or CLI** | Core requirement |
| **All strategies must incorporate options trading** | Core requirement |
| Fresh paper account, $100,000, never used for testing | Required for judging |
| Alpaca account ID submitted | Required for judging |
| Public GitHub repo · Application URL · cover image · video · slide deck | Submission fields |
| One-page write-up: AI logic, risk gates, Alpaca infrastructure | Required |

**Judging criteria (five, published):** P&L Performance · Technology
Implementation · Creativity & Originality · Presentation & Execution ·
**Social engagement**.

Note what is *not* there: "robustness" is not a criterion. The original plan
optimised for it. It is instrumental to the others, not scored directly.

**Prize pool $6,300** — 1st $2,500 (+$300 Featherless credits), 2nd $1,500,
3rd $1,000, plus two $500 social-engagement prizes.
**Submission deadline: Fri 4 Sep, 20:30 IST** (15:00 UTC / 11:00 ET).
**Teams: 1–6.**

### Confirmed facts

- **NFP: Fri 4 Sep 2026, 08:30 ET.** Verified against the BLS September schedule.
  (It is the *August* report, released in September — say it that way.)
- **VIX printed 14.1–14.2 on 28 Aug**, its 2026 low. "Convexity is cheap" is
  verified, not assumed. Supporting: 2026 is a midterm year, mid-Aug to mid-Oct
  is historically choppier, and markets are repricing a new Fed chair — which
  makes a payrolls print unusually rate-path-relevant.
- **Options are enabled by default on Alpaca paper accounts.** The original
  plan's top risk (Med/Critical, gating hour zero) drops to Low.
- **Alpaca stops accepting options orders at 15:30 ET on expiration day.**
- **Paper accounts partially fill ~10% of orders at random.**

### The one open question

The Q&A says equity is photographed Fri 09:30 ET. The event page says
submissions close 11:00 ET and judges evaluate P&L from the account ID. If
measurement is at or after 11:00, the market has been open ninety minutes and
marks are reliable — which weakens the case against a 4 Sep expiry.

**Ask on Discord today.** Until answered, 11 Sep expiry stands, because it is
robust to both answers.

---

## 2 · The strategy

> **Glassbox is an autonomous agent that trades scheduled volatility events.**

Not "an agent with guardrails that happens to trade options." The calendar is
the alpha source.

### Why this, and why now

Implied volatility is at its cheapest of the year. The scored window contains a
scheduled macro catalyst on four of five days:

| Day | Catalyst | Time ET |
|---|---|---|
| Mon 31 Aug | Month-end rebalancing flows | close |
| Tue 1 Sep | ISM Manufacturing PMI | 10:00 |
| Wed 2 Sep | ADP National Employment | 08:15 |
| Thu 3 Sep | ISM Services + jobless claims | 08:30 / 10:00 |
| **Fri 4 Sep** | **Employment Situation (NFP)** | **08:30** |

The agent's job, every day: measure whether implied volatility is cheap
relative to realised, check the calendar for a catalyst inside the option's
life, and if both hold, buy defined-risk convexity and flatten after the event.

That is a real, nameable, testable edge — the volatility risk premium is
compressed and the catalysts are scheduled. It is options-native, so the core
requirement is satisfied by the *concept* rather than by a sleeve bolted on.
And it gives the submission a narrative: five days, five catalysts, one climax.

### The structural edge nobody else has

**At 08:30 ET Friday the payrolls number lands. The equity market is closed
until 09:30. Crypto is not.**

Alpaca runs its own crypto venue — 24/7, full-quality free data, no IEX
partial-feed problem. In the sixty minutes between the print and the
measurement, the agent can trade the reaction while every equity-only
competitor's book is frozen at Thursday's close.

This is the single most defensible thing in the project. It exploits the exact
timing of this competition, needs no paid data, and produces something
happening *live* inside the window that decides the score.

Honest caveat, stated in the pitch rather than hidden: BTC's beta to payrolls
is real but noisy. It is sized as a defined-risk expression, not a conviction
bet — and the kernel enforces that.

### Sizing: why a tournament is not a portfolio

A ranked prize is a step function. Fourth of a hundred pays exactly what
fortieth pays. Under a step-function payoff, maximising expected return and
maximising probability-of-placing are different objectives, and only the second
is paid. With four trading days the winning P&L will be an outlier from the
right tail.

So variance is not the enemy — but *unbounded* variance is, because a deeply
negative finish destroys everything else on the scorecard. What we want is
asymmetric variance: capped downside, uncapped upside. Long options are the one
instrument class that gives that natively, and the cap is enforceable by a risk
check rather than by discipline.

---

## 3 · Capital allocation

| Sleeve | Allocation | Job |
|---|---|---|
| Event / convex | **$25,000** | Long premium only. The strategy. |
| Crypto | **$10,000** | 24/7. Carries the Friday 08:30 window. |
| Core | **$65,000** | Passive, fixed-weight, buy-and-hold. Financing, not alpha. |

Changed from the original 80/15/5. Options are a core requirement and the
challenge is titled *Options Alpha Agents*; 85% of capital in non-options
instruments scored on none of the five criteria.

**The core sleeve is deliberately passive** — fixed-weight SPY/QQQ/IWM bought
at Monday's open and held. Twenty lines, unbreakable, and the write-up says
plainly that we declined to pretend to edge we did not have. That is a
strength, not an omission.

### Rules the kernel enforces

- Long premium only. No naked short options, ever. This is what makes maximum
  loss knowable at entry.
- $25,000 total premium outstanding, checked portfolio-wide before every order.
- $8,000 of new premium per day, with a separately budgeted $18,000 allowance
  for a calendar-triggered event trade.
- SPY and QQQ only, strikes within ~2% of spot, 3–10 trading days to expiry.
- **Never hold to expiry — force-close at 14:30 ET**, ahead of Alpaca's 15:30
  cutoff. Non-trade activity syncs the following day, i.e. after the snapshot.

---

## 4 · Architecture

```
ingest → features → thesis (LLM) → RISK KERNEL → execution → Alpaca
                                        ↓
                              decision journal (hash-chained)
```

The model proposes. It never executes. No code path from the thesis layer to
the broker bypasses `kernel.py`.

**Thirteen invariants**, one named test each — see `README.md` and
`tests/test_kernel.py`. Thirteen, not twelve: the original set had no
order-frequency breaker, so nothing stopped the agent generating hundreds of
distinct plans overnight.

Corrections already implemented in code:

- Expiry force-close 15:45 → **14:30 ET** (Alpaca's cutoff is 15:30).
- Kill switch split **per sleeve**. A portfolio switch at 88% fired in the
  plan's own modal scenario, because the convex sleeve expiring worthless is a
  designed ~50% outcome. Core drawdown trips at 6%; 15% portfolio backstop.
- Daily burn cap given an **event-trade exemption**. The original $5k/day made
  the flagship trade impossible — the kernel would have refused the trade the
  plan was built around.
- Concentration split: **capital** concentration for equity/crypto, a wide
  **delta sanity bound** for options. A single 25% delta limit refuses every
  option trade, because long options carry large notional delta per dollar of
  premium. The premium caps are the real control.
- `max_loss` computed **per instrument** — exact for long options, gap-adjusted
  estimate for equity/crypto. A stop is not a bound.
- Long-only enforced **in the kernel, not the schema**, so a hostile plan is
  refused with a reason string and a journal entry rather than dying as a
  validation error.

### MCP and CLI have real jobs

Both are required, and Technology Implementation is a scored criterion. Neither
goes in the order path.

- **CLI** — `tools/panic.sh` (flatten everything) and the daily reconciliation
  report.
- **MCP** — the 16:15 ET `daily_review` job, and the live "ask the agent"
  demo segment.

### The journal, honestly

Append-only JSONL, SHA-256 chained. A self-generated chain proves the file has
not been casually edited; it does **not** prove we did not regenerate it. Two
mechanisms close that gap and both are implemented:

1. **External anchoring** — the head hash is posted to Discord hourly, carrying
   server-side timestamps we do not control.
2. **Broker reconciliation** — every order entry carries Alpaca's own
   `broker_order_id` and timestamp. Judges hold the account ID.

Claim "reconcilable against broker-side records," not "tamper-proof."

Also retired: **"nobody was awake at 04:00 ET."** That is 13:30 IST — the
middle of our working day. Claim no human input and continuous operation
instead, which is true and stronger.

---

## 5 · Mapping to the five criteria

| Criterion | What we do |
|---|---|
| **P&L** | Event-driven convexity into four catalysts, with the Friday crypto window. Bounded left tail by construction. |
| **Technology Implementation** | Trading API in production; CLI for ops; MCP for review and demo. All three, each with a real job. |
| **Creativity & Originality** | Calendar-timed volatility, and trading the payrolls print in crypto while equities are closed. Nobody in the visible field mentions event timing. |
| **Presentation & Execution** | Live kernel refusal, chain verifier failing on a tampered byte, per-sleeve attribution. Demonstrated, not asserted. |
| **Social engagement** | Five posts, one per day, D owns it. Starts today. |

**Social is the highest points-per-hour on the board** — a scored criterion plus
a $1,000 track that most teams will score zero on. Two hours total.

---

## 6 · Schedule to go-live

Go-live is **Mon 31 Aug 09:30 ET = 19:00 IST**. Times below in IST.

| When | Block |
|---|---|
| Sat evening | ~~Kernel, schema, journal, tests~~ **done, 36 tests green, pushed** |
| Sat night | Both paper accounts created. Discord questions posted. First social post. VPS provisioned — "hello world every 60s, restarts if killed." |
| Sun 09:00 | Broker wrapper (rate limiter, reconciliation) + data layer (bars, chains, Greeks, calendar, clock) |
| Sun 13:00 | Feature layer: realised vol, ATR, gap, **implied-vs-realised ratio**, macro calendar proximity |
| Sun 15:00 | `strategies/event_vol.py` — calendar-triggered, emits plans through the kernel. Crypto strategy. |
| Sun 18:00 | Execution + position manager. Legged option entry with a completion rule. |
| Sun 21:00 | Thesis layer + prompt. Run against Friday's close; read every plan it produces. |
| Sun 23:00 | Deploy to VPS. Kill the process mid-tick, confirm it recovers with correct positions. Twice. |
| Mon 02:00 | **Buffer.** If consumed, the dashboard is what gets cut. |
| Mon 10:00 | Dashboard + README + write-up |
| Mon 17:00 | Cutover to scored account. Equity reads exactly $100,000.00, positions empty. |
| **Mon 19:00** | **GO LIVE · CODE FREEZE** |
| Mon 19:05 ET+ | Options path validation: buy one cheap ATM SPY call, confirm fill, mark, manager, expiry guard, close it. ~$400 tuition. A trade, not a code change. |
| Tue–Thu | Monitoring rota. Video, deck, daily social posts. |
| Thu 23:30 | Event trade established, 14:00–15:30 ET |
| Fri 18:00 | Submit. Deadline is 20:30 IST — do not use the margin. |

### Go-live checklist (binary)

- [ ] Scored account equity reads exactly $100,000.00, position list empty
- [ ] 36/36 tests green
- [ ] Process survives SIGKILL and resumes with correct positions
- [ ] 60 minutes of unbroken heartbeat in Discord
- [ ] One real option fill observed and closed
- [ ] Chain verifies seq 1 → head
- [ ] Account ID recorded in the journal and the submission draft

---

## 7 · Team

| Who | Owns |
|---|---|
| A | Kernel, execution, position manager, tests. Critical path. |
| B | Data, features, event strategy, crypto strategy |
| C | Thesis layer, prompt, journal, chain verifier, dashboard |
| D | **Infra** — VPS, systemd, Discord alerts, monitoring rota |
| D2 | **Submission** — social posts, video, deck, write-up, the clock, saying no |

D was one person in the original plan and carried the heaviest workload while
being described as the least experienced. Split it. If the team is four, B
picks up infra once the feature layer lands.

During the live week: one named person per session, authorised to hit the panic
script. **Nobody touches the code.** US market hours are 19:00–01:30 IST — make
the alerting good enough that nobody has to be awake for it.

---

## 8 · Risk register

| Risk | P | Impact | Mitigation |
|---|---|---|---|
| Agent dies overnight on a laptop | High | Critical | VPS, `Restart=always`, 15-min heartbeat, broker-as-truth reconciliation |
| Team ships code after go-live and breaks it | High | High | Code freeze. D enforces. Config only, logged. |
| Unbalanced strangle from a partial fill | **Med** | High | Documented ~10% paper behaviour. Leg completion rule: retry wider N minutes, else close the filled leg. |
| Indicative option feed gives bad limits | Med | Med | ATM SPY/QQQ only, marketable limits with a tolerance band, verify a real fill Monday. Or buy Algo Trader Plus ($99). |
| Timezone / DST bug | Med | High | UTC internally, one conversion at the edge, trust `get_clock`/`get_calendar` |
| Event trade is a dud (number lands in line) | Med | Med | Bounded to premium by construction. Priced, intentional outcome. |
| Rate-limit exhaustion | Low | Med | Token bucket at 150/min, cached snapshots |
| LLM API down or hung | Med | Low | Deterministic sleeves keep trading. **Explicit timeout** — a hung call stalls the tick loop. |
| Submitted from the wrong account | Low | Critical | Account number and equity printed to the journal at every startup |
| Leaked keys in the demo video | Med | Med | Scrub terminal output before filming; rotate after submission |

Retired: "options can't be enabled" — they are on by default.

---

## 9 · Do not build

User authentication · a React SPA · multi-user support · anything beyond SQLite
· a custom backtesting framework · a multi-agent debate system · reinforcement
learning · Docker orchestration · anything with the word "platform" in it.

**And do not build a second language.** FastAPI plus one server-rendered HTML
page covers the Application URL requirement with no build step.

---

## 10 · The pitch

> Glassbox is an autonomous agent that trades scheduled volatility events. It
> buys convexity into catalysts when implied volatility is cheap, a
> deterministic kernel bounds every position's loss before it opens, and on
> Friday morning it trades the payrolls print in crypto while the equity market
> is still closed.

**Say:** "The model proposes. It never executes." · "Every option position had
an exact maximum loss before it opened." · "We froze the code at Monday's open
and the journal reconciles against Alpaca's own records." · "Four days with a
ranked prize is a tournament, not a portfolio. We sized for the tail and we
bounded it."

**Don't say:** "multi-agent framework" · "our AI predicts the market" · any
Sharpe ratio computed on four days · anything about scaling to millions of users.

---

Paper trading only. Nothing here is investment advice.
