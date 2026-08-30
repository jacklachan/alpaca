# Glassbox `review` Branch — Independent Readiness Audit

**Audit date:** 2026-08-31 IST
**Repository:** `https://github.com/jacklachan/alpaca.git`
**Local build reviewed:** `utk` at `54144987b3a2c3265ea4b752f9ec929431750377`
**Teammate build reviewed:** `origin/review` at `c45b23fdf6cb51be1092ea2b0c76d1e7f0128c69`
**Shared ancestor:** `eea74d0817ab201deb575db413f3ef533cf7f347`
**Audit worktree:** `C:\Users\Utkarsh\Desktop\Project\Trading\alpaca-review-audit`
**Decision:** use `origin/review` as the new base, but do not activate, deploy, or submit it yet.

## 1. Executive verdict

The teammate branch is a substantial improvement over the local `utk` branch. It
adds 28 commits after the common ancestor, 8,615 insertions across 56 files, a
position ledger, release metadata, performance reporting, a read-only MCP
client, CLI proof capture, option-surface checks, a richer dashboard, and 555
collected tests. Its current Python 3.12 GitHub Actions run is green.

It is nevertheless **not ready for scored trading or submission**. Four release
blockers dominate the result:

1. A scored process can start with the release gate disabled, and the gate does
   not enforce the pending venue, CLI, or soak evidence even when enabled.
2. An incomplete two-leg entry still uses symbol-wide liquidation, reports flat
   after request acceptance, and then records the entry fill without recording
   the unwind. That can either liquidate unrelated holdings or poison the
   strategy ledger.
3. The claimed third-party verifier returns exit code zero and prints
   `VERIFIED` with seven important artifacts absent; its central bounded-AI
   checks have demonstrable cross-cycle and no-offer bypasses.
4. No real Alpaca CLI/MCP proof, dev venue proof, scored-account evidence,
   deployment/soak evidence, or P&L exists. The CLI capture command table also
   disagrees with the pinned official CLI reference, so it cannot currently
   produce the required complete proof bundle.

**Current win-readiness score: 47/100.** This is a strong engineering prototype
with a distinctive safety story, but a weak submission today because the
event-relevant evidence and P&L are absent and several safety claims are
stronger than the implementation.

## 2. Branch comparison

### 2.1 Exact divergence

`utk` and `review` diverged after `eea74d0`:

| Side | Unique commits | Meaning |
| --- | ---: | --- |
| `utk` | 3 | Typed outcome work, lifecycle reducer, and an older handoff |
| `origin/review` | 28 | Independent equivalents of those two code changes plus ledger, release, proof, performance, MCP, Greeks, dashboard, CI, and operations work |

`git cherry origin/review utk` marks all three `utk` commits as patch-distinct,
but `git range-diff` shows that `review` independently implemented and extended
the same typed-broker and lifecycle areas. A normal merge would mix competing
implementations and handoff histories. The safe continuation is a new branch
from `origin/review`, followed by targeted reapplication of any genuinely
missing behavior as tests—not a merge of `utk`.

### 2.2 What `review` adds

The most material additions are:

- per-contract position ledger and exact-exit path;
- typed broker outcomes and a monotonic order-state reducer;
- runtime release manifest and options-only scored composition;
- read-only Alpaca CLI evidence capture and a JSON-RPC MCP client;
- candidate-set manifests, selection receipts, and counterfactual evidence;
- total-account-equity performance metrics;
- supplementary Greeks/IV checks;
- optional trade-update stream, intentionally not wired before soak;
- pinned GitHub Actions, dependency notices, richer CI, dashboard, runbook,
  and one-page write-up;
- 555 collected tests versus the much smaller earlier suite.

### 2.3 Remote visibility

The repository's default branch is still `main` at `363808c`; `review` is at
`c45b23f` and `utk` is at `5414498`. Anyone opening the repository root sees
the older `main` build unless the submitted URL explicitly selects `review`.
Do not change the default branch or merge until the safety blockers below are
closed, but treat canonical-repository alignment as a release checkpoint.

## 3. Verification evidence

### 3.1 Authoritative target-runtime result

GitHub Actions run `33317592515` for `c45b23f` completed successfully on the
declared Python 3.12 target:

- `Competition guards`: success;
- `Tests and drills`: success.

Run URL: `https://github.com/jacklachan/alpaca/actions/runs/33317592515`

### 3.2 Independent Windows result

A fresh Python 3.13 virtual environment was installed from
`requirements-dev.lock`. The following passed independently:

- Ruff formatting: 87 files formatted;
- Ruff lint;
- mypy: 46 source files;
- dependency integrity;
- 33 kernel tests;
- crash drill: 13/13;
- environment parity: all 10 variables;
- compileall;
- 22 dashboard tests;
- generated notices freshness;
- submission verifier execution.

The single-process full `pytest -q` run made no progress after 64% and was
interrupted. Reproduction showed repeated Windows native access-violation
diagnostics in unrelated standard-library reads and imports as well as the MCP
pipe reader; targeted test files still completed with passing assertions. This
matches the teammate handoff's host-runtime warning. It is an unresolved local
runtime reliability signal, not evidence of a Python assertion failure. The
clean Python 3.12 CI run is therefore the target-runtime authority.

### 3.3 What the green gate does not prove

`tools/verify_submission.py --skip-claims` currently reports:

```text
VERIFIED  2 passed, 0 failed, 7 not yet applicable
```

The seven skips are the journal, bounded-AI history, AI-field history, release
manifest, position ledger, CLI proof, and MCP proof. `VerificationReport.ok`
means only “no FAIL,” so the command exits zero despite the absence of nearly
all release evidence. The green CI gate proves internal consistency of the
offline build; it does not prove event or scored-account readiness.

## 4. Findings in risk order

### P0 — Scored release activation fails open

**Evidence:** `.env.example:36`, `main.py:189-200`,
`glassbox/release.py:135-157`, `glassbox/release.py:236-268`.

- `GLASSBOX_RELEASE_GATE` defaults to `0`.
- A scored process checks the release only when that optional flag is exactly
  `1`.
- The runtime builds a new manifest from its own current state rather than
  comparing against an approved reviewed-release attestation.
- `main.release_manifest()` always records three pending gates, but
  `assert_scored_startable()` ignores `pending_gates` and `verification`.

**Impact:** a clean scored checkout with the right environment and account can
start before the development proof, CLI proof, or deployment soak exists. This
contradicts the product contract and the README's release-gate claim.

**Required change:** scored mode must always require an explicit reviewed SHA
and a validated release-evidence manifest. Any required pending or skipped gate
must refuse startup.

### P0 — Incomplete option-leg unwind is unsafe and can corrupt ownership

**Evidence:** `glassbox/execute.py:325-379`,
`glassbox/scheduler.py:427-469`.

After a partially completed two-leg entry, the executor:

- calls `broker.close_position(symbol)` for each filled leg;
- closes symbol-wide rather than the exact test-created quantity;
- creates no deterministic unwind client-order ID or durable unwind intent;
- does not reconcile an ambiguous close response;
- does not wait for a terminal unwind order and exact venue-zero quantity;
- returns `"position unwound to flat"` when the close request did not raise.

The scheduler then records every entry fill even when execution returned
`ok=False`. It does not record the unwind fill. Consequently:

- a successful symbol-wide close can remove unrelated holdings and leave the
  ledger expecting a position that no longer exists; or
- an accepted but incomplete close can leave unknown exposure while the result
  claims flat.

**Required change:** route unwind through the same exact-quantity,
intent-first, deterministic-ID, submit-reconciliation, terminal-order, and
venue-reconciliation machinery as ordinary exits. Apply entry and unwind
observations to the ledger atomically and idempotently.

### P0 — Event evidence and P&L do not exist

**Evidence:** repository state and the teammate handoff.

The audit worktree has no `.env`, release manifest, ledger, CLI proof, or MCP
proof. No credentials or VPS target were supplied. No paper order, scored
account activation, deployment, soak, or measured P&L can be verified.

The event page requires an Alpaca-based trading agent and advertises Trading
API, MCP, and CLI usage. The local event audit further records a dedicated
$100,000 paper account, options in every strategy, Trading API plus CLI or MCP,
account ID, write-up, public repository, demo, video, and deck as submission
requirements.

**Impact:** submitted now, the project would have almost no evidence for the
P&L dimension and incomplete proof for the technology dimension.

### P1 — Exit submission ambiguity is not reconciled

**Evidence:** `glassbox/manage.py:408-487`.

The exact-exit path durably registers its ID, then calls `broker.submit()`
directly. If Alpaca accepts the exit but the response times out, the generic
exception handler discards the in-memory sent guard and does not look up the
original deterministic ID. Entry submission already has a reconciliation
helper; exit submission does not use it.

**Required change:** one shared submission primitive must own intent,
single-attempt mutation, lookup-by-client-ID, bounded ambiguity handling, and
adoption for entries, exits, replacements, and unwinds.

### P1 — Position fill accounting is non-idempotent

**Evidence:** `glassbox/position_ledger.py:139-192`.

Both `record_entry_fill` and `record_exit_fill` add the supplied cumulative
fill on every call. The client-order-ID tuples are deduplicated, but the
quantity is not. Replaying the same confirmed observation twice doubles
strategy ownership.

Independent reproduction:

```json
{"duplicate_cumulative_fill":"2","duplicate_signed_qty":"2"}
```

after applying the same one-contract fill twice under the same client order ID.

**Required change:** persist the last cumulative fill per client order ID and
apply only a non-negative delta. Duplicate and out-of-order observations must
be no-ops; decreasing or overfilled observations must fail closed.

### P1 — The singleton lock is test-only

**Evidence:** `glassbox/state.py:92-167` and repository-wide `ProcessLock`
references.

`ProcessLock` is implemented and tested, but only tests and documentation refer
to it. `main.py` and the scheduler never acquire it.

**Impact:** two scored processes can own the same state directory and execute
independent loops against one account despite the README saying this is
prevented.

### P1 — The “verified 404 only” claim is false

**Evidence:** `glassbox/broker.py:189-218`.

The classifier returns `OrderNotFound` for a status-less class named
`APIError` whose message contains a not-found phrase. A verified HTTP 404 is
therefore not the only absence path.

**Impact:** a changed SDK error message or wrapped ambiguous response can be
misread as proof that an order does not exist.

### P1 — The bounded-AI verifier has no cycle binding and a no-offer bypass

**Evidence:** `glassbox/verification.py:107-147`.

The check accumulates every offered candidate ID across the entire journal.
It does not match a selection to one run, candidate-set hash, or selector
receipt. It also checks membership only when the global offered set is
non-empty.

Independent reproductions both return `PASS`:

- a `CANDIDATE_SELECTED` event with no preceding candidate set;
- a later cycle selecting an ID offered only in an older cycle.

**Required change:** every event needs a run/cycle ID and candidate-set digest;
the selected receipt must resolve to exactly one preceding set in that cycle.
Missing, duplicated, reordered, or mismatched evidence must fail.

### P1 — The AI-field check does not inspect real model output

**Evidence:** `glassbox/thesis.py:100-188`,
`glassbox/verification.py:150-179`.

Selection events store a receipt and hashes, not a `model_output` object. The
verifier increments its checked count for these events, sees `model_output` is
absent, and passes. Even if present, the forbidden list omits fields such as
`max_loss`, `stop`, `target`, `option_legs`, and `contract`.

**Required change:** store a redacted canonical response envelope or prove the
response against the receipt hash, then validate the exact ID-or-null schema.

### P1 — Submission verification treats missing evidence as success

**Evidence:** `glassbox/verification.py:31-83`,
`tools/verify_submission.py:95-118`.

`SKIP` is considered okay, report success means only zero failures, and the
human verdict says `VERIFIED`. CI therefore stays green with all broker-facing
evidence absent.

**Required change:** separate an offline/development audit from a release audit.
Release mode must define required checks and return nonzero on any required
`SKIP`.

### P1 — CLI proof capture disagrees with the pinned official CLI

**Evidence:** `tools/capture_alpaca_proof.py:41-50`,
`tools/capture_alpaca_proof.py:165-227`, and
`REfrences/audit-output/OFFICIAL-ALPACA-REPOS.md:79-103`.

The current table uses:

- `--version` rather than the recorded `version` command;
- `clock get` rather than `clock`;
- `config get` rather than `account-config get`;
- `option contracts list` rather than `option contracts` with filters;
- no `doctor` command;
- no option-chain command;
- no `--quiet` on structured commands.

It also requires JSON from every successful step. The official `version` and
`doctor` outputs are operational plain text, so a normal version response is
marked incomplete. The CLI binary/release/checksum is not pinned or verified.

**Required change:** generate commands from the installed pinned CLI's help and
schema, permit documented plain text only for operational steps, require exact
structured account equality, capture option contracts and chain, and bind the
artifact to CLI version and checksum.

### P1 — MCP proof identity and completeness are weak

**Evidence:** `tools/verify_mcp_surface.py:59-108` and
`tools/verify_mcp_surface.py:123-154`.

- The expected account ID defaults to empty and is checked only when present.
- Account matching is substring search over a rendered result rather than
  structured equality.
- Any `ALPACA_ENV` value other than exact `scored` selects the dev variable.
- The official server version/SHA has not been run or captured.

Because CLI is the lower-risk required integration, MCP should remain optional
until these checks are corrected and a real official-server transcript exists.

### P1 — Release and proof artifacts are self-descriptions, not attestations

**Evidence:** `glassbox/release.py:135-180`,
`glassbox/release.py:236-268`, `glassbox/verification.py:270-281`.

The release manifest hashes itself but is generated from the same checkout it
is asked to approve. It has no required approved SHA, no external anchor, and
does not require verification evidence. Proof verification trusts a top-level
`"complete": true` without validating schema, commands, account equality,
freshness, hashes, or tool version.

### P2 — The strangle breakeven gate understates the move

**Evidence:** `glassbox/greeks.py:181-193`.

The function computes premium-per-share divided by spot and omits the distance
from spot to each out-of-the-money strike. The docstring says this overstates
the required move; for an OTM strangle it understates it. A candidate can pass
the cap even when its true call- or put-side breakeven is farther away.

**Required change:** accept the call and put strikes and calculate both actual
breakevens, with explicit tests around spot, strike gap, premium, and contract
multiplier.

### P2 — Greeks behavior is documented inconsistently

**Evidence:** `README.md:104-110`,
`glassbox/strategies/event_vol.py:316-348`, `handoff.md:160-164`.

The approved behavior is defensible: Greeks are supplementary; a missing
surface is recorded and the candidate proceeds through primary hard gates.
The code and teammate handoff do that. The README instead says missing Greeks
cause abstention. Correct the public claim; do not make an optional Alpaca
surface a permanent outage. Also classify “venue omitted Greeks” separately
from authentication, network, schema, and code failures rather than catching
all exceptions as equivalent unavailability.

### P2 — Primary market-data failures are silent abstentions

**Evidence:** `glassbox/strategies/event_vol.py:379-419`.

Expiry-quote and chain exceptions are swallowed and converted to empty
candidate lists without a typed journal event. This misses the reference
requirement for fail-loud, provenance-rich no-data behavior and makes “no
opportunity” indistinguishable from “the data path failed.”

### P2 — Executable Decimal values are converted to float at the SDK boundary

**Evidence:** `glassbox/broker.py:625-665`.

Quantity and limit price are converted to binary floats before request
construction. This violates the reference backlog's exact Decimal boundary and
can alter executable values. Use the exact input type accepted by the pinned
SDK and test serialized order payloads at tick and fractional-quantity edges.

### P2 — MCP subprocess transport is not production-hardened

**Evidence:** `glassbox/mcp_client.py:225-313`,
`glassbox/mcp_client.py:386-406`.

Each response starts a new daemon reader thread. A timeout leaves that thread
blocked on stdout while a later request can create another reader. Stderr is
piped but never drained, so a verbose server can block. The Windows audit also
observed native access-violation diagnostics while the reader thread was active,
although the targeted tests completed.

### P2 — Source-quality gates are broad but shallow

Twenty-three of 46 production/tool Python files exceed 250 lines; the largest
are 786, 697, 594, 593, and 512 lines. Ruff selects only basic E/F/I rules,
mypy is not strict, and there is no coverage threshold, property-test framework,
mutation gate, security scan, dependency vulnerability scan, or SBOM. Lock
files pin versions but not artifact hashes.

This is not a reason for a deadline-week rewrite. Add only high-value gates and
split files when touching the affected safety boundaries.

## 5. Reference implementation trace

The goal is not to implement every reference feature. Several reference ideas
were explicitly rejected because they conflict with Approach A or have
licensing risk. The correct question is whether every **adopt-now** pattern is
present and truthful.

| Reference | Adopt-now pattern | Status on `review` | Evidence or gap |
| --- | --- | --- | --- |
| TradingAgents | Canonical identity and explicit provenance | Implemented | Candidate manifests, quote snapshots, stable IDs |
| TradingAgents | Explicit vendor route; no silent fallback | Missing | No configured vendor chain; primary data exceptions silently abstain |
| TradingAgents | Typed no-data/stale failures | Partial | Broker outcomes typed; event-vol quote/chain catches are broad and silent |
| TradingAgents | Structured evidence/report tree | Partial | Dashboard and journal improved; verifier is not cycle-bound |
| TradingAgents | Research checkpoint signature | Implemented | Candidate-set and selector receipt hashes |
| TradingAgents | Rich proof-oriented UI | Partial | Counterfactual and verification panels exist; real venue lifecycle evidence absent |
| TradingAgents | Pinned supply chain/SBOM/security | Partial | Exact versions, notices, pinned Actions; no hashes, SBOM, or vulnerability gate |
| AI Hedge Fund v2 | Point-in-time snapshot and content hash | Implemented | Candidate snapshots/manifests and freshness validation |
| AI Hedge Fund v2 | Explicit abstention | Implemented | Model errors, malformed output, unknown ID abstain |
| AI Hedge Fund v2 | Stage-rich cycle record | Partial | Many events exist; run/cycle binding and semantic verification are missing |
| AI Hedge Fund v2 | Protocols, fakes, crash cases | Implemented strongly | Extensive broker/lifecycle/crash tests |
| AI Hedge Fund v2 | Release-scoped mandate | Partial | Options-only composition exists; release gate defaults off and ignores pending evidence |
| AI Hedge Fund v2 | PEAD feature | Optional/not implemented | Correctly remains offline “could,” not a release blocker |
| AI-Trader | Dry-run and exact preflight counts | Partial | Dry run and reconciliation exist; release proof does not require them |
| AI-Trader | Request versus confirmation semantics | Partial | Entry cancel confirmation good; unwind and exit-submit paths break the rule |
| AI-Trader | Server-authoritative prices | Partial | Quote-derived Decimal candidates; float conversion at broker boundary |
| AI-Trader | Scored/dev isolation | Implemented | Scored composition options-only; crypto remains dev-only |
| AI-Trader | Bounded read retries | Implemented at broker boundary | Primary strategy errors still lose typed evidence |
| AI-Trader | Singleton scheduler | Missing in runtime | `ProcessLock` is never acquired outside tests |
| AI-Trader | Redacted evidence export | Partial | CLI/MCP bundles exist; no complete release evidence bundle exists |
| Live Trade Bench | Model input/output decision provenance | Partial | Hash receipts exist; raw canonical bounded response is not verifiable |
| Live Trade Bench | Forward-only evaluation | Missing external evidence | No scored run or P&L |
| Live Trade Bench | Read-only evidence dashboard | Implemented offline | No real broker evidence or deployment |
| Live Trade Bench | Freshness and next-run visibility | Partial | Candidate freshness exists; lifecycle and external-anchor freshness incomplete |
| Live Trade Bench | No-trade/underlying benchmark | Optional/not implemented | Correctly a “could”; total-equity performance module exists |
| Official Alpaca repos | `alpaca-py` Trading/Data API | Implemented, unproven live | Correct primary adapter; no credentialed transcript |
| Official Alpaca repos | CLI proof | Broken and unrun | Command/schema mismatch and no pinned binary evidence |
| Official Alpaca repos | MCP read-only proof | Built, unrun, weak identity | Optional; do not claim official integration yet |
| Official Alpaca repos | Deterministic ID and ambiguity lookup | Partial | Entry path good; exit and unwind paths incomplete |
| Official Alpaca repos | Terminal cancel and exact position truth | Partial | Working-order reducer good; unwind violates exact ownership |

### Correctly rejected reference features

These should remain unimplemented:

- LLM-authored symbols, contracts, weights, sizes, prices, stops, or risk;
- equity, crypto, prediction-market, or copy-trading sleeves on the scored account;
- persona debate presented as a risk control;
- immediate-fill simulators presented as broker execution;
- free-text selection fallbacks;
- social/marketplace/leaderboard product scope;
- source, UI, or prompts from unlicensed/proprietary/noncommercial references.

## 6. Provisional event score

The public page exposes the event, track, dates, and tool focus but not a
machine-readable weighting table. This audit therefore uses equal 25-point
weights for the four judging dimensions recorded in the event-reference audit.

| Dimension | Score | Assessment |
| --- | ---: | --- |
| P&L performance | **1/25** | No scored account evidence, orders, equity curve, or measured return exists. |
| Technology implementation | **16/25** | Excellent offline test depth and original safety architecture; release, unwind, ledger, proof, and CLI defects remain. |
| Creativity/originality | **21/25** | The immutable candidate → bounded AI ID/null → deterministic kernel → reconciled execution story is distinctive and judge-friendly. |
| Presentation/execution | **9/25** | Strong local write-up/dashboard, but no deployed current demo, real proof transcript, video/deck alignment, canonical branch, or P&L story. |
| **Total** | **47/100** | **Do not submit this state as final.** |

### Can it win?

**Submitted now: no.** A technically sophisticated repository cannot compensate
for zero P&L evidence, missing required Alpaca proof, an unactivated release,
and public artifacts that are not demonstrably tied to the audited commit.

**After the remediation plan: potentially.** The core narrative is stronger
than many generic “multi-agent trader” entries because it makes AI authority
small, deterministic, and auditable. To convert that into a winning entry, the
team must close the P0/P1 defects, capture real CLI and paper-account evidence,
run the strategy early enough to produce total-equity results, and present one
canonical live proof chain rather than a collection of local claims.

## 7. Public submission identity check

The event directory currently contains an AEGIS-Q entry with a very similar
bounded-AI options description. It links to `VicensPaneque/aegis-q`, says “20
passing automated tests,” and describes one atomic `mleg` CLI order. The audited
Glassbox branch has 555 tests and intentionally submits two independent legs
through `alpaca-py`.

This audit cannot prove that AEGIS-Q is this team's entry. **If it is**, its
GitHub link, name, architecture description, test count, demo, deck, and order
semantics are all stale and must be replaced together. **If it is not**, make
no change to it and create/confirm the canonical Glassbox submission identity.

## 8. Go/no-go decision

| Action | Decision |
| --- | --- |
| Use `origin/review` as the next code base | **GO** |
| Merge `utk` into `review` | **NO-GO**; reintroduce only missing behavior as tests |
| Start scored process | **NO-GO** until R1–R3 and external account proof |
| Place development proof order | **GATED** by credentials, both account IDs, clean baseline, and explicit authorization |
| Deploy | **GATED** by reviewed SHA, VPS target, secret delivery, and successful dev proof |
| Submit final event entry | **NO-GO** until real Alpaca proof, P&L/equity evidence, and canonical demo package exist |
| Wire trade stream now | **DEFER** until polling path and paper soak are clean |

The implementation-ready sequence is in
`docs/superpowers/plans/2026-08-31-win-readiness-remediation.md`.
