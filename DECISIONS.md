# Decisions

Why Glassbox is built the way it is. Written so a teammate joining on Sunday,
or a judge reading the repo on Friday, can reconstruct the reasoning without
having been in the conversation.

Every entry is a decision that could have gone the other way. Where we were
wrong first, that is recorded too — several of these were corrections.

---

## 1 · What the competition actually is

The project began from a plan written against the Alpaca Q&A document alone.
Checking the published event page changed the shape of the work, because the
page carries requirements the Q&A does not mention.

**Hard requirements found on the event page, not in the Q&A:**

- *"All strategies must incorporate options trading."* The original plan
  treated options as an optional 15% sleeve and had a fallback to leveraged
  ETFs if options could not be enabled. That fallback would have disqualified
  the submission rather than degraded it. Deleted.
- *"Projects must utilize either Alpaca's MCP server or its CLI tools."* Both
  now have real jobs outside the order path (§7).
- **Social engagement is one of five judging criteria**, plus a separate
  $1,000 prize track. The original plan mentioned it nowhere, and its
  "code freeze, touch nothing" ethos guaranteed zero posts.

**The five published criteria** are P&L Performance, Technology
Implementation, Creativity & Originality, Presentation & Execution, and Social
engagement. Notably *robustness is not among them* — it is instrumental to the
others, not scored directly. The original plan optimised for a rubric that did
not exist.

**Facts verified rather than assumed:**

| Fact | Source |
|---|---|
| Payrolls: Fri 4 Sep 2026, 08:30 ET | BLS September 2026 schedule |
| VIX 14.1–14.2 on 28 Aug, a 2026 low | market data |
| Options enabled by default on paper accounts, level 3 | Alpaca docs, confirmed on our account |
| Alpaca stops accepting options orders 15:30 ET on expiry day | Alpaca docs |
| Paper accounts partially fill ~10% of orders at random | Alpaca docs |
| Submission deadline Fri 4 Sep 20:30 IST | event page |
| Prize pool $6,300, split published | event page |

**Still unresolved:** whether equity is measured at Friday 09:30 ET (per the
Q&A) or at the 11:00 ET submission close (implied by the event page). Asked on
Discord. Until answered we assume the earlier time, because being early is
recoverable and being late is not.

---

## 2 · Why the strategy is what it is

### The risk kernel is not the differentiator

The original plan's central bet was that *"almost nobody will build a
deterministic risk layer between the model and the broker — that is the entire
differentiator."* The event page's public submissions already include at least
five teams leading with exactly that: AEGIS-Q, BABIL, SPY Sentinel, AlphaSwarm,
and Vega — the last already long SPY convexity with a stronger verifiability
claim than a self-generated hash chain.

The architecture is table stakes. We keep it — the required write-up literally
names "risk gates" — but it is the credibility floor, not the pitch.

### What is still uncontested: the calendar

No visible competitor description mentions event timing relative to the
measurement window. So Glassbox is framed as **an agent that trades scheduled
volatility events**, not an agent with guardrails that happens to trade
options. The scored window contains a catalyst on four of five days, and the
alpha source is nameable: the volatility risk premium is compressed at VIX 14
and the catalysts are on a published schedule.

### Sizing: a tournament is not a portfolio

A ranked prize is a step function. Fourth of a hundred pays what fortieth
pays, so the objective is probability of placing, not expected return. That
argument does **not** say take more risk — it says take *asymmetric* risk.
Long options are the one instrument class where maximum loss is known at entry
and enforceable by a risk check, while the upside is uncapped.

This is also why we refuse standing margin leverage (§6).

### Allocation: 80/15/5 → 65/25/10

Options are a core requirement of the track. **Options Alpha Agents** is the
event's track name — lablab's `/live` page lists it as the main track, open to
all participants — and the event page states that all strategies must
incorporate options trading. Under the original split, 85% of capital sat in
instruments that scored on none of the five criteria.

#### A failed challenge to this decision, kept as a warning

On 29 Aug a review concluded that "Options Alpha Agents" was **fabricated**,
on the grounds that the phrase appears nowhere on the public event page, in the
official Q&A, or in a web search. The allocation was moved to 70/20/10 and this
section was rewritten as a retraction.

That conclusion was wrong. The phrase is on the `/live` page as the track name.
The public event page renders far less than the enrolled view — the same fetch
that missed the track name also missed the five judging criteria, the prize
breakdown, and the submission fields, all of which are real. The reviewer
treated "I could not find it" as "it does not exist", which is the same class of
error the review was set up to catch.

Reverted to 65/25/10. Kept in the record rather than deleted, for two reasons:

1. **The rule it teaches is load-bearing this week.** Verify against the
   enrolled view, not the public page. Anything sourced from the public page
   alone is weaker evidence than it looks, in both directions.
2. **An audit trail that only records the author's mistakes and not the
   auditor's is not an audit trail.** The journal makes the same commitment
   about the agent; this document should hold itself to it.

If you are about to change these numbers, confirm against the enrolled view
first.

---

## 3 · The expiry decision, and how it was actually made

This is the most-worked decision in the project and it changed twice.

**First argument (mine, and overstated):** options are priced in business
time, so an expiry spanning the Labor Day weekend (Mon 7 Sep) is cheap
relative to its calendar length. Gamma per dollar scales as 1/T, so the 8 Sep
expiry should buy ~2.5× the convexity of the 11 Sep expiry a normal team would
default to.

**The correction:** that ignored event premium. The market knows payrolls is
scheduled, so short-dated contracts spanning it carry a premium that collapses
once the number prints — and the crush hits the shortest expiry hardest,
exactly the one the holiday discount favours. The two forces oppose each
other and no argument settles them.

**So we stopped arguing and measured.** `select_expiry()` reads the observed
term structure and picks the shortest expiry that still has real extrinsic
value at measurement, quotes tightly enough to mark honestly on an indicative
feed, and is not carrying outsized event premium. Returning `None` — refusing
to trade — is a valid outcome.

**What the live chain said on 29 Aug:**

| Expiry | ATM IV | ATM spread | Sessions left at measurement |
|---|---|---|---|
| 3 Sep | 9.9% | 4.0% | 0 |
| 4 Sep | **10.7%** | 4.1% | 1 — payrolls day, highest IV in the curve |
| **8 Sep** | **9.4%** | 5.3% | 2 — cheapest viable, spans Labor Day |
| 9 Sep | 9.7% | 6.0% | 3 |
| 11 Sep | 10.6% | 4.0% | 5 |

Two forward-vol calculations settle it:

- **4 Sep → 8 Sep: 7.0% annualised.** Saturday, Sunday, Labor Day, one session.
- **8 Sep → 11 Sep: 13.9% annualised.**

We are measured Friday morning, so every session past 4 Sep is time we pay for
and never use — and the holiday stretch costs half what the following week
costs. The 8 Sep contract is simultaneously the cheapest in vol terms and the
highest gamma per dollar, which is abnormal and is the whole edge.

Selector picks **8 Sep at 2.5×**, with event premium *negative* against the
11 Sep baseline. If Monday's quotes come back wider than 5.5%, it falls back to
11 Sep automatically.

**Caveat we state rather than hide:** the finer the pricing distinction an edge
relies on, the more it depends on Alpaca's paper marking methodology, which is
undocumented. This is the least robust of our levers, and the ones that survive
any marking methodology — more contracts per dollar, cheaper underlying, more
shots — are the ones we lean on.

---

## 4 · The risk kernel: thirteen invariants, and five corrections

The original plan specified twelve. Writing them found real defects.

**Thirteen, not twelve.** Invariant 11 (idempotency) stops the *same* plan
being sent twice. Nothing stopped the agent generating hundreds of *distinct*
plans. Runaway loops are the classic unattended-overnight failure, so invariant
13 is an order-frequency circuit breaker.

**The kill switch fired in the plan's own modal scenario.** A portfolio switch
at 88% of starting equity would trip when the convex sleeve expires worthless
— a designed ~50% outcome, not a failure. Now per sleeve: the convex sleeve is
*permitted* to go to zero, the core sleeve trips at 6% drawdown, with a 15%
portfolio backstop behind it. There is a test asserting the convex sleeve going
to zero does not trip it.

**The kernel would have refused the flagship trade.** Invariant 04 capped
convex premium at $5,000/day, while the plan instructed sizing the Thursday
event trade to "whatever remains of the $15,000 budget." Both could not hold.
Resolved with a separately budgeted event-trade allowance rather than by
weakening the daily cap. There is a regression test asserting the event trade
survives with the daily cap already spent.

**Concentration was ambiguous in a decisive way.** "No single underlying > 25%
of equity, counting option delta exposure" did not say gross or net, and one
number cannot serve both instruments. Gross, a single ATM SPY call is ~160% of
a $100k account and every option trade is refused. Net at 25% still refuses any
normal convex position, because long options deliver large notional delta for
small premium — that *is* the leverage, and it is why we buy them. Split into
capital concentration (equity/crypto, 25%) and a deliberately loose delta
sanity bound (options, 200%). **The premium caps are the real risk control, not
the delta bound**, and we say so rather than overclaiming.

**Maximum loss is exact for options and estimated for everything else.** "Every
position had a computable maximum loss before it was opened" is true of long
premium and false of equities and crypto, where price gaps straight through a
stop. Equity and crypto max-loss is stop distance × quantity × a gap
multiplier, labelled an estimate. The pitch line is now qualified to the
options book, where it is exactly true.

**Long-only is enforced in the kernel, not the schema.** If the schema rejected
a short option leg, a hostile plan would die as a Pydantic validation error
with no reason string and no journal entry. Hostile plans must be
*representable* so they can be *visibly refused*.

---

## 5 · The journal, and what it does not prove

Append-only JSONL, SHA-256 chained. Editing history in place breaks the chain
and `tools/verify_chain.py` detects it.

**It does not prove we did not regenerate the whole chain from altered
content.** We control every input to the hash, so a self-generated chain is
self-attestation. The original plan claimed this "turns trust-us into a
verifiable claim"; that would not survive a brokerage engineer thinking about
it for thirty seconds.

Two mechanisms close most of the gap, both implemented:

1. **External anchoring** — the head hash is posted to Discord hourly, carrying
   server-side timestamps we do not control. An anchor accepted at a given time
   constrains every entry written before it.
2. **Broker reconciliation** — every order entry carries Alpaca's own
   `broker_order_id` and timestamp. Judges hold the account ID and can
   reconcile against records we cannot edit.

The claim we make is *"reconcilable against broker-side records"*, not
*"tamper-proof"*.

**Retired: "nobody was awake at 04:00 ET."** That is 13:30 IST — the middle of
our working day. A judge doing the arithmetic would find our strongest
evidentiary claim resting on a timezone the team does not live in. We claim no
human input and continuous operation instead, which is true and stronger.

---

## 6 · Margin: refused, with one narrow exception

The paper account carries $400,000 of buying power — 4× equity. We do not use
it for standing exposure.

The reason is not caution, it is shape. Margin gives linear exposure: doubling
a position doubles both tails. Options give convex exposure — the right tail
fattens, the left tail hits a floor. Under a step-function payoff we want
asymmetry, and we already own leverage with a floor: $25k of premium controls
several hundred thousand of notional delta. There is no reason to borrow the
same exposure without a floor.

Secondary but real: a deeply negative finish degrades four of five criteria
together, not just P&L.

**The exception we would defend:** time-bounded intraday leverage on *released*
information. After the payrolls print is public, a leveraged position held
ninety minutes against a hard time exit is bounded by the clock rather than by
price, and gap risk inside a liquid session is small. That is budgeted and
exit-gated if we use it, not a standing posture.

---

## 7 · Engineering decisions

**Python.** Pydantic is the reason — the whole design depends on turning "the
model returned some text" into "a validated object or nothing." `alpaca-py`
also has the fullest options surface, and the feature layer is numpy-shaped
work. Nothing here is latency-sensitive, so Python's speed is irrelevant.

**The broker is the source of truth.** Every tick re-reads positions and orders
from Alpaca and rebuilds state from scratch. Local state is never trusted. This
is what makes a crash cost minutes instead of the week.

**The process cannot boot against a live account** — checked three ways (env
flag, `PK` key prefix, paper endpoint in the base URL). A false positive costs
nothing; a false negative costs everything.

**The strangle is legged as two single-leg orders**, not one multi-leg order.
That removes any dependence on multi-leg support and on the approval level. It
introduces the failure `execute.py` exists to handle: Alpaca paper accounts
partially fill ~10% of orders at random, and on Thursday an unbalanced strangle
would silently become a directional bet on the day that decides the score. The
completion rule reprices the laggard within the band and, failing that,
**unwinds the filled side back to flat**. A flat book is acceptable; a naked
long call into payrolls is not.

**Expiry close-out at 14:30 ET, not 15:45.** Alpaca stops accepting options
orders at 15:30 on expiry day, then auto-exercises ITM positions, and non-trade
activity settles T+1 — after the account is photographed. The original 15:45
would have lost the position to settlement mechanics. There is a test asserting
the close-out beats the broker cutoff by an hour.

**APScheduler with `max_instances=1, coalesce=True`** and an explicit misfire
grace. On a one-minute tick with defaults, an overrunning job spawns a second
instance and two copies hit the broker concurrently holding the same view.

**`iv_rank` was renamed.** The original plan cited `iv_rank_spy=8.2` in its own
sample journal payload. An IV percentile needs IV history the free tier does
not provide — shipping a metric named for a number we cannot compute would put
fabricated evidence into the journal, the exact failure the journal exists to
prevent. We ship `iv_to_rv_ratio`, which we actually compute.

**The core sleeve is deliberately passive.** Fixed-weight SPY/QQQ/IWM bought at
Monday's open and held. We have no demonstrable edge in four days of
directional equity trading, and a rules engine on 26 hours of IEX data would
produce noise recorded as reasoning. Saying we declined to pretend to an edge
is a stronger position than a strategy we cannot defend. Its weights are
bounded by the kernel, not by taste — a first attempt at 50/30/20 put SPY at
32.5% of equity and invariant 05 refused it, correctly.

**MCP and CLI have real jobs, outside the order path.** The CLI owns
`tools/panic.sh` and the daily reconciliation report; MCP powers the 16:15
daily review and the live "ask the agent" demo. An LLM tool-calling a broker
unsupervised for 96 hours is the fragility we are engineering away, so neither
touches execution.

**The thesis layer is optional.** If the API is down, hung, or returns
nonsense, the deterministic sleeves keep trading and the failure is journalled.
Hence the explicit timeout — the original plan handled a *failed* call but not
a *hung* one, which would block the tick loop.

---

## 8 · Bugs the tests caught that review did not

Worth recording, because it is the honest case for why the suite exists.

1. **The reprice never fired.** The bump was `LIMIT_TOLERANCE × (attempt + 1)`,
   so the first attempt asked for 6% against a 5% band and every reprice was
   refused. In production the laggard would never have been chased — every
   incomplete strangle would have gone straight to unwind.
2. **Off-by-one in sessions-to-measurement.** Trading days remaining must
   include the measurement day itself, since the account is photographed at the
   open while the option still has that session to live. Wrong, the 8 Sep
   expiry looked like 1 session and was rejected outright.
3. **Entry-DTE conflated with sessions-at-measurement.** Let the 18 Sep expiry
   into the candidate set, where it became the denominator of the convexity
   ratio and reported a meaningless 5.0×.
4. **The core strategy violated the kernel's concentration limit** on its first
   run. Fixed in the strategy, not by loosening the rule.
5. **SPY seeded at 650 in fixtures.** A placeholder never checked; the real
   close was 774.46, and later 769.28.
6. **`reconcile()` never populated `trading_days_to`.** Invariant 10 refuses
   any option plan whose expiry it cannot count sessions to, so the empty map
   refused *every* option trade. The convex sleeve � the entire thesis � would
   have been dead at Monday's open, discovered at 09:30 with the code frozen.
   Surfaced by the practice harness, not by review.
7. **`panic.sh` invented its CLI syntax.** `alpaca order cancel --all` and
   `--output json` do not exist; the real commands are `order cancel-all`,
   `position close-all` and `--json`. The manual kill switch would have failed
   at the moment it was needed. Verified against github.com/alpacahq/cli.

---

## 9 · Open items

- **Measurement time.** Asked on Discord. Changes whether we hold into the
  snapshot or close into cash — and cash has no marking ambiguity at all.
- **Monday's quote widths.** Saturday spreads are meaningless. If 8 Sep comes
  back above 5.5% in session, the selector falls back to 11 Sep on its own.
- **Options path never exercised end to end.** Options markets are closed all
  weekend, so the sleeve carrying the thesis will have completed zero real
  orders at go-live. Plan: one cheap ATM SPY call Monday 09:35 ET, confirm
  fill, mark, manager and expiry guard, then close. ~$400 of tuition, and it
  answers the marking question empirically. It is a trade, not a code change,
  so it does not violate the freeze.
- **Algo Trader Plus ($99).** Buys OPRA quotes instead of the indicative feed.
  Against a $6,300 pool and with marking as the largest unknown, this should be
  a decision rather than an open question.
- **Social posts.** Scored criterion, $1,000 track, near-zero marginal cost,
  and most teams will score zero.

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
Sharpe ratio computed on four days · anything about scaling to millions of
users.

---

Paper trading only. Nothing here is investment advice.
