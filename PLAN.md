# Current implementation and release plan

This file supersedes the original multi-sleeve proposal. The approved product
is Approach A: a proof-first, options-only scored path with bounded AI.

## Product contract

1. Deterministic SPY and QQQ strategies choose every contract, side, quantity,
   limit, maximum loss, exit, and evidence item.
2. AI may return one supplied candidate ID or `null`. Any unavailable,
   malformed, unknown, non-option, or extended response means abstention.
3. The exact original candidate passes through `RiskKernel.review()` and the
   single hardened execution path.
4. The scored account registers no core equity or crypto strategy. Development
   connectivity remains a separate, explicit, capped CLI proof.
5. Public artifacts claim only Alpaca Trading/Data APIs plus local CLI usage,
   and never claim a completed venue order, deployment, or soak without evidence.

## Completed implementation phases

- Account credentials bind to distinct explicit expected dev/scored IDs.
- Live venue proof has a $50 ceiling, clean-baseline requirement, exact test
  quantity cleanup, residual cancellation, nonzero failures, and flat
  reconciliation.
- Cancel/reprice observes terminal broker state and banks late fills before any
  replacement.
- Single-leg equity/crypto development orders cancel all incomplete residuals.
- Plans and client order IDs are deterministic across restart.
- Submission intent is durable and ambiguous submits reconcile by client ID.
- Safety state uses atomic fsync-and-replace and fails closed on corruption.
- AI is limited to selecting an immutable deterministic option candidate or
  abstaining.
- Scored construction and scheduling are options-only.
- Dependencies are exact-locked, CI runs one verification target, and
  deployment requires a reviewed full SHA.
- README, dashboard, handover, decisions, and social/demo guidance state the
  actual behavior and open external gates.

## Remaining external gates

These are blocked on inputs, not implementation:

- **Dev venue proof:** user supplies dev credentials plus both explicit expected
  account IDs. Run the read-only check, then one user-authorized capped proof.
- **Deployment:** user supplies the VPS/SSH target and the reviewed commit SHA.
  Deploy that detached revision and run `tools/soak.sh`.
- **Scored activation:** only after the venue proof, exact release verification,
  and explicit user direction. Never use the dev proof against the scored ID.

## Release checklist

- `make verify` passes from the exact lock on Python 3.12/Linux.
- `git diff --check` is clean and no credential file is tracked.
- `python main.py --dry-run` returns the expected account number.
- Journal and dashboard show bounded selection, kernel verdicts, broker IDs,
  confirmed cancellations, and any abstention/failure.
- Submission text uses “Alpaca Trading/Data APIs plus CLI” only.
- Live and VPS evidence fields remain “pending” until actually performed.

Detailed architecture: `docs/superpowers/specs/2026-08-29-options-only-proof-first-design.md`.
Detailed build sequence: `docs/superpowers/plans/2026-08-29-options-only-proof-first.md`.
