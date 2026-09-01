# Status — agent is live

Written 1 Sep 2026, ~02:30 ET. Read this first when you're back.

## The agent is running

```
account   PA3XT8QFJZAQ   (scored, judged)   equity $100,000   options level 3
mode      scored, options-only, release gate green
commit    3678e14561bb84b01e9fb8aaa57638f68f24a718
model     Featherless / Qwen/Qwen2.5-72B-Instruct  (bounded selector)
log       state/agent.log
lock      state/scheduler.lock
```

Release gate: **all four checks PASS** — journal chain, account identity, CLI
proof, dev venue proof. `--dry-run` passed, then the schedule started.

**First trading tick is 09:30 ET (19:00 IST).** Nothing happens before that —
the equity market is shut. Crypto ticks run continuously.

## Restarting it

The release gate pins an exact commit, so **any new commit invalidates the
manifest** and startup refuses with "current checkout drift". Rebuild first:

```bash
python tools/build_release_manifest.py     # prints the SHA to use
export GLASSBOX_RELEASE_GATE=1
export GLASSBOX_APPROVED_COMMIT_SHA=<the SHA it printed>
python main.py
```

Delete `state/scheduler.lock` first if a previous process died holding it.

## The model

Running through Featherless, not Anthropic. `Qwen/Qwen2.5-72B-Instruct`, which
is ungated -- `meta-llama/*` returns 403 until a HuggingFace account is linked
to the org. Config lives in `.env` as `LLM_PROVIDER` / `LLM_API_KEY` /
`LLM_MODEL` / `LLM_BASE_URL`.

The model's whole authority is choosing one pre-priced candidate ID or
abstaining. It cannot author a trade, so a small model is adequate and a dead
model is survivable -- the deterministic sleeves keep running and the journal
records `CANDIDATE_SELECTION_UNAVAILABLE`.

## Check on it

```bash
tail -20 state/agent.log
python tools/verify_chain.py
python tools/verify_submission.py
```

Dashboard, needs no credentials:

```bash
uvicorn dashboard.app:app --port 8080
```

To stop it: `Stop-Process -Id <pid from state/scheduler.lock> -Force`, then
delete `state/scheduler.lock` before restarting. The lock is a singleton guard
— it refuses a second scheduler against the same state, which is why a stale
lock blocks a restart.

## The one thing still on you: it dies if the laptop sleeps

This is running on your machine. `deployment_soak` is not a required release
check, so the gate passed without a VPS — but nothing has proven the process
survives three days, and **Windows sleeping will kill it**.

Two options, in order of preference:

**1. A $5 VPS.** `deploy/setup.sh` provisions it and `deploy/glassbox.service`
runs it under systemd with `Restart=always`. Roughly 30 minutes, and then the
laptop is irrelevant. I can't do this part — it needs an account and payment.

**2. Stop the laptop sleeping.** Settings → System → Power → Screen and sleep
→ set both to *Never* while plugged in. Cheaper, weaker: a reboot, an update,
or a closed lid still ends the run.

If it does die, restarting is safe. State is durable and it reconciles against
the venue on the next tick — it will not double a position.

## What changed tonight

Six real bugs, all found by running the thing rather than reading it:

1. **`sessions_remaining_at_measurement` counted the measurement day.** Correct
   at a 09:30 snapshot, wrong at an EOD one. Every contract over-reported its
   life by one session, which let a one-session expiry pass the two-session
   guard. The selector was choosing the 4 Sep stub over 8 Sep and calling it
   "3.00x convexity". Twelve tests encoded the old count and were rewritten.
2. **`capture_alpaca_proof.py` and `verify_mcp_surface.py` never loaded
   `.env`.** The two tools the release gate depends on were the only two that
   could not authenticate.
3. **`config get` is not a command in Alpaca CLI v0.0.14.** The CLI proof
   failed on every run.
4. **The CLI was not installed** and there is no `go`/`brew`/`scoop` here. I
   pulled the official Windows binary and verified its SHA-256.
5. **`live_check` sold the order's `filled_qty`, not the position's.** Alpaca
   reports crypto fills to six decimals while positions carry nine, so the exit
   asked for more than existed, was rejected, and left dust — the exact
   outcome the tool exists to prevent.
6. **`.env` had inline comments and a `/v2` endpoint**, either of which fails
   the scored gate. `preflight` catches the first; the second is silent.

## Accounts — do not mix these up

| Account | Role | State |
| --- | --- | --- |
| `PA3XT8QFJZAQ` | **SCORED — judged.** In `.env` | trading |
| `PA3WWRSIJUKT` | dev. In `.env.dev` | used for the $25 venue proof |
| `PA3PB02CJ4F6` | original, unused | idle |

`PA3XT8QFJZAQ` is the number that goes on the submission form.

## Still open

- **VPS.** Above. The only thing between you and a genuinely unattended run.
- **Discord webhook is empty**, so there is no remote heartbeat. Paste a
  webhook URL into `DISCORD_WEBHOOK_URL` in `.env` and restart to get alerts.
- **Rotate the keys** that appeared in chat once the week is over.
- **Social posts.** A judged criterion still at zero.
- **Submission package**: video, deck, cover image, one-page write-up, and the
  account ID above.

## Read this before judging it by the equity curve

**Two gates decide whether it trades, and one is marginal.**

`chain_legs` rejects any option quote older than 30 seconds
(`MAX_OPTION_QUOTE_AGE_SECONDS`). While the market is shut, every quote is
hours stale and the strategy correctly produces nothing -- probing at 03:30 ET
showed all 4,000 contracts rejected at ages around 41,580s. That is expected.
**What is unverified is whether the indicative feed refreshes inside 30s
during the session.** If it does not, the agent will stand down all day with a
journal full of `OPTION_QUOTE_REJECTED`. Check that after the open; if it is
the cause, `MAX_OPTION_QUOTE_AGE_SECONDS` is the dial.

The cheapness gate is **razor thin**. On 1 Sep the chosen 8 Sep expiry priced
at 10.27% implied against 7.64% realised -- a ratio of **1.344 against a cap of
1.35**. It passes by four thousandths. A small fall in realised vol pushes it
over and the agent stops trading entirely.

I have not widened `MAX_IV_TO_RV_RATIO`. It is a strategy parameter that trades
"never trades" against "overpays for vol", and that is your call, not mine.
But know that it is balanced on a knife edge, and that "no trades all week"
is the failure it produces.

## Honest note on P&L

Measurement is **EOD Thursday 3 September**, which is about three sessions
away. The agent is options-only on the scored account and will stand down
rather than force a trade when convexity is not cheap — an empty journal entry
saying why is a correct outcome, not a failure.

The payrolls print on Friday 4 Sep is **outside** the window. The code knows;
do not let anyone re-add it.
