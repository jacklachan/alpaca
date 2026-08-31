# What is required now

**Branch state:** `main` and `utk-review` are identical at `8e8da2d`, CI green.
**Suite:** 663 tests, crash drill 14/14, `make verify` exit 0, submission
verification VERIFIED with 0 contradictions.
**Merged:** `main` fast-forwarded to contain `review` and `utk-review` in full.

---

## 1. The only two things that still move the score

Neither needs code. Both need credentials I do not have and should not.

### Trade the account. Today.

The measurement window opened **Monday 31 August 09:30 ET** and closes
**Friday 4 September 09:30 ET**. The account has never placed an order, so the
journal is empty, the equity curve has no points, and the performance panel has
nothing to draw.

P&L is a full quarter of the rubric and it is currently zero. "Demonstrates the
agent in action" is unanswerable for an agent that has never acted. Every hour
spent hardening now trades against the only dimension still at zero.

```bash
# 1. fresh, dedicated, $100,000 paper account -> .env
#    ALPACA_ENV=scored
#    ALPACA_EXPECTED_SCORED_ACCOUNT_ID=<the account number Alpaca returns>
python -m glassbox.preflight        # refuses on a malformed .env
python main.py --dry-run            # proves account identity, does not trade
python main.py                      # runs the schedule
```

`--dry-run` contacts Alpaca and asserts the account is the expected one without
starting the clock. If it refuses, read the exit code table in
`docs/OPERATIONS.md` before changing anything.

### Capture the CLI and MCP proofs

The event requires the Trading API **plus either** the MCP server or the CLI.
Both tools are built, allowlisted, and tested; neither has run against the real
thing, which is arguably a compliance gap rather than a scoring one.

```bash
python tools/capture_alpaca_proof.py --out state/cli_proof.json
python tools/verify_mcp_surface.py --command <how-to-start-the-official-server>
```

Each takes minutes with credentials. `tools/verify_submission.py` will flip
those two rows from SKIP to PASS once the bundles exist.

## 2. Still open, lower priority

- **Wire the trade stream.** `glassbox/trade_stream.py` is complete and tested
  and deliberately not wired. I tried gating entries on it and removed that:
  REST reconciliation already proves the book exactly, so the gate could never
  fire, and ordering it the other way deadlocked. Its real value is latency,
  which needs a live socket to be worth anything. Revisit once the agent has
  been running.
- **Re-run the matrix on Python 3.12.** CI runs 3.12 and is green; local runs
  used 3.13 because 3.12 was unavailable.
- **`LICENSE` copyright line** still reads `The Glassbox Contributors`. One
  line, if a specific legal entity is wanted.
- **The `utk` branch is superseded.** It has zero unique files, every shared
  file is smaller than its `utk-review` counterpart, and merging it produces
  five conflict regions in the execution core. It was deliberately not merged.
  Delete it or leave it; do not merge it without re-reading both versions.

## 3. What changed this session

| Commit | What |
| --- | --- |
| `555ff04` | Fixed the CI failure: `sys.platform`, not `os.name` |
| `8fc7c19` | Closed two ways the scored account could still be touched |
| `621fd14` | Corrected the handoff's diagnosis |
| `bdae31a` | Trading sessions from Alpaca's calendar; removed 48 lines of dead code |
| `9b3dd5e` | Deterministic replay of recorded decisions |
| `a75af13` | Reconciliation gaps heal; corruption still needs a human |
| `62ab007` | Recorded why the stream stays unwired |
| `d59f1b5`, `8e8da2d` | Documentation |

**The CI failure was misdiagnosed as Linux-specific.** It reproduces on macOS
too, and on every platform except Windows: mypy narrows on `sys.platform`, not
`os.name`, so a Windows-only branch written under `os.name` is type-checked
everywhere its API does not exist. The type gate was green on exactly one
machine. `make type` now checks linux, darwin and win32.

**Two scored-account gaps.** `tools/practice.py` compared
`os.getenv("ALPACA_ENV")` to `"scored"`; systemd keeps inline `#` comments in
values, so `scored  # note` walked past the guard while `Broker` resolved the
same variable to `scored` and connected. And `PositionManager` had a *comment*
saying the symbol-wide close must never run on the scored account, with nothing
enforcing it -- while `practice.py` builds a manager with no ledger. Both closed.

**Sessions came from a hardcoded table** holding one holiday, Labor Day 2026.
Every expiry decision rests on that count. An agent still running in October
would have counted Thanksgiving as a session and overstated every contract's
life, silently. Sessions now come from Alpaca's calendar, with the source
recorded and an offline fallback for tests.

**Determinism became checkable.** The journal now records the parts each
candidate-set hash was built from, so `verify_submission.py` rebuilds the
address and compares it to the one published at the time.

**One fault latch became two.** Nothing ever cleared `_state_faulted`, so a
single transient failure to read open orders stopped the agent for the rest of
the process -- a blip on Monday and nothing trades all week. A reconciliation
gap now clears when the venue and ledger agree exactly; durable corruption
still never clears itself.

**Removed 48 lines of dead code**, including `MarketData.feature_table`, a
leftover from before the AI was bounded to picking an id.

## 4. Do not close these on your own authority

| Gate | Needs |
| --- | --- |
| Scored activation | A fresh dedicated $100,000 paper account and exact identity proof |
| Development venue proof | Dev credentials, distinct expected IDs, explicit authorisation for one capped write ($50 ceiling) |
| CLI / MCP proof | Credentials plus a pinned server or CLI release |
| Deployment / soak | A named VPS/SSH target and a reviewed full SHA |
