# Deck source material — everything needed to build the slides

For whoever is making the presentation. This is the raw material, not the deck:
take what serves the slide and drop the rest. Nothing here is aspirational —
every number is real and has a command next to it that re-checks it.

**One rule.** If you cannot show where a number comes from, cut the claim. The
entire pitch is that we are checkable, and a single unverifiable boast on a
slide costs more than it buys.

**The measured day is over and the numbers below are settled.** The account was
valued at 16:00 ET Thursday 3 Sep, and Alpaca's own portfolio history records
that close as **$94,207.02, −5.79%**. `tools/calibration.py` now freezes at that
instant, so it prints the same result whenever it is run — an earlier version
drifted with the live book and produced two different headline numbers an hour
apart. One position remains open and will be closed at Friday's open; that
affects Friday, not the measured day.

---

## 1. The 60-second version

Glassbox is an autonomous options agent on Alpaca paper trading. Deterministic
code finds and prices every trade. A language model's entire authority is to
pick one of those pre-priced candidates, or decline — it cannot author a trade,
because there is no field in the schema in which to express one.

It finished the measured day down 5.79%. Two things are worth more than that
number.

Before each order, the risk model wrote into a hash-chained journal exactly how
much premium would decay before the account is valued — timestamped, so it
provably predates the outcome. $6,797 forecast against $25,628 committed.

And at the instant the account was valued, our own honest reading of the book
said $99,642 while Alpaca's official close said $94,207. Same account, same
second, $5,435 apart. An option mark is an opinion until it is cash, and that
gap is the entire reason this agent would rather hold a number it can defend
than one that merely looks better.

---

## 2. Suggested deck — 10 slides

Order matters more than content here. **Lead with the calibration**, not with
the architecture, for the reason in §7.

### Slide 1 · Title
Glassbox — autonomous options agent on Alpaca paper trading.
Account `PA3XT8QFJZAQ`. Repo `github.com/jacklachan/alpaca`.
Team, and the one-line thesis: *the model proposes nothing it could get wrong.*

### Slide 2 · The uncomfortable opening
"Our agent lost money on the measured day." **$94,207.02, −5.79%**, taken from
Alpaca's own portfolio history rather than from anything we computed.

Do not bury this and do not spin it. Every other deck opens on an equity curve
going up. Opening on the loss is what earns attention for slides 3 onward, and
the criteria explicitly weigh more than P&L.

### Slide 3 · Two numbers for the same instant
**The most important slide in the deck.** `python tools/calibration.py`

```
Account at the measurement instant   (2026-09-03 16:00 ET)
  our mark, indicative feed    $99,642.35
  Alpaca's official close      $94,207.02
  gap                          $5,435.33   (5.77% of the account)
```

We price options off Alpaca's **indicative** feed — a derived estimate, not
OPRA. At the one instant that decides the result, our honest reading of the
book and the broker's official close disagreed by 5.77% of the account.

Neither is a lie. An option mark is an opinion until it is cash. That gap is
the whole reason the measurement-aware exit exists, and it is the strongest
single piece of evidence in the submission — because it is a *measured* number
from our own journal, not an argument.

The same tool also prints the pre-trade decay forecasts, read from the
hash-chained journal where they were written before the orders. They provably
predate the outcome and cannot be fitted afterwards without breaking the chain,
which `tools/verify_chain.py` reports.

**Say what it does not show.** The forecast was about decay. The account's
change also contains direction, and a mark cannot be split into the two after
the fact — so the difference is not model error. The tool says this itself, on
screen. Do not let anyone on the team turn it into "we predicted the loss."
That claim was in an earlier draft of this deck and it was wrong.

### Slide 4 · AI logic — one decision, one field
Required write-up topic 1.

Deterministic code builds fully specified candidates: contract, side, quantity,
limit price, maximum loss, exits, and a content-addressed ID. The set is
canonically ordered and hashed. The model sees summarised immutable fields and
returns **one candidate ID, or null.** That is the whole output schema.

Timeout, malformed JSON, unknown ID, altered object → abstention, journalled
with a reason. The exact original candidate goes to the kernel, never a
reconstruction.

So the worst a compromised model can do is pick a different pre-approved trade,
or nothing. It cannot invent a contract, widen a limit, raise size, or remove a
stop — there is no field in which to say so.

### Slide 5 · Risk gates — show the refusals, don't claim the principle
Required write-up topic 2.

A **13-invariant kernel**: symbol allowlist, bounded max loss, sleeve budget,
daily burn, concentration, position count, gross exposure, drawdown kill
switch, market hours, expiry guard, idempotency, sanity band, order frequency.
No network or model call, so the verdict is reproducible from its inputs.

Screenshot `python tools/demo.py` — four hostile plans, four different
invariants, under a second:

```
sell 400 naked SPY calls  → 02_bounded_max_loss
                            "unbounded maximum loss; long premium only"
hallucinated ticker       → 01_symbol_allowlist
100x oversized position   → 02_bounded_max_loss
                            "stated max loss 10 understates worst case 4052.49"
0DTE into the snapshot    → 05_concentration
```

Anyone can run that with no Alpaca account and no API key.

### Slide 6 · It knows when it is being scored
The originality slide.

The account is valued at a known instant, and we price options off Alpaca's
**indicative** feed — a derived estimate, not OPRA. So the last decision of the
week is not "is this position good?" but "can this position be marked
honestly?"

A contract quoting 30% wide at the snapshot is a number nobody can defend, in
either direction. Cash has no marking ambiguity. Inside 45 minutes of
measurement, a leg too wide to price gets flattened; a leg with no two-sided
quote is treated as the worst case, not as permission to hold.

Deliberately narrow: it never opens risk and never touches a position it can
price.

### Slide 7 · Alpaca infrastructure
Required write-up topic 3.

`alpaca-py`, pinned and hash-locked, against the **paper Trading and Data
APIs** — account and clock, server-authoritative option contracts via
`GetOptionContractsRequest` with pagination, timestamped quotes and Greeks,
orders with deterministic client order IDs, position reconciliation, and
`get_portfolio_history` for equity, because we are scored on Alpaca's number
rather than one we reconstruct. Trading sessions come from Alpaca's calendar,
not weekday arithmetic, which is silently wrong on holidays.

**The MCP client is built inverted, and this is the slide's real content.** The
official server's default toolset includes `place_option_market_order`,
`close_position` and `cancel_orders`. Rather than trusting configuration to
hide them, our client declares the only tools it will ever call, discovers what
the server actually exposes, and enforces three independent barriers: an exact
allowlist, a mutating-verb scan that runs even for allowlisted names, and a
discovery gate.

Proven against the official **Alpaca MCP Server 3.4.7** with live credentials:
three authenticated read-only calls, four mutating tools refused *by attempting
them.*

A read-only **Alpaca CLI** evidence tool builds commands from an allowlist and
refuses any mutating token before a process starts, so it cannot become a
second order path.

### Slide 8 · The bug worth a slide
Pick one from §6. The IV term-structure bug is the strongest.

### Slide 9 · Check it without trusting us
`python tools/verify_submission.py` — 11 checks, no credentials, no network,
nothing mutated.

The one to name out loud: **every AI selection must name a candidate that was
actually offered.** If that check ever fails on real evidence, the model
authored a trade and our central claim is false. We shipped the thing that
could prove us wrong.

It also **replays**: each recorded candidate set is rebuilt from the ids and
hashes the agent journalled, and the address that produces is compared with the
one published at the time. Determinism stops being an adjective.

### Slide 10 · What we claim, and what we don't
Claimed: every option position had an exact maximum loss before it opened. The
model proposed and never executed. The code was frozen at the open, pinned to
one commit that refuses to run as anything else.

Not claimed: four days does not prove an edge. The agent runs on a laptop under
a watchdog, not a deployed host, so there is no soak evidence and we assert
none. P&L is a mark on an open position. And the hash chain detects edits to
the recorded history without proving we never regenerated it — Alpaca's own
order IDs are what a third party reconciles against.

*Reconcilable, not tamper-proof.*

---

## 3. Mapping to the five judging criteria

| Criterion | Where the deck answers it |
| --- | --- |
| P&L Performance | Slides 2–3. We are down. The answer is not to hide it but to show the loss was priced correctly in advance and the risk system behaved as specified. |
| Technology Implementation | Slide 7. Trading API + Data API + CLI + MCP, all four, with the MCP surface proven read-only by attempting the dangerous calls. |
| Creativity & Originality | Slides 3 and 6. Calibration-as-evidence and the measurement-aware exit. **Not** slides 4–5 — see §7. |
| Presentation & Execution | The whole deck, plus the video. Every claim has a runnable command. |
| Social engagement | Five posts live on LinkedIn tagging lablab.ai and Alpaca; drafts and reasoning in `SOCIAL.md`. |

---

## 4. Required one-page write-up

Already satisfied and in the repo: `docs/WRITEUP.md`, 769 words, with sections
titled exactly *AI logic*, *Risk gates*, and *Alpaca infrastructure*. The rule
allows it to be a slide instead — if you would rather put it in the deck,
slides 4, 5 and 7 already are that write-up and you can say so.

Longer version with the detail that did not fit: `docs/WRITEUP-FULL.md`.

---

## 5. Facts and figures — with the command that re-checks each

| Fact | Value | Re-check |
| --- | --- | --- |
| Scored account | `PA3XT8QFJZAQ`, paper, $100,000 at activation | Alpaca dashboard |
| Premium committed, all week | $25,628 across three entries, cap $25,000 per sleeve | `python tools/calibration.py` |
| Open at measurement | one leg, 35× QQQ 8 Sep 717 C, $7,840 premium | same |
| Decay forecast, pre-trade | $6,797 on $25,628 committed | `python tools/calibration.py` |
| Indicative vs official, at measurement | $99,642.35 vs $94,207.02, gap $5,435.33 | same |
| Measured-day close (Alpaca's own history) | **$94,207.02 (−5.79%)** | same |
| Kernel invariants | 13 | `tools/demo.py` |
| Submission checks | 11, all passing | `python tools/verify_submission.py` |
| Automated tests | 741 | `python -m pytest -q` |
| Crash-recovery drill | 14/14 | `python tools/crash_drill.py` |
| Journal entries | 76,000+, chain intact | `python tools/verify_chain.py` |
| Model responses journalled | 769, every one only an id or an abstention | `verify_submission.py` |
| MCP server proven against | official Alpaca MCP Server 3.4.7 | `state/mcp_proof.json` |
| Model | Featherless, `Qwen/Qwen2.5-72B-Instruct` | `.env` |
| Pinned commit for the run | read it from the log, never from prose | `grep -o "release gate: commit [0-9a-f]*" state/agent.out \| tail -1` |

**Positions at the measurement instant** (one leg; the strangle's other legs
were closed earlier in the session):

```
QQQ 8 Sep 717 C   35 lots   paid $7,840   premium still open at measurement
$25,628 of premium was committed across the week; $17,788 of it was closed
before the account was valued.
```

---

## 6. The three stories, if you want a narrative slide

**The IV term-structure bug — the strongest.** The agent buys volatility when
options look cheap against what the underlying is actually doing, refusing
anything above 1.35x implied-to-realised. It was reading the vol of the *front*
contract — but the contract it buys is chosen afterwards, and the front month
is systematically the most expensive thing on the board, because it crams
weekend and event premium into the fewest days.

```
front  (2 Sep)  14.92% implied vs 7.64% realised → 1.95 → stand down
bought (8 Sep)  10.27% implied vs 7.64% realised → 1.34 → trade
```

It was refusing trades based on the price of something it was never going to
buy. It would have run flawlessly all week and never placed an order, with a
journal full of abstentions that all looked correct. Both halves read fine in
isolation; only the *order* was wrong.

**Two correct gates fighting.** The strategy picks the shortest expiry that
survives to the snapshot, because that is the most convexity per dollar.
Short-dated options burn a large share of premium daily — that is the
structure, not a defect. A separate gate refused anything burning >12% of
premium per day. Measured live: a complete 35-lot QQQ strangle at $502/pair,
refused at 13.59%/day. The fix was not a looser number: a *daily* rate limit
silently assumes a one-day horizon and we hold for two, so it now measures
decay actually paid before measurement, capped at a third of premium — which is
a *tightening* for long holds.

**The off-by-one that chose the wrong contract.** Sessions-remaining counted
the measurement day itself. Correct at a 09:30 snapshot, wrong at an end-of-day
one. Every contract over-reported its life by one session, letting a
one-session expiry pass the two-session guard — the selector was picking a stub
contract and calling it "3x convexity". Twelve tests encoded the old count and
had to be rewritten.

---

## 7. Lines to avoid — this part matters

**Do not say "LLM proposes, code decides," or any close paraphrase.** Two
submissions already published on the event page use almost exactly our framing:

- *ThetaSwarm* — "separates AI reasoning from execution by letting an LLM swarm
  propose trades, while a deterministic Python risk engine and the Alpaca MCP
  server enforce hard mathematical guardrails."
- *ORION* — "deterministic quant, an adversarial challenger, and an independent
  Risk Governor… 'No Trade' is a first-class decision."

That is our thesis, twice, in their words. Bounded authority is table stakes at
this event, not a differentiator. **Show the refusals; never claim the
principle.** What is genuinely ours is slide 3 (the forecast that provably
predates the outcome) and slide 6 (marking-aware exit). Spend the originality
budget there.

**No Sharpe, Sortino, Calmar, or any annualised figure.** We have five daily
observations. Our own dashboard marks those *indicative* below twenty, and
quoting one on a slide contradicts the premise of the project.

**Do not write "fully autonomous" or "production ready."** It runs on a laptop
under a restart watchdog. "Unattended since Monday under a restart watchdog" is
true and is enough.

**Do not show the Alpaca dashboard home page in any screenshot** — the API key
panel is on it. Orders and Positions tabs are fine.

---

## 8. Assets that already exist

| Asset | Where |
| --- | --- |
| One-page write-up | `docs/WRITEUP.md` |
| Full write-up | `docs/WRITEUP-FULL.md` |
| Video script and shot list | `VIDEO.md` |
| Social posts as published | `SOCIAL.md` |
| Operating notes and restart procedure | `STATUS.md`, `docs/OPERATIONS.md` |
| Why each decision was made | `DECISIONS.md` |
| Credential-free demo for screenshots | `python tools/demo.py` |
| Calibration result | `python tools/calibration.py` |
| Submission verification | `python tools/verify_submission.py` |
| Dashboard (no credentials needed) | `uvicorn dashboard.app:app --port 8080` |

**Screenshot suggestions:** `tools/demo.py` section 1 for slide 5,
`tools/calibration.py` for slide 3, `tools/verify_submission.py` final list for
slide 9.

---

## 9. Open items on the submission form

The form asks for a **Demo application platform** and an **Application URL**.
We have no hosted deployment — the agent runs on a laptop. The dashboard runs
locally and needs no credentials, so if a URL is required, that is the thing to
host. Otherwise the repository URL is the honest answer.

Deadline: **Friday 4 September, 8:30 PM IST**.
