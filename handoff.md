# Alpaca Hackathon Implementation Handoff

**Prepared:** 2026-08-30  
**Repository:** `C:\Users\Utkarsh\Desktop\Project\Trading\alpaca`  
**Branch:** `review`  
**Starting point:** `f2d25c7`  
**Completed implementation checkpoint:**
`3fac4c60b127aae196d0c44fec7b0536712d5f80`  
**Remote state at checkpoint:** local `review` was 11 commits ahead of
`origin/review`; nothing was pushed or merged

## 1. Executive Status

The approved Approach A implementation is complete through the final local
verification checkpoint.

Glassbox now has one scored execution contract: deterministic code creates
immutable, fully priced SPY/QQQ option candidates; bounded AI may select exactly
one supplied candidate ID or abstain; the selected original candidate still
passes through the deterministic risk kernel and hardened executor. Equity and
crypto sleeves are not registered on the scored account. Crypto remains a
separate development connectivity proof only.

All implementation, test, release, documentation, dashboard, and demo-truth
work is complete and committed. The remaining work is operational and is
correctly blocked on external inputs:

1. Development Alpaca credentials plus explicit expected dev/scored account
   IDs for the bounded venue proof.
2. Explicit direction to make the reviewed commit available remotely, because
   the current commits are local only.
3. A VPS/SSH target for exact-SHA deployment and soak.
4. Explicit user direction before any live paper order, push, deployment, or
   scored activation.

No live order, deployment, push, merge, or hackathon submission was performed.

## 2. Governing Documents

- Master status and checkpoint plan: `IMPLEMENTATION-PLAN.md`
- Approved architecture:
  `docs/superpowers/specs/2026-08-29-options-only-proof-first-design.md`
- Detailed TDD implementation plan:
  `docs/superpowers/plans/2026-08-29-options-only-proof-first.md`
- Current product overview: `README.md`
- Current operating decisions: `DECISIONS.md`
- Current operational plan: `PLAN.md`

Treat those files as current. `HANDOVER.md` and `HANDOVER-2.md` were reconciled
to the same truthful product state, but this lowercase `handoff.md` is the most
precise continuation record.

## 3. Exact Commit Ledger

All implementation commits are descendants of `f2d25c7` in this order:

| Commit | Purpose |
| --- | --- |
| `5d807ee` | Record the approved design and detailed TDD plan |
| `6c58dac` | Bind credentials to explicit expected account IDs |
| `3e6e7c3` | Add terminal cancellation confirmation |
| `ff29e4b` | Bound and exactly reconcile the live venue proof |
| `75cca48` | Prevent residual and overlapping entry orders |
| `8b5b9fd` | Add deterministic IDs, submission intent, and ambiguous-submit reconciliation |
| `9810390` | Add atomic fail-closed safety-state persistence |
| `6c0ea74` | Bound AI to selecting or abstaining among supplied candidates |
| `a225c80` | Enforce the options-only scored scheduler |
| `973edf8` | Pin dependencies, reviewed releases, and verification gates |
| `3fac4c6` | Finalize immutable candidates, truthful artifacts, safe practice, and the final proof checkpoint |

The documentation-only commit containing this handoff should be `HEAD` when
this file is read. The exact implementation checkpoint remains `3fac4c6`.

## 4. Audit Findings — Resolution Map

### Finding 1 — Environment labels did not bind account identity

**Resolved.** `glassbox/env.py` requires explicit expected development and
scored account IDs and refuses equal IDs. `glassbox/broker.py` compares the
broker-returned account number with the expected ID during readiness checks.

### Finding 2 — Live check could affect pre-existing holdings or exit zero on uncertainty

**Resolved in code and tests.** `tools/live_check.py` now:

- enforces `0 < notional <= $50.00` before submit;
- requires an exact clean position/open-order baseline;
- settles entry cancellation to terminal state;
- sells only the exact quantity filled by the test entry;
- never calls symbol-wide `close_position`;
- terminally settles the exit;
- proves exact flat state and no residual test orders; and
- returns nonzero for timeouts, cleanup warnings, or uncertain state.

The live proof itself remains unperformed because credentials and expected IDs
were not supplied.

### Findings 3–4 — Cancel/reprice race and residual GTC orders

**Resolved.** `glassbox/broker.py` exposes terminal cancellation polling.
`glassbox/execute.py` refreshes late fills from the terminal order before
calculating remaining quantity, refuses a replacement when cancellation is
uncertain, and cleans up incomplete equity, crypto, and option entry orders.

### Finding 5 — Random IDs made restart reconciliation ambiguous

**Resolved.** `glassbox/ids.py` creates stable IDs from canonical semantic
inputs. Strategies create deterministic plan IDs. The executor journals
`ORDER_SUBMIT_INTENT` before submission and adopts only an order observed under
the original client-order ID after an ambiguous broker exception.

### Finding 6 — Direct JSON writes and fail-open corruption

**Resolved.** `glassbox/state.py` implements same-directory temporary writes,
file fsync, atomic replace, and directory fsync where supported. Manage and
scheduler state validate on read and fail closed on corruption/write errors.

### Finding 7 — Default-branch deployment and unpinned dependencies

**Resolved in release tooling.** Runtime and development dependencies have
exact transitive locks. `deploy/setup.sh` requires a full 40-character reviewed
SHA, fetches and detaches that exact object, verifies `HEAD`, and installs the
runtime lock. An executable command-stubbed harness covers refusal, checkout,
verification, and lock behavior.

Operational deployment is still blocked because the commits are not pushed and
no VPS target was supplied.

### Finding 8 — AI/integration claims exceeded implementation

**Resolved.** `glassbox/thesis.py` has no plan-proposal path. AI output may name
one existing candidate ID or abstain. Invalid output, unknown IDs, missing
credentials, and model failures abstain. The exact original candidate is
returned; model output cannot author trade fields. Public artifacts describe
the Alpaca Trading/Data APIs and CLI actually used and make no unimplemented
integration claim.

### Finding 9 — Scored path included equity/crypto sleeves

**Resolved.** `glassbox/scheduler.py` registers deterministic SPY and QQQ event
option strategies only in scored mode, omits the crypto job, selects at most one
candidate, and refuses any injected non-option candidate before kernel review.

### Finding 10 — Documentation, UI, demo, and submission claims diverged

**Resolved.** README, plans, decisions, handovers, social copy, dashboard, CLI
help, schema commentary, and practice flow now agree on the actual system and
show the live/deploy gates. `tools/practice.py` is read-only; the only
write-capable development proof is the separately authorized bounded
`tools/live_check.py` flow.

## 5. Important Code Map

| Area | Primary files | Contract |
| --- | --- | --- |
| Account boundary | `glassbox/env.py`, `glassbox/broker.py` | Expected account ID must match broker account |
| Live venue proof | `tools/live_check.py`, `tests/test_live_check.py` | `$50` cap, clean baseline, exact created quantity, exact cleanup |
| Order execution | `glassbox/execute.py`, `tests/test_execute.py` | Intent-first submit, terminal cancel, no overlap or residual order |
| Stable identity | `glassbox/ids.py`, strategy modules | Same opportunity produces same plan/client IDs |
| Durable state | `glassbox/state.py`, `glassbox/manage.py`, `glassbox/scheduler.py` | Atomic state; corruption and write failure fail closed |
| Candidate schema | `glassbox/schema.py` | Frozen plan with tuple-backed option legs and evidence |
| Bounded AI | `glassbox/thesis.py`, `tests/test_thesis.py` | Select existing ID or abstain; never construct a plan |
| Scored policy | `glassbox/scheduler.py`, `tests/test_scheduler.py` | SPY/QQQ options only, at most one selected plan per cycle |
| Release | locks, `Makefile`, CI workflow, `deploy/setup.sh` | Exact dependencies and exact reviewed commit |
| Evidence UI | `dashboard/app.py`, `tests/test_dashboard.py` | Read-only truthful candidate/kernel/order timeline |
| Safe rehearsal | `tools/practice.py` | Read-only dev account and kernel demonstration |
| Recovery proof | `tools/crash_drill.py`, `tests/test_crash_drill.py` | Wait for first durable append before kill/restart drill |

## 6. Fresh Verification Evidence

Verification was run after the final behavior changes on 2026-08-30.

| Gate | Result |
| --- | --- |
| uv-managed CPython 3.12.11 + `requirements-dev.lock` full suite | `232 passed in 18.09s` |
| Existing Windows project environment, CPython 3.10.11 full suite | `232 passed in 102.79s` |
| Deterministic kernel suite | `33 passed in 0.75s` |
| Executable deploy harness | `8 passed in 58.47s` |
| Dashboard response/body suite | `12 passed in 3.50s` |
| Crash-recovery drill, eight real kill/restart rounds | `DRILL PASSED — 13/13 checks` |
| Crash-drill readiness regression | `2 passed` after the pre-fix test failed |
| Environment parity | systemd and python-dotenv agree on all 9 variables |
| Dependency consistency | no broken requirements |
| Ruff format | 56 files formatted |
| Ruff lint | all checks passed |
| mypy | no issues in 31 production source files |
| Compile | `compileall` passed for `glassbox`, `dashboard`, `tools`, and `main.py` |
| Claim scan | no stale generative-AI, old test-count, multi-sleeve, or unimplemented-integration claim |
| Secret scan | no live-looking Alpaca/Anthropic credential found outside ignored documentation |
| Diff hygiene | `git diff --check` passed |

One host-specific observation: during an earlier Windows CPython 3.10 run,
Python printed intermittent native access-violation diagnostics while starting
AnyIO TestClient threads and Git Bash subprocesses, but the processes continued
and all tests exited zero. The final target CPython 3.12 exact-lock run was
clean, fast, and exited zero. The current CI definition mirrors the target 3.12
lock and `make verify`; it has not been executed by hosted CI for these local
commits because pushing was explicitly out of scope.

## 7. External Gates and Required Inputs

### Gate A — Development Venue Proof

Still required:

- `ALPACA_API_KEY` for the development paper account.
- `ALPACA_SECRET_KEY` for that account.
- `ALPACA_EXPECTED_DEV_ACCOUNT_ID`.
- `ALPACA_EXPECTED_SCORED_ACCOUNT_ID`, distinct from the dev ID.
- Paper base URL and `ALPACA_PAPER_TRADE=true` as represented in
  `.env.example`.
- Explicit user authorization to place the bounded paper order.

Do not use scored credentials for this proof. Do not increase the hard `$50.00`
ceiling. Stop on any dirty baseline, identity mismatch, partial cleanup, or
uncertain order state.

### Gate B — Remote Availability and VPS Deployment

Still required:

- Explicit permission to push or otherwise publish the reviewed commit.
- Confirmation of the exact 40-character commit SHA to deploy.
- VPS hostname/IP, SSH user, and approved authentication route.
- Runtime secret-delivery method.
- Successful development venue proof evidence.

`deploy/setup.sh` fetches an exact remote object; the current local-only commit
cannot be deployed through that script until the SHA is available from the
configured remote.

### Gate C — Scored Activation

Still required:

- Successful venue proof and VPS soak.
- Reconfirmation of the expected scored account ID.
- Explicit user direction.
- Any organizer clarification that materially changes the approved
  options-only strategy. In the absence of such clarification, keep scored mode
  options-only.

## 8. Safe Continuation Commands

Run from the repository root.

### Re-establish local state

```powershell
git status --short --branch
git log --oneline --decorate -15
git rev-parse HEAD
```

Expected after this handoff is committed: branch `review`, clean working tree,
with the handoff documentation commit at `HEAD` and implementation checkpoint
`3fac4c60b127aae196d0c44fec7b0536712d5f80` immediately below it.

### Re-run the target-runtime proof

```powershell
uv run --isolated --python 3.12 --with-requirements requirements-dev.lock -- python -m pytest -q
```

### Re-run the complete local verification contract

Use a Python environment installed from `requirements-dev.lock`, then:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m mypy glassbox dashboard tools main.py
python -m pytest -q
python -m pytest tests/test_kernel.py -q
python tools/crash_drill.py -n 8 --seed 1
python tools/env_parity.py .env.example
python -m compileall -q glassbox dashboard tools main.py
python -m pip check
```

### After Gate A inputs are supplied

First run the read-only check:

```powershell
python tools/live_check.py
```

Only after it proves identity and cleanliness, and only with explicit
authorization, run the bounded trade proof:

```powershell
python tools/live_check.py --trade --notional 50
```

Preserve command output and journal evidence. A nonzero result is a stop signal,
not permission to retry blindly.

### After Gate B inputs are supplied

On the named Debian/Ubuntu target, with the exact published reviewed SHA:

```bash
sudo bash deploy/setup.sh <full-40-character-reviewed-commit-sha>
sudo -u glassbox /opt/glassbox/.venv/bin/python /opt/glassbox/main.py --dry-run
sudo bash /opt/glassbox/tools/soak.sh 30 3
```

Verify detached `HEAD`, dependency lock installation, journal integrity,
systemd restart behavior, and dashboard health before considering scored mode.

## 9. Do Not Regress These Boundaries

- Do not replace expected account IDs with an environment label check.
- Do not treat a cancellation request as a terminal cancellation.
- Do not submit a replacement while an earlier order may still fill.
- Do not close a symbol-wide position in the venue proof.
- Do not retry an ambiguous submit under a new client-order ID.
- Do not restore direct JSON safety-state writes or warning-and-continue reads.
- Do not let AI return trade fields or construct a `TradePlan`.
- Do not register equity or crypto strategies on the scored path without a new,
  explicit product decision.
- Do not restore a live-order option to `tools/practice.py`.
- Do not deploy a branch name, default branch, short SHA, or unlocked dependency
  set.
- Do not claim a venue proof, deployment, soak duration, performance, or hosted
  CI run without the corresponding evidence.

## 10. Recommended Next Move

Wait for external inputs. The implementation checkpoint is complete; adding
more strategy surface before proving the venue and deployment path would weaken
the approved proof-first submission. When the user provides development-only
credentials and both expected account IDs, perform Gate A exactly once under
the bounded state machine. When the user provides a VPS and permission to make
the reviewed SHA remotely available, deploy that exact SHA and run the soak.
