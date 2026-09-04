# Submission form — the answers, ready to paste

Deadline **Friday 4 September, 20:30 IST** (17:00 CEST / 08:00 PDT).

**Final numbers, settled:**

| | |
| --- | --- |
| Measured day, Thu 3 Sep close | **$94,207.02, -5.79%** |
| Final, flat and in cash | **$100,095.01, +0.10%** |
| Scored account | `PA3XT8QFJZAQ` |

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

Result. On the day we designated as measurement, Thursday 3 September, the
account closed at $94,207.02 -- down 5.79%, read from Alpaca's own portfolio
history. The week ended with the book flat and the account entirely in cash at
$100,095.01, +0.10%, which is realised rather than a mark. Both numbers are
real and they answer different questions. Neither is four days of edge, and we
do not claim one.

What we would rather be judged on is that the system priced its own costs in
advance, wrote them into a chain that provably predates the outcome, refused
the largest catalyst of the week because its payoff landed after the account
was photographed, and shipped the tool that could prove our central claim
false:

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

Created during the event, funded at $100,000, options-only — confirmed by
Mohit on 4 Sep. The rule is that a reused account is not eligible for judging,
so this matters more than any single score: it is the one requirement that is
pass/fail rather than weighted.

## ✅ Public GitHub repository

```
https://github.com/jacklachan/alpaca
```

## ✅ Technology & category tags

These are two separate fields, and the technology field is a picker with a
fixed vocabulary -- typing a name it does not know silently gets you nothing.
Verified against the tags in use on the event page.

**Technology tags** -- pick exactly these:

```
Alpaca, Featherless, Claude Code
```

**Category tags** -- free-ish text, what other entries use:

```
Finance, Investment
```

Everything the old list had beyond that (`Options Trading`, `Autonomous
Agents`, `Risk Management`, `Python`, `MCP`) is **not in the technology
picker**. Those belong in the description, where they already are.

Two names were wrong and would have failed to match:

- The tag is **`Featherless`**, not "Featherless AI".
- There is no `Qwen` or `Qwen2.5` tag. The only one offered is **`Qwen3`**,
  and we run `Qwen/Qwen2.5-72B-Instruct` -- so do **not** tag Qwen3. Name the
  exact model in the description instead. Qwen is not a prize partner; tagging
  the wrong version buys nothing and costs accuracy.

Featherless **is** a technology partner, and partner prizes require the
partner's technology to be integrated in the submitted project. Ours runs the
bounded selector on `Qwen/Qwen2.5-72B-Instruct` through Featherless, so that
tag is earned rather than decorative. It is the one tag on this page with
money attached.

## ✅ Social engagement — up to 5 links

Five posts are live on LinkedIn tagging lablab.ai and Alpaca. **The five URLs
are in the table at the top of `SOCIAL.md`** -- copy them from there rather than
hunting through two LinkedIn profiles. The drafts below that table are the
record of what was posted and why.

## ✅ One-page write-up

`docs/WRITEUP.md` — test count synced by `tools/sync_writeup_counts.py` and
guarded by `tests/test_claims.py`, three required sections named
exactly as the rules word them. The rules allow a slide instead, so if the deck
carries it, point at the slide rather than the file. Do not submit both and let
them disagree.

---

## ⚠️ Video presentation

With Tanush, being voiced now. Five cut beat clips are in the `Videos\glassbox-beats\` folder, already zoomed and held to each beat's script length.

~~Segment one is stale and must be re-recorded~~ —
`calibration.py` was rewritten after that take and now leads with a different,
stronger finding. The `demo.py` and `verify_submission.py` segments are
unaffected. Script and shot list: `VIDEO.md`.

## ⚠️ Slide presentation

With the teammate. Source material and slide-by-slide content: `DECK.md`.

## ✅ Cover image

`docs/cover.png`, 1200x630. Regenerate with `python tools/make_cover.py`.

It is a terminal frame of two real refusals taken verbatim from `tools/demo.py`,
not a chart. A cover has about a second to earn a click, and the agent refusing
things is the one thing an equity curve cannot show. Every string on it is
either a fact about the account or a line the kernel actually emits.

Swap it only for something equally concrete — a stock trading graphic would
contradict the whole submission.

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
- [x] The account was created during the event, not reused — confirmed
- [ ] Repo is public and `main` is pushed
- [ ] Video re-records segment one
- [ ] Write-up appears once, in one place, not twice with different numbers
- [ ] Five social links pasted
- [ ] No screenshot anywhere shows the Alpaca dashboard home page — the API key
      panel is on it
- [ ] `python tools/verify_submission.py` still reports 11 passed

## After the deadline

Rotate the Alpaca keys. They were pasted into a chat during the week.
