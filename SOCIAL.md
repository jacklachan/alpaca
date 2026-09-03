# Social posts — published

**Status: all five are live on LinkedIn**, with lablab.ai and Alpaca tagged.
That is the full allocation the event allows, and the submission rule reads
"up to 5 links to posts shared on X **or** LinkedIn" -- LinkedIn alone
satisfies it.

The drafts below are kept as the record of what was posted and why.

Every number in them is real and checkable against the repository or the
account. **Do not post a claim you cannot show.**

Tagging differs by platform, and the handles below are the **X** ones. On
LinkedIn a mention only registers if you type `@` and select the company page
from the dropdown -- pasting the X handle as text tags nobody. The published
posts use the LinkedIn company pages.

Tag **@lablabai** and **@AlpacaHQ** on X. Repository:
<https://github.com/jacklachan/alpaca>

A note on tone, because it is the whole strategy here: every other team will
post an equity curve going up. An equity curve is an outcome and proves
nothing about the agent. Post the *mechanism* — the refusals, the bugs, the
things that did not happen. That is both more interesting and more credible,
and the criterion explicitly weighs quality alongside engagement.

---

## Post 1 — the one to publish first

> Our trading agent spent this morning refusing to trade.
>
> Not broken. Two safety gates were fighting each other, and both were right.
>
> The strategy picks the *shortest* option expiry that still survives to the
> scoring snapshot, because that's where you get the most convexity per dollar.
> Short-dated options burn a big share of their premium every day — that's the
> structure, not a defect.
>
> A separate gate then refused any position burning >12% of premium per day.
>
> So one gate was built to find short-dated options, and the other refused them
> for being short-dated. Nothing traded. Both looked correct in isolation.
>
> Measured live: QQQ built a complete 35-lot strangle at $502/pair, then got
> refused at 13.59%/day against the 12% cap.
>
> The fix wasn't a looser number. A *daily* rate limit silently assumes a
> one-day horizon, and we hold for two. It now measures the decay we actually
> pay before we're measured, capped at a third of premium — which is a
> *tightening* for long holds.
>
> It traded within the hour.
>
> 🔗 github.com/jacklachan/alpaca
> @lablabai @AlpacaHQ

---

## Post 2 — the refusals

> The interesting part of an AI trading agent isn't the model. It's what the
> model is not allowed to do.
>
> Ours returns one candidate ID from a pre-priced list, or null. It cannot set
> the contract, the size, the side, or the limit price. Those are computed by
> deterministic code before the model ever sees them.
>
> So we can hand it hostile input and watch what happens. Four plans, each
> refused by a different invariant, in under a second:
>
> • sell 400 naked SPY calls → 02_bounded_max_loss
>   "short option leg has unbounded maximum loss; this system trades long
>    premium only"
> • a hallucinated ticker → 01_symbol_allowlist
> • a 100x oversized position → 02_bounded_max_loss
>   "stated max loss 10 understates computed worst case 4052.49"
> • 0DTE held into the snapshot → 05_concentration
>
> You can run this yourself with no Alpaca account and no API key:
>
>     python tools/demo.py
>
> @lablabai @AlpacaHQ

---

## Post 3 — the bug worth writing about

> Found a bug today that would have quietly cost us the whole competition.

> Our agent buys volatility when options look cheap relative to what the
> underlying is actually doing. The check compared implied vol against realised
> vol, and refused anything above 1.35x.
>
> It was reading the vol of the *front* contract. But the contract it buys is
> chosen afterwards, and the front month is systematically the most expensive
> thing on the board — it crams weekend and event premium into the fewest days.
>
> Live numbers:
>   front  (2 Sep) 14.92% vs 7.64% realised → ratio 1.95 → stand down
>   bought (8 Sep) 10.27% vs 7.64% realised → ratio 1.34 → trade
>
> It was refusing trades based on the price of something it was never going to
> buy. It would have run flawlessly all week and never placed an order — with a
> journal full of abstentions that all looked correct.
>
> Both halves read fine in isolation. Only the *order* was wrong. That's why we
> found it by probing the live candidate path, not by reading the code.
>
> @lablabai @AlpacaHQ

---

## Post 4 — the original idea

> Most trading agents don't know when they're being measured. Ours does, and it
> changes what it does about it.
>
> The account is valued at a known instant, and we price options off Alpaca's
> *indicative* feed — a derived estimate, not OPRA. So the last decision of the
> week isn't "is this position good?" It's "can this position be marked
> honestly?"
>
> A contract quoting 30% wide at the snapshot produces a number nobody can
> defend, in either direction. Cash has no marking ambiguity at all.
>
> So inside 45 minutes of measurement, any option too wide to price gets
> flattened — and a contract with *no* two-sided quote is treated as the worst
> case, not as permission to hold.
>
> It's deliberately narrow. It never opens risk, never touches a position it
> can price, and the threshold is looser than our entry gate: refusing to
> *enter* on a wide spread is prudence, but closing a working position needs
> the quote to be genuinely unusable.
>
> Sometimes the right last move is to take the certain number.
>
> @lablabai @AlpacaHQ

---

## Post 5 — the honest close (publish Thursday or Friday)

> Final day. Here's what we'd want a judge to check rather than take our word
> for:
>
>     python tools/verify_submission.py
>
> It re-derives what can be re-derived and inspects what cannot. No credentials,
> no network, nothing mutated. A SKIP means the evidence doesn't exist yet — not
> that a check was waived.
>
> What we claim: every option position had an exact maximum loss before it
> opened. The model proposed and never executed. The code was frozen at the
> open.
>
> What we don't claim: that four days proves an edge. And our hash-chained
> journal is not tamper-proof — it detects edits to the recorded history, but it
> does not prove we never regenerated the whole thing, because we control every
> input to the hash. Alpaca's own order IDs and timestamps are what a third
> party actually reconciles against.
>
> Reconcilable, not tamper-proof. [ADD FINAL EQUITY + ACCOUNT ID]
>
> @lablabai @AlpacaHQ

---

## Before publishing

- Post 1 is the strongest opener — a story with a fix, not a boast.
- Only Post 5 has a placeholder. Everything else is publishable as written.
- Screenshot suggestions: `tools/demo.py` output for Post 2, the term-structure
  numbers for Post 3. **Never screenshot the Alpaca dashboard home page** — the
  API key panel is on it.
- If P&L ends negative, publish Post 5 unchanged and state the number. A team
  that reports a loss accurately alongside a working risk system is more
  credible than one that goes quiet, and "not P&L alone" is in the rules.
