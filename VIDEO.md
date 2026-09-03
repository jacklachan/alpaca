# Demo video — script and shot list

Target **3:00**. lablab does not publish a hard length for this event, so this
is written to the platform norm (under 5 minutes), with a 2:00 cut at the
bottom if the deck already covers the architecture.

Everything below is a real command with real output. Nothing is mocked, and no
number in the narration is rounded in our favour. If a take produces a
different number than the script says, **read the number on screen**, not the
script.

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
  be recorded on any machine. `demo.py` runs in about five seconds,
  `verify_submission.py` in about a minute — cut to its finished output.
- Rehearse `calibration.py` once first. It hits Alpaca for the live mark; if it
  cannot authenticate it prints the forecast alone and says so. Correct
  output, weaker shot.

---

## 0:00 — 0:15 · Hook

**On screen:** title card — *Glassbox · autonomous options agent on Alpaca
paper trading* — then cut to a terminal.

> Our agent lost money this week.
>
> I'm going to show you why we think that's the most interesting submission
> you'll watch today.

*Do not soften this. Every other video opens on an equity curve going up. An
equity curve is an outcome; it proves nothing about the agent.*

---

## 0:15 — 0:45 · The one thing nobody else has

**On screen:** run it live.

```bash
python tools/calibration.py
```

Let the output sit. Point the cursor at the two forecast lines, then the error.

> Before it placed either trade, the risk model wrote down how much of the
> premium was certain to decay before the account gets valued.
>
> Those forecasts went into a hash-chained journal, timestamped — so they
> provably predate the outcome they're being judged against. They can't be
> fitted afterwards without breaking the chain.
>
> Predicted decay: six thousand seven hundred and seventy-seven dollars.
> Actual: six thousand three hundred and thirty-five. Off by one point eight
> percent of premium.
>
> The trade lost. The model that priced it was right. Direction is the other
> half of a long-volatility position, and the move simply never arrived.

*The tool prints that caveat itself. Show that — it is the credibility.*

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

**End card:** `github.com/jacklachan/alpaca` · account `PA3XT8QFJZAQ` · final
equity `[FILL IN AFTER 16:00 ET THURSDAY]`

---

## If P&L ends positive

Swap the hook and nothing else.

> Our agent finished the week up. I'm going to spend most of this video on the
> parts that have nothing to do with that number, because four days of P&L is
> noise, and we'd rather be judged on the machine.

The close already says four days proves nothing, so it still works. Do **not**
cut the calibration segment if we finish green — it is stronger, not weaker,
when the outcome is good, because it shows the forecast was honest either way.

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
