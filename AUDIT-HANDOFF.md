# Glassbox Review-Branch Audit Handoff

**Prepared:** 2026-08-31 IST
**Main user checkout:** `C:\Users\Utkarsh\Desktop\Project\Trading\alpaca`
**Isolated audit worktree:** `C:\Users\Utkarsh\Desktop\Project\Trading\alpaca-review-audit`
**User checkout branch/HEAD:** `utk` / `54144987b3a2c3265ea4b752f9ec929431750377`
**Teammate branch/HEAD:** `origin/review` / `c45b23fdf6cb51be1092ea2b0c76d1e7f0128c69`
**Recommended continuation base:** `origin/review`
**External actions performed:** none—no order, deployment, merge, default-branch change, or submission.

## 1. Read in this order

1. `docs/reviews/2026-08-31-review-branch-audit.md`
2. `docs/superpowers/plans/2026-08-31-win-readiness-remediation.md`
3. `IMPLEMENTATION-PLAN.md`
4. `handoff.md` (the teammate's implementation handoff)
5. `C:\Users\Utkarsh\Desktop\Project\Trading\REfrences\audit-output\BUILD-THREAD-HANDOFF.md`
6. `C:\Users\Utkarsh\Desktop\Project\Trading\REfrences\audit-output\GLASSBOX-REFERENCE-MASTER-PLAN.md`

The new audit supersedes completion claims in the older handoffs where it cites
counterevidence. Do not redo candidate provenance, bounded selection,
options-only composition, the lifecycle reducer, dashboard foundations, or the
unwired trade stream. Fix the named boundary gaps.

## 2. Current decision

Use `review` as the new base. Do not merge `utk` into it: the branches contain
independent broker/lifecycle implementations. Preserve `utk` as a rollback and
reapply only behavior proven missing by a new regression test.

Do not activate or submit `review` yet. The current provisional event score is
47/100: strong originality and offline engineering, but no P&L or real Alpaca
proof and several release-critical safety defects.

## 3. Exact verification state

- GitHub Actions Python 3.12 run `33317592515` is green for `c45b23f`.
- 555 tests collect.
- Independent checks passed: Ruff format/lint, mypy over 46 sources, pip check,
  33 kernel tests, 13/13 crash drill, 10-variable environment parity,
  compileall, 22 dashboard tests, notices, and offline submission verifier.
- The Windows Python 3.13 single-process full suite hung at 64% amid repeated
  native access-violation diagnostics. Targeted tests still passed. Treat Python
  3.12 CI as authoritative and investigate the host separately before relying
  on it for a release run.
- `tools/verify_submission.py --skip-claims` exits zero with only 2 passes and 7
  missing artifacts. Do not interpret its `VERIFIED` label as release readiness.

## 4. Highest-priority defects

1. **Scored release gate fails open:** `.env.example` defaults it off;
   `assert_scored_startable()` ignores pending gates and verification evidence.
2. **Incomplete-leg unwind is unsafe:** `execute.py` uses symbol-wide close,
   does not reconcile terminal flatness, and the scheduler records only the
   entry fill afterward.
3. **Ledger replay double-counts fills:** same cumulative observation adds
   quantity again.
4. **Exit submit ambiguity is unreconciled:** exact exits bypass the entry
   reconciliation helper.
5. **Singleton is not wired:** `ProcessLock` appears only in tests/docs.
6. **Order absence is not 404-only:** message-only status-less API errors can
   become `OrderNotFound`.
7. **Verifier can pass invented selections:** no-offer and cross-cycle stale-ID
   reproductions both returned `PASS`.
8. **AI-field verifier does not inspect actual model output.**
9. **CLI proof is currently unusable against the pinned official command set.**
10. **No external evidence or P&L exists.**

## 5. Reference status

The references are not “all implemented,” and they should not be. Rejected
features—LLM-authored trades, scored equity/crypto, persona risk theater,
copy-trading, social scope, instant-fill simulation, and unlicensed code—must
remain absent.

The material adopt-now gaps are:

- typed/fail-loud primary option-data evidence;
- runtime singleton enforcement;
- exact request/confirmation semantics for unwind and exit submit;
- cycle-bound evidence verification;
- release-strict manifest/proof validation;
- working pinned official CLI evidence;
- complete redacted release evidence and real forward evaluation;
- hashed supply-chain artifacts/SBOM/security gate.

## 6. Immediate next checkpoint

Start R0/R1 from `origin/review`:

1. create the new implementation branch at `c45b23f`;
2. commit the audit artifacts as documentation only;
3. write failing startup tests proving scored mode currently starts with the
   release gate off and with pending evidence;
4. make scored release gating mandatory and bind it to an explicit reviewed SHA;
5. run focused release/startup/claim tests and commit that boundary alone.

Then follow R2–R7 in order. The unwind and ledger fixes must land before any
credentialed paper mutation.

## 7. External gates still closed

| Gate | Needed |
| --- | --- |
| Read-only Alpaca proof | Dev key/secret, exact dev ID, distinct scored ID, pinned CLI binary/checksum |
| Bounded dev paper order | All read-only proof plus explicit one-order authorization |
| Deployment/soak | VPS/SSH target, secret delivery, reviewed SHA, successful dev proof |
| Scored activation | Fresh $100,000 paper account, options permission, clean evidence, explicit direction |
| Submission | Canonical project identity, real proof/P&L, aligned demo/video/deck/cover, explicit confirmation |

The optional trade stream remains correctly unwired until polling correctness
and a paper soak are proven. Greeks remain supplementary: missing venue Greeks
must be visible evidence, not a primary outage; present unhealthy Greeks may
still veto a candidate.

## 8. Public identity warning

The event directory contains an AEGIS-Q entry with a similar bounded-AI design,
but it links to `VicensPaneque/aegis-q`, says 20 tests, and claims atomic CLI
MLEG execution. If that is this team's entry, it is stale relative to Glassbox
and all public artifacts must be realigned. If it belongs to another team, do
not touch it; confirm/create the canonical Glassbox entry instead.

## 9. Authority boundary

This audit authorizes no order, deployment, push, merge, default-branch change,
or hackathon submission. Continue offline implementation and verification. Stop
at credentials, paper mutation, VPS, or submission until the named inputs and
explicit direction are supplied.
