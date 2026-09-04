# Decision record

## D1 — Proof-first options-only scored path

**Decision:** Approach A is approved. The scored account runs only deterministic
SPY/QQQ option candidate generators. Core equity and crypto sleeves are not
registered on that account.

**Why:** Options are the product being judged, and a composite account would
make attribution and AI authority harder to prove. Crypto remains useful only
as a separate dev connectivity check.

## D2 — AI selects; deterministic code authors

**Decision:** `ThesisLayer.select()` can select one existing candidate ID or
abstain. It never constructs a `TradePlan`.

**Why:** Contracts, quantities, limits, maximum loss, and exits are safety
properties. Giving those fields to a generative model would make the risk story
depend on post-hoc validation. Returning the exact original object gives the
model useful ranking authority without order-authoring authority.

Unknown IDs, extra fields, bad JSON, missing credentials, and timeouts all
abstain. The selected candidate still goes through all kernel invariants.

## D3 — Account role is proved by returned identity

**Decision:** `ALPACA_ENV` chooses policy but is never identity evidence.
Credentials must resolve to the explicit expected dev or scored account number,
and those configured IDs must differ.

## D4 — A cancel request is not a cancellation

**Decision:** Reprice and cleanup wait for terminal broker state. Late fills are
banked before remaining quantity is recomputed. Uncertain state stops execution
and requires intervention.

## D5 — Ambiguous submission is reconciled, not retried

**Decision:** Write submission intent before the API call, use stable plan/client
IDs, and look up the same client ID after an exception. Adopt one observed order
or report ambiguity; never send a guessed duplicate.

## D6 — Safety state fails closed

**Decision:** Kill-switch, exit-target, and positioned-event JSON use same-folder
temporary files, flush, fsync, atomic replace, and schema validation. Missing
state may use an explicit default; unreadable or invalid state does not.

## D7 — The live proof is bounded and reversible

**Decision:** Only `tools/live_check.py --trade` may serve as the dev venue
proof. It refuses scored mode, requires a clean account, caps notional at $50,
cancels residual GTC quantity, sells only the quantity it created, and succeeds
only after exact flat reconciliation.

The older practice rehearsal is not evidence of scored execution.

## D8 — Releases are immutable

**Decision:** Deploy only a full 40-character reviewed commit SHA and install
`requirements.lock`. Moving default branches and open-ended dependency ranges
are not deployment inputs.

## D9 — Audit claims stay narrow

**Decision:** The hash chain detects changes to the recorded local history. It
does not prove the whole history was never regenerated. Broker order IDs and
timestamps support reconciliation against Alpaca records.

## D10 — Integration claim

**Decision:** Describe the implementation as Alpaca Trading/Data APIs plus local
CLI operations. No additional broker integration is claimed.

## D11 — A closed venue is a deferral, not an exit failure

**Observed live, 4 Sep 2026.** `time_exit` for the convex sleeve is
`MEASUREMENT_ET` — 16:00 ET — which is the exact minute Alpaca stops accepting
options market orders. The exit fired correctly and on time and could not fill.
Every subsequent tick filed it as `EXIT_FAILED`: 45 identical error lines and 45
journal entries for one deferred order, until the tick window closed at 17:00.

**Decision:** keep retrying, because the condition genuinely clears when the
venue reopens, but journal it once as `EXIT_DEFERRED_VENUE_CLOSED` rather than
as a repeated failure. Detection matches Alpaca's error code first and its prose
second, so a reworded message does not silently turn a deferral back into a
stream of failures.

**Not changed:** the exit still fires at the measurement instant. Moving it
earlier would flatten the position *before* the account is valued, which is a
different strategy, not a bug fix. Holding convexity into the snapshot is the
design.

**Not active during the scored run.** The fix is in the tree; the measured day
ran on the pinned commit without it. The 45 `EXIT_FAILED` entries are in the
journal and stay there — the record of a run is not edited because the code
later improved.

## Current evidence boundary

Local tests, type/lint/format gates, crash drills, deploy harness tests and
dashboard response checks are implemented.

Since this section was first written, the external gates it described as open
have been closed and the evidence captured against scored account
`PA3XT8QFJZAQ`: live options orders with Alpaca's own order IDs, a read-only
CLI proof bundle, a run against the official Alpaca MCP Server 3.4.7 with four
mutating tools refused by attempting them, and a dev venue proof on a separate
account.

**Still open:** no VPS soak. No host target was provisioned, so the agent ran
on a laptop under a restart watchdog and no `deployment_soak` evidence exists.

This paragraph went stale for two days while the tests that were supposed to
police our claims held it in place -- they pinned the literal sentence rather
than the property, so they enforced the caveat after it became false. The
guards now check that no public document calls a proof pending when the bundle
is on disk. Understating the work is a claim error like any other.
