# Glassbox implementation handoff

**Prepared:** 2026-08-30
**Branch:** `review` (pushed to `origin/review`; `main` untouched)
**Last verified commit:** `feeacf108fb52d9896cdcdfa5feb3bb944df1eb1` (CI green,
run 33311898521; a later docs commit may update this file itself)
**Starting point for this session:** `eea74d0` (Task B, candidate provenance)
**Verification:** `make verify` exits 0; **395 tests pass**; crash drill 13/13;
GitHub Actions green (both `Tests and drills` and `Competition guards`)

This session continued the audit-derived backlog in
`GLASSBOX-REFERENCE-MASTER-PLAN.md`. It did not reopen the reference audit and
did not redo completed checkpoints. No order was placed, no deployment
happened, and nothing was merged to `main`.

---

## 1. Read this first

The product contract is unchanged and is the thing to protect:

1. Deterministic code acquires and validates Alpaca account, clock, active
   option contracts, and timestamped quotes.
2. Deterministic SPY/QQQ strategies build fully specified, pre-priced option
   candidates with stable identity.
3. Bounded AI returns **exactly one offered candidate ID, or abstains**. Any
   timeout, model error, malformed output, extra field, unknown ID, altered
   object, or missing credential means abstention.
4. The exact original immutable candidate passes deterministic policy and the
   risk kernel.
5. Intent is durable before mutation; every transition stays attributable to
   deterministic plan/client-order identity.
6. Success means venue-confirmed terminal order state and exact per-contract
   position reconciliation -- never request acceptance or a local log entry.

Equity and crypto remain disabled on the scored account.

## 2. Environment note (read before you run anything)

The repository was authored on Windows and now also runs on macOS. Two things
follow.

- **Line endings.** The working tree arrived with CRLF while every committed
  blob is LF, which made `git status` show 40 files as modified with a ~19,000
  line phantom diff. It was verified to be line-ending-only (every file byte
  identical modulo `\r`) and normalised. If you see that again, check
  `git diff HEAD --raw` -- it reports the real change set -- before believing
  `git status`. Consider adding a `.gitattributes` with `* text=auto eol=lf`.
- **`git grep` vs GNU grep.** CI's committed-credential scan uses GNU grep.
  macOS `git grep -E` treats `\b` differently and matched nothing, so that
  gate passed locally and failed on the runner. The first push was red for
  exactly this reason: a PK-shaped fake key in a test fixture. Fixed on the
  fixture side -- the fake keys are assembled at runtime, since excluding
  `tests/` from the scan would create the blind spot the scan exists to cover
  -- and `tests/test_claims.py` now runs the same pattern through Python so
  local and CI agree everywhere.
- **Python.** The committed `.venv/` is a Windows venv (`Scripts/`, not
  `bin/`). This session used a separate `.venv-mac/` on Python 3.13.12 because
  3.12 was unavailable on the host. **The release target is still 3.12**; CI
  runs 3.12 and that remains authoritative. Re-run the matrix on 3.12 before
  any release claim.

```bash
python3 -m venv .venv-mac && .venv-mac/bin/python -m pip install -r requirements-dev.lock
make verify PYTHON=.venv-mac/bin/python
```

## 3. What was completed this session

Each item is one commit and one rollback boundary.

| Commit | Task | What it changes |
| --- | --- | --- |
| `58144e7` | C | Typed Alpaca failures; unknown is never absence |
| `5317724` | D | Pure order-lifecycle reducer, monotonic fill |
| `42bac6d` | E | Per-contract position ledger, exact exits, singleton lock |
| `bee13df` | G+H | Release/account manifest; read-only CLI proof capture |
| `c1bf93c` | I | MIT `LICENSE`, generated `THIRD_PARTY_NOTICES.md` |
| `ca5bf1a` | J | Public claims checked against the code |
| `73d2307` | K | Notices gate in `make verify`; measurement date closed |
| `74e6b90` | - | This handoff |
| `feeacf1` | - | Test fixtures no longer trip CI's credential scan |

### C -- typed Alpaca failures (`glassbox/broker.py`, `glassbox/execute.py`)

`get_order_by_coid` collapsed every exception into `None`. "The venue says that
order does not exist" and "we could not reach the venue" reached callers as the
same answer -- the difference between an intent with no order and an intent
whose order may be working and filling.

Now: `OrderNotFound`, `BrokerAuthError`, `BrokerValidationError`,
`BrokerRateLimited`, `BrokerUnavailable`, `BrokerUnknownState`, produced by
`classify_broker_error`. Anything unidentifiable becomes `BrokerUnknownState`,
which is neither retryable nor absence. Lookup returns `None` only for a
verified 404. `_call` retries idempotent operations only, with bounded jittered
backoff honouring `Retry-After`; `close_position` is marked non-idempotent.
`_await_fills` tolerates a few transient lookup failures then marks the leg
uncertain, which blocks the residual-cancel path. 22 broker tests, 24 executor.

### D -- order lifecycle reducer (`glassbox/order_lifecycle.py`)

A pure reducer: state + one observation -> new state. No network, clock, or
disk, so duplicate polls, stale answers arriving late, fills after a cancel
request, and replacement chains are table-driven tests. Cumulative fill never
decreases; `pending_cancel` is never terminal; an unrecognised status sets
`unknown`, which is never terminal. The same monotonic rule was wired into
`ExecutionEngine._refresh_leg`, which previously overwrote leg fill with
whatever the latest read said. 37 tests including a seeded property test over
shuffled, duplicated observation streams.

### E -- position ledger and exact exits (`glassbox/position_ledger.py`)

The exit path called `close_position(symbol)`, which liquidates everything the
**account** holds in a contract, not what **this strategy** holds -- and its
acceptance proves nothing about our quantity reaching zero.

Now expected signed quantity per contract is derived only from confirmed fills
and reconciled exactly against the venue. Unknown exposure, foreign exposure,
a missing position, a quantity mismatch, or an open order outside our client-ID
family all fail closed and block new entries. Exits size to the exact owned
quantity under a deterministic ID (`glassbox/ids.py:exit_client_order_id`)
registered on disk *before* the mutation, fold the terminal read through the
reducer, and leave a partial exit retryable. Flat is proven only from a
terminal order plus a zero venue quantity. Persistence is schema-versioned and
checksummed; a corrupt, foreign-account, or foreign-environment ledger raises
rather than healing to empty. `ProcessLock` in `glassbox/state.py` makes two
schedulers against one state directory impossible. 23 + 26 tests.

`PositionManager` takes the ledger optionally. **With** one it uses the exact
path; **without** one it keeps the development symbol-wide path. Wiring the
ledger into `main.py`/`scheduler.py` for the scored account is the next task
(see below).

### G/H -- release identity and CLI proof

`glassbox/release.py` binds commit + dirty flag, both lock hashes, Python and
platform, policy hash, resolved paper endpoint, environment, and a redacted
account suffix. `assert_scored_startable()` refuses dirty, non-paper, unbound,
or non-options-only starts. `write()` fails *before* touching disk if any
credential value or marker appears in the body.

`tools/capture_alpaca_proof.py` captures the event-required CLI evidence
read-only. Commands come from an allowlist and a mutating token anywhere in a
built argv is refused before a process starts, so even a bad edit to the table
cannot place an order. Output is redacted, hashed against the real bytes, and
written atomically; a nonzero exit, unparseable JSON, or a wrong account ID
marks the proof **incomplete** rather than absent.

### I/J/K -- legal, claims, verification

MIT `LICENSE` (see section 6 on the copyright line). `THIRD_PARTY_NOTICES.md` is
generated from `requirements.lock` by `tools/build_notices.py`; all 40 runtime
packages are permissive (MIT/BSD/Apache-2.0/MPL-2.0/PSF). `make verify` fails
if the notices go stale. `tests/test_claims.py` fails when the README claims
more than the code supports.

Also fixed: `deploy/setup.sh` used `${1,,}` (bash 4+) and died on macOS bash
3.2 before any step ran.

## 4. Measurement date -- audit gate now closed

The reference audit recorded the 3 September cutoff as **unconfirmed** because
the public event page shows only the 4 September deadline. Re-verified this
session against the archived Alpaca guidelines document. It carries both dates
and they are the same number, not a conflict:

- "Official P&L measurement: Monday, August 31 ... to Friday, September 4 at
  9:30 a.m. ET. We will be looking at the portfolio's total equity as of EOD
  Thursday Sep 3rd."
- "The measurement window ends at 9:30 a.m. ET on Friday, September 4, when a
  snapshot of total account equity will be taken."

The market is shut between Thursday's close and that Friday snapshot, so EOD
Thursday 3 September equity is what the snapshot photographs. `MEASUREMENT_ET`
stays 2026-09-03 16:00 ET, which is also the conservative reading: stop taking
risk by Thursday's close. Reasoning is now recorded in `glassbox/macro.py`
where the constant lives.

**Timing:** trading is meant to begin Monday 31 August 09:30 ET, and the
window closes Friday 4 September 09:30 ET.

## 5. Next task, in dependency order

1. **Wire the ledger into the scored path.** `PositionManager` accepts
   `ledger=`/`ledger_path=` but `main.py:68` still constructs it without one,
   so the scored account would still take the development symbol-wide close.
   Build the ledger at startup, feed `record_entry_fill` from confirmed
   executor fills, and call `reconcile()` in the scheduler's pre-entry gate so
   a fault blocks new entries. This is the highest-value remaining change.
2. **Gate scored start on the release manifest.** `assert_scored_startable()`
   exists and is tested but nothing calls it. Add `GLASSBOX_RELEASE_GATE=1`
   handling in `main.py`.
3. **Task F, trade-update stream** (`glassbox/trade_stream.py`) -- Should, and
   explicitly only after polling/restart correctness is proven. REST stays
   authoritative.
4. **Evidence UX** -- the read-only proof drawer showing candidate -> select or
   abstain -> kernel -> intent -> lifecycle -> reconciliation.
5. **Pin GitHub Actions by SHA.** `.github/workflows/ci.yml` still uses
   `@v4`/`@v5` tags. Deliberately not guessed here; resolve the real digests.
   GitHub also warns on every run that `actions/checkout@v4` and
   `actions/setup-python@v5` target deprecated Node 20 and are being forced
   onto Node 24. It is a warning, not a failure, and bumping to current major
   versions resolves it at the same time as the pinning.

## 6. Open gates -- do not close these on your own authority

| Gate | Status | What it needs |
| --- | --- | --- |
| Development venue proof | **Open** | Dev credentials, distinct expected dev/scored account IDs, clean read-only baseline, explicit authorisation for one capped write ($50 ceiling) |
| CLI proof capture | **Open** | The tool is built and tested against fakes; no bundle has been captured from a real CLI. Pin a reviewed CLI release and run it |
| MCP | **Not claimed** | No MCP integration exists. `tests/test_claims.py` fails the build if the docs start claiming one |
| Deployment / soak | **Open** | Named VPS/SSH target, reviewed full SHA, secret delivery, successful dev proof |
| Scored activation | **Open** | All of the above, a fresh dedicated $100,000 paper account, exact identity proof, explicit direction |
| Python 3.12 re-verification | **Open** | Everything here was verified on 3.13.12 because 3.12 was unavailable on the host. CI runs 3.12 and is green, which covers it for now, but re-run the matrix locally on 3.12 before any release claim |
| Legal -- copyright line | **Needs your confirmation** | `LICENSE` reads `Copyright (c) 2026 The Glassbox Contributors`. You chose "a team name" but did not give the exact string, so a collective holder was used rather than guessing between the repo owner, the audit docs' author, and the account running the build. **Change the single line if a specific legal entity is wanted.** |

No claim of completed paper execution, realized P&L, deployment, soak, MCP, or
third-party attestation may be made without the corresponding captured
evidence.

## 7. Verification evidence (this checkout, 2026-08-30)

Run on Python 3.13.12 via `.venv-mac`. **Re-run on 3.12 before release.**

```
ruff format --check .                72 files already formatted
ruff check .                         All checks passed
mypy glassbox dashboard tools main.py  Success: no issues in 38 source files
pytest -q                            395 passed
pytest tests/test_kernel.py -q       33 passed
tools/crash_drill.py -n 8 --seed 1   DRILL PASSED 13/13
tools/env_parity.py .env.example     PARITY OK, 9 variables
compileall                           clean
pip check                            No broken requirements found
tools/build_notices.py --check       notices are current
pytest tests/test_dashboard.py -q    12 passed
make verify                          exit 0
```

CI competition guards re-run locally and passing: `.env` untracked, no
live-looking keys committed, paper guards intact, sleeve budgets sum to
100000, measurement window guards OK.

`tools/verify_chain.py` reports no journal, which is correct -- this checkout
has never run against an account.

## 8. Where things are

```text
glassbox/
  broker.py           Alpaca boundary, typed failures, identity, reconciliation
  order_lifecycle.py  pure reducer over observed order states
  position_ledger.py  per-contract ownership, exact venue reconciliation
  release.py          release/account identity manifest
  candidates.py       canonical candidate sets, manifests, selection receipts
  option_data.py      option contract and quote acquisition
  kernel.py           deterministic 13-invariant risk review
  execute.py          intent journal, fill/cancel/reprice state machine
  manage.py           exits; exact path when a ledger is supplied
  state.py            atomic fail-closed persistence, ProcessLock
tools/capture_alpaca_proof.py  read-only CLI evidence capture
tools/build_notices.py         regenerates THIRD_PARTY_NOTICES.md
```

Governing plan: `GLASSBOX-REFERENCE-MASTER-PLAN.md` (Tasks B-K).
Approved design: `docs/superpowers/`.
