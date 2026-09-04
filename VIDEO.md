# Demo video — script and shot list

Target **3:00**. lablab does not publish a hard length for this event, so this
is written to the platform norm (under 5 minutes), with a 2:00 cut at the
bottom if the deck already covers the architecture.

Everything below is a real command with real output. Nothing is mocked, and no
number in the narration is rounded in our favour. If a take produces a
different number than the script says, **read the number on screen**, not the
script.

## The numbers, so nobody has to remember them

Read these off the page; do not paraphrase from memory.

| | |
| --- | --- |
| Measured-day close, Thu 3 Sep | **$94,207.02, -5.79%** — settled, Alpaca's own portfolio history |
| Our reading of the same instant | **$99,642.35** — indicative feed |
| Gap | **$5,435.33, 5.77% of the account** |
| Scored account | `PA3XT8QFJZAQ` |

**The final number is not settled yet.** One leg is still open and the agent
closes it at Friday's open, 19:00 IST. Until that fills, equity is a mark on an
option that has not traded — the exact thing this video argues is an opinion
rather than cash. **Record after 19:15 IST**, or record the hook as written
below, which depends on none of it.

---

> **The take recorded on 3 Sep is stale for segment one only.** `calibration.py`
> was rewritten after that recording: it used to compare the forecast against
> whatever was open when you ran it, which made the headline number drift. It
> now freezes at the measurement instant and leads with a different, stronger
> finding. **Re-record the `calibration.py` segment.** The `demo.py` and
> `verify_submission.py` segments are unaffected and still usable.

---

## Read this first: two of the four shots only exist on Mohit's laptop

`state/` is gitignored, and it holds the journal, the position ledger, the
release manifest and the proof bundles. That is deliberate -- it is live
account evidence -- but it means a fresh clone cannot produce two of the
segments below.

| Command | On a fresh clone | Why |
| --- | --- | --- |
| `tools/demo.py` | **works** | builds its own journal in a temp dir, no credentials, no `state/` |
| `tools/calibration.py` | prints "no decay forecast recorded yet" | reads the live journal under `state/` |
| `tools/verify_submission.py` | fails or skips most checks | reads journal, ledger, manifest, proofs -- all under `state/` |

So: **Mohit records the terminal, Tanush edits and voices over.** One
continuous take of the three commands in order runs about 15 seconds of
actual command time, so a relaxed take with pauses is under two minutes.

Do not work around this by committing `state/` or by displaying saved output
as though it were running. A pre-captured text file shown as a live terminal
is the one thing in this submission that would actually be dishonest, and the
whole video is an argument about honesty.

---

## Before you record

```bash
python tools/demo.py
```

```bash
python tools/calibration.py
```

```bash
python tools/verify_submission.py
```

- Terminal at a large font, dark background, **maximised** — the refusal
  reasons wrap badly in a narrow window.
- Close every other tab and window. **Never show the Alpaca dashboard home
  page** (the API key panel is on it), and never show `.env` in an editor.
- `demo.py` and `verify_submission.py` need no credentials, so those two can
  be recorded on any machine. Measured on this laptop: `calibration.py` 3s,
  `demo.py` 1s, `verify_submission.py` 7s. None of them stall, so a long
  pause on screen means something is wrong, not that it is working.
- Rehearse `calibration.py` once first. It hits Alpaca for the live mark; if it
  cannot authenticate it prints the forecast alone and says so. Correct
  output, weaker shot.

---

## 0:00 — 0:15 · Hook

**On screen:** title card — *Glassbox · autonomous options agent on Alpaca
paper trading* — then cut to a terminal.

> At the exact instant our account was valued, we thought it was worth
> ninety-nine thousand, six hundred and forty-two dollars.
>
> Alpaca said ninety-four thousand, two hundred and seven.
>
> Same account. Same second. Five thousand four hundred dollars apart — and
> neither of us was lying.
>
> That's what this video is about.

*This replaced "our agent lost money this week", which was true on Thursday and
stopped being true on Friday. The hook now rests on a settled, frozen fact
instead of a number that moves, and it is a stranger opening than any equity
curve. Do not soften it and do not add a P&L figure here — the close carries
that.*

---

## 0:15 — 0:45 · The one thing nobody else has

**On screen:** run it live.

```bash
python tools/calibration.py
```

Let the output sit. Point the cursor at the forecast lines, then at the two
numbers under *Two numbers for the same instant*.

> Before it placed a trade, the risk model wrote down how much of the premium
> was certain to decay before the account gets valued. That went into a
> hash-chained journal, timestamped — so it provably predates the outcome and
> can't be fitted afterwards without breaking the chain.
>
> But look at the bottom. At the exact instant the account was valued, our own
> reading of the book said ninety-nine thousand six hundred and forty-two.
> Alpaca's official close, same account, same second, said ninety-four
> thousand two hundred and seven.
>
> Five thousand four hundred dollars apart. Nearly six percent of the account.
>
> Neither number is a lie. We price options off Alpaca's indicative feed, which
> is a derived estimate — and an option mark is an opinion until it's cash.
>
> That gap is the whole reason this agent would rather hold a number it can
> defend than one that merely looks better.

*The tool also prints what it does NOT show — that the difference between
forecast and outcome is not model error, because a mark contains direction as
well as decay. Leave that on screen. It is the credibility.*

**Do not say "we predicted the loss."** An earlier cut of this script did, and
it was wrong: the forecast was about decay alone, and the account's change also
contains direction. The tool was rewritten to stop implying otherwise.

---

## 0:45 — 1:10 · What the model is not allowed to do

**On screen:** `python tools/demo.py`, hold on section 1.

> Every team here will tell you their LLM is supervised. Here is ours failing
> to do damage.

Four refusals land on screen. Read two of them aloud, not all four:

> Sell four hundred naked calls — refused, unbounded maximum loss.
> A hallucinated ticker — refused, not on the allowlist.
>
> The model's entire output schema is one candidate ID from a pre-priced list,
> or null. It cannot set the contract, the size, the side, or the limit price.
> There is no field in which to say "bigger", or "naked", or "no stop".
>
> The worst a compromised model can do here is pick a different pre-approved
> trade, or nothing at all.

---

## 1:10 — 1:35 · It knows when it is being scored

**On screen:** section 2 of the same run.

> Most agents don't know when they're being measured. Ours does, and it
> changes what it does about it.
>
> We price options off Alpaca's indicative feed — a derived estimate. So the
> last decision of the week isn't "is this position good?" It's "can this
> position be marked honestly?"
>
> A contract quoting thirty percent wide at the snapshot produces a number
> nobody can defend, in either direction. Cash has no marking ambiguity.

HOLD, HOLD, FLATTEN, FLATTEN on screen.

> It never opens risk, and it never touches a position it can actually price.
> Sometimes the right last move is to take the certain number.

---

## 1:35 — 1:55 · The journal, and what it does not prove

**On screen:** sections 3 and 4 — chain intact, then one byte changed, then
`FAIL (correct)`.

> Every decision is in an append-only hash chain. Change one byte and it tells
> you exactly which entry moved.
>
> And here is what we won't claim. This detects edits to the recorded history,
> but it does not prove we never regenerated the whole thing, because we
> control every input to the hash. Alpaca's own order IDs are what a third
> party actually reconciles against.
>
> Reconcilable, not tamper-proof. The tool says so itself.

---

## 1:55 — 2:25 · The Alpaca surface

**On screen:** the account's Orders or Positions tab. **Not the home page.**

> Real orders, on scored paper account P-A-3-X-T-8-Q-F-J-Z-A-Q. Alpaca's
> options API for active contracts, timestamped quotes and Greeks. Portfolio
> history for equity, because we're scored on Alpaca's number, not on ours.
>
> We also run a read-only MCP client, built inverted. The official Alpaca MCP
> server exposes place-option-market-order, close-position and cancel-orders.
> Rather than trusting configuration to hide them, our client declares the
> only tools it will ever call, then checks what the server actually offers.
>
> We proved it by attempting the dangerous ones against the real server.
> Four mutating tools, four refusals.

---

## 2:25 — 2:50 · Check it without trusting us

**On screen:** `python tools/verify_submission.py`, cut to the finished list.

> Eleven checks. No credentials, no network, nothing mutated.
>
> One of them is that every AI selection names a candidate that was actually
> offered to it. If that check ever fails on real evidence, the model authored
> a trade, and our central claim is false.
>
> We shipped the thing that could prove us wrong.

---

## 2:50 — 3:00 · Close

> Four days doesn't prove an edge, and we're not claiming one.
>
> What we claim is this. Every option position had an exact maximum loss
> before it opened. The model proposed and never executed. The code was frozen
> at the open, pinned to one commit that refuses to run as anything else.
>
> It's all in the repo. Run it yourself.

Then state the result, plainly, in one line before the end card. Use the
measured day, which is settled:

> On the day we were measured, the account closed down five point seven nine
> percent. Four days of P&L is noise either way — we'd rather be judged on the
> machine.

**End card:** `github.com/jacklachan/alpaca` · account `PA3XT8QFJZAQ` ·
measured close `$94,207.02`

---

## Whatever Friday does, do not change the middle

The last leg closes at Friday's open and the final figure could land either
side of flat. That changes one line in the close and nothing else.

- **Finishes down:** the close as written. Say the number.
- **Finishes up:** add "and we finished Friday slightly above where we
  started" after it. Do not lead with it, and do not drop the measured-day
  figure — hiding the bad number to show the good one is the single fastest
  way to lose the credibility the rest of the video is spending three minutes
  building.

Do **not** cut the calibration segment either way. It is stronger when the
outcome is good, not weaker, because it shows the forecast was honest
regardless of which way the trade went.

## The 2:00 cut

Keep the hook, calibration, the refusals, the verification, and the close.
Drop the measurement-exit and MCP segments to a deck slide. Do **not** drop
calibration to save time; it is the only segment no other team can produce.

## Lines to avoid

- **"LLM proposes, code decides."** Several teams in this hackathon use almost
  exactly that phrase, and one named their submission it. Show the refusals
  instead of claiming the principle.
- **Any Sharpe or annualised figure.** We have five daily observations. Our own
  dashboard marks ratios *indicative* below twenty, and quoting one on camera
  would contradict the premise of the project.
- **"Fully autonomous" / "production ready."** It runs on a laptop under a
  watchdog. Say "unattended since Monday under a restart watchdog" — that is
  true, and it is enough.
