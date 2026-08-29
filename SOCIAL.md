# Social engagement plan

Social engagement is one of the five judged criteria, and the guidelines are
specific about what counts:

> "For the social engagement component, both the quality of the content and the
> engagement it generates may be considered. Engagement can include likes,
> comments, and shares, while the quality, creativity, and usefulness of the
> post also matter."

Two things follow, and they point the same way.

**Quality is judged directly, not only through the like count.** A team with a
modest following can still score here by posting something genuinely useful.
That is the lever we can actually pull.

**We have an unfair advantage and it is not the strategy.** Every other team
will post an equity curve going up. Curves are unfalsifiable, identical, and
boring. What we have that nobody else does is a *decision journal full of
refusals* — a record of an agent being told no by its own risk kernel. That is
the rarest thing in an AI-trading timeline, where every post is a win.

So the content strategy is: **post the bugs and the refusals, not the wins.**
It is more interesting, more credible, more useful to other builders, and it is
the only version of this that survives a flat P&L week.

---

## The rule that governs every post

Do not post anything that is not true at the moment of posting. No projected
returns, no "our agent is up X%" before the snapshot, no screenshots of the
scored account's equity presented as a final result. If the week goes badly,
post the post-mortem — that scores better on "quality, creativity and
usefulness" than a fabricated win, and it is the only honest option anyway.

Tag `@lablabai` and `@AlpacaHQ` and use `#AlpacaHackathon`. Verify the exact
tags on the event page before the first post.

---

## Five posts, in order

Post 1 is the one that matters. If only one thing gets posted, post that.

### 1 · The bug that only appeared in production
**Best single post. Genuinely useful to every Python developer who reads it.**

> systemd's `EnvironmentFile` and python-dotenv disagree about this line:
>
> `ALPACA_ENV=scored   # dev | scored`
>
> dotenv strips the comment. systemd keeps it. And `load_dotenv()` won't
> override a variable systemd already set — so the mangled value wins.
>
> Our agent gates its "is this the right account?" safety checks on
> `env == "scored"`. On the VPS that comparison was False. The guards silently
> did not run. Every hand test passed, because by hand there is no systemd.
>
> Now: an unrecognised value crashes instead of falling through, and a
> preflight refuses to boot when the two parsers disagree.
>
> [screenshot of the parity checker output]

Attach the real `tools/env_parity.py` output. This post is useful to people who
have never heard of the hackathon, which is exactly what makes it spread.

### 2 · The kernel saying no, on video
15–20 seconds, screen recording, no narration needed.

> We asked our own trading agent to sell 400 naked SPY calls.
>
> Its risk kernel refused in 40ms — `02_bounded_max_loss: maximum loss is
> unbounded` — and wrote the refusal into a hash-chained journal it cannot
> edit.
>
> The model proposes. It never executes.

### 3 · The correction that reshaped the strategy
Shows you read the rules properly, which is rarer than it should be.

> We built our whole options thesis around the payrolls print landing 60
> minutes before the measurement snapshot.
>
> Then we read Alpaca's guidelines properly: equity is measured **EOD Thursday
> 3 Sep**. Payrolls is Friday 08:30. Our flagship catalyst was ~16 hours
> outside the scored window.
>
> Buying convexity for it would have been paying for a payoff that lands after
> the photograph. The most expensive kind of wrong — it looks like a thesis.
>
> Re-timed the whole calendar. Read the rules twice.

### 4 · The audit trail
Screenshot of the decision timeline, refusals highlighted in amber.

> Every trade our agent makes has a receipt.
>
> Thesis, evidence, the kernel's verdict, the order, the broker's own order id
> — appended to a hash-chained log. Edit one byte anywhere in the history and
> the verifier fails.
>
> It does not prove we are honest. It proves the record has not been changed
> since it was written, and every order reconciles against Alpaca's own
> records. Claim the second thing, not the first.

### 5 · The write-up
Post at the end, links the repo.

> Four days, one $100k paper account, and an agent nobody touched after Monday
> 09:30.
>
> What worked, what broke, and the two bugs that only appeared in production.
> Full journal and code: [repo link]

---

## Where and when

| When | Post | Platform |
|---|---|---|
| Immediately | 1 · the systemd bug | X/Twitter + LinkedIn |
| Same day | 2 · kernel refusing | X/Twitter (video) |
| Mon after open | 3 · the correction | X/Twitter + Discord |
| Wed | 4 · audit trail | LinkedIn + Discord |
| Fri | 5 · write-up | everywhere, links repo |

Post 1 also belongs in the lablab Discord, where the judges actually are, and
where a genuinely useful debugging post gets read rather than scrolled past.

## What not to post

- Equity screenshots before the snapshot. Every team does it and it ages badly.
- Anything with an API key visible. Scrub terminal recordings before posting.
- Predictions about where the market goes. It is a four-day sample.
- "Our AI predicts the market." It does not, and a judge who trades will notice.
