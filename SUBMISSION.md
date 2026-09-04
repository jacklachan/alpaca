# Submission form — the answers, ready to paste

Deadline **Friday 4 September, 20:30 IST** (17:00 CEST / 08:00 PDT).

The lablab form asks for the fields below. Everything marked ✅ is settled and
can be pasted as written. Everything marked ⚠️ needs a human decision or an
asset that does not exist yet.

---

## ✅ Project title

```
Glassbox
```

## ✅ Short description

```
An autonomous options agent on Alpaca paper trading whose language model cannot
author a trade. Deterministic code prices every candidate; the model may only
pick one of them or decline. Every order passed a 13-invariant risk kernel
first, and every decision is in a hash-chained journal you can verify yourself.
```

## ✅ Long description

```
Glassbox is an options-only autonomous agent on Alpaca paper trading, account
PA3XT8QFJZAQ, funded at $100,000.

The design premise is that a language model should not be trusted to write an
order, so it is not given a way to. Deterministic strategy code screens the
SPY/QQQ event calendar and the live option surface and builds fully specified
candidates: contract symbols, side, quantity, limit price, maximum loss, exits,
evidence, and a content-addressed ID. That set is canonically ordered and
hashed. The model receives only summarised immutable fields and returns one
candidate ID, or null. That is its entire output schema. A timeout, malformed
JSON, an unknown ID or an altered object is an abstention, journalled with its
reason, and the exact original candidate — never a reconstruction — is what
reaches the risk kernel.

The kernel checks 13 invariants before anything is sent: symbol allowlist,
bounded maximum loss, sleeve budget, daily burn, concentration, position count,
gross exposure, drawdown kill switch, market hours, expiry guard, idempotency,
sanity band and order frequency. It makes no network or model call, so its
verdict is reproducible from its inputs. Around it sit account-identity checks,
typed venue failures that never infer "no such order" from "we could not ask",
exact position ownership derived only from confirmed fills, and option-surface
gates that refuse convexity bought too rich or decaying too fast to survive to
the measurement.

The agent also knows when it is being scored, and that turned out to matter more
than anything else we built. The account is valued at a known instant, and we
price options off Alpaca's indicative feed — a derived estimate, not OPRA. So
the last decision of the week is not "is this position good?" but "can this
position be marked honestly?" Inside 45 minutes of measurement, a leg too wide
to price is flattened, and a leg with no two-sided quote is treated as the worst
case rather than as permission to hold.

That gate was vindicated by our own numbers. At the instant the account was
valued, our indicative-feed reading of the book said $99,642.35 and Alpaca's
official close said $94,207.02 — the same account, the same second, $5,435.33
apart, 5.77% of the account. Neither number is a lie. An option mark is an
opinion until it is cash.

Alpaca infrastructure: alpaca-py (pinned and hash-locked) against the paper
Trading and Data APIs — account and clock, server-authoritative option contracts
via GetOptionContractsRequest with pagination, timestamped quotes and Greeks,
orders under deterministic client order IDs, position reconciliation, and
get_portfolio_history for equity, because we are scored on Alpaca's number
rather than one we reconstruct. Trading sessions come from Alpaca's calendar,
not weekday arithmetic. A read-only Alpaca CLI evidence tool builds commands
from an allowlist and refuses any mutating token before a process starts. The
MCP client is built inverted: the official server's default toolset includes
place_option_market_order, close_position and cancel_orders, so rather than
trusting configuration to hide them, the client declares the only tools it will
ever call and enforces an exact allowlist, a mutating-verb scan and a discovery
gate — proven against the official Alpaca MCP Server 3.4.7 with live
credentials, with four mutating tools refused by attempting them.

Result on the measured day: -5.79%, read from Alpaca's own portfolio history.
We are not hiding it and we are not dressing it up. What we would rather be
judged on is that the system priced its own costs in advance, wrote them into a
chain that provably predates the outcome, refused the largest catalyst of the
week because its payoff landed after the account was photographed, and shipped
the tool that could prove our central claim false:

    python tools/verify_submission.py

Eleven checks, no credentials, no network, nothing mutated. One of them is that
every AI selection named a candidate that was actually offered to it. If that
check ever fails on real evidence, the model authored a trade and our central
claim is wrong.
```

## ✅ Alpaca paper trading account ID

```
PA3XT8QFJZAQ
```

Created for this hackathon, funded at $100,000, options-only. **Confirm it was
created during the event** — a reused account is disqualifying, and that is a
fact only you can vouch for.

## ✅ Public GitHub repository

```
https://github.com/jacklachan/alpaca
```

## ✅ Technology & category tags

```
Alpaca, Options Trading, Autonomous Agents, Risk Management, Python,
Featherless AI, MCP, Qwen
```

Featherless is a technology partner, and partner prizes require the partner's
technology to be integrated in the submitted project. Ours runs the bounded
selector on `Qwen/Qwen2.5-72B-Instruct` through Featherless, so the tag is
earned rather than decorative.

## ✅ Social engagement — up to 5 links

Five posts are live on LinkedIn tagging lablab.ai and Alpaca. Paste the five
URLs. `SOCIAL.md` holds what was posted and why.

## ✅ One-page write-up

`docs/WRITEUP.md` — 743-test count synced, three required sections named
exactly as the rules word them. The rules allow a slide instead, so if the deck
carries it, point at the slide rather than the file. Do not submit both and let
them disagree.

---

## ⚠️ Video presentation

Recorded 3 Sep. **Segment one is stale and must be re-recorded** —
`calibration.py` was rewritten after that take and now leads with a different,
stronger finding. The `demo.py` and `verify_submission.py` segments are
unaffected. Script and shot list: `VIDEO.md`.

## ⚠️ Slide presentation

With the teammate. Source material and slide-by-slide content: `DECK.md`.

## ⚠️ Cover image

Does not exist yet. Cheapest credible option is a clean terminal frame of
`tools/demo.py` showing the four refusals, or the two-numbers block from
`tools/calibration.py`. Do not use a stock chart image — the whole pitch is
that we show mechanism rather than outcomes.

## ⚠️ Demo application platform / Application URL

**We have no hosted deployment.** The agent runs on a laptop under a restart
watchdog, and `docs/WRITEUP.md` says so plainly rather than implying otherwise.

Options, in order of honesty:

1. Put the repository URL in the Application URL field. It is what a judge will
   actually open, and it is true.
2. Host the dashboard. `uvicorn dashboard.app:app --port 8080` needs no
   credentials and is the only part of the system with a UI. Roughly 30 minutes
   on any free host.

Do **not** describe the laptop run as a deployment. The write-up already
declares the gap, and a form that contradicts the write-up is worse than an
empty field.

---

## Before you hit submit

- [ ] Account ID matches the account that actually traded: `PA3XT8QFJZAQ`
- [ ] The account was created during the event, not reused
- [ ] Repo is public and `main` is pushed
- [ ] Video re-records segment one
- [ ] Write-up appears once, in one place, not twice with different numbers
- [ ] Five social links pasted
- [ ] No screenshot anywhere shows the Alpaca dashboard home page — the API key
      panel is on it
- [ ] `python tools/verify_submission.py` still reports 11 passed

## After the deadline

Rotate the Alpaca keys. They were pasted into a chat during the week.
