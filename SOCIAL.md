# Truthful social and demo copy

Publish only after replacing bracketed evidence fields with observed output.
Do not turn a passing fake-broker test into a live-trading claim.

## Short project description

> Glassbox is an options-only Alpaca paper-trading agent. Deterministic code
> creates fully priced SPY/QQQ option candidates; bounded AI can select one or
> abstain, but cannot change the contract, size, side, or limit. Every selection
> still passes a 13-invariant risk kernel and a restart-safe executor.

## Engineering post

> The difficult part of an AI trading agent is not calling a model. It is
> defining what the model is allowed to control.
>
> In Glassbox, the model returns one existing candidate ID or null. Unknown IDs,
> extra fields, bad JSON, timeouts, and missing credentials all abstain. A late
> fill during cancel/reprice is reconciled before any replacement quantity is
> sent. [LINK TO REVIEWED COMMIT]

## Audit post

> Each selected plan records its evidence and kernel verdict. Submission intent
> is durable before the Alpaca API call, and ambiguous responses reconcile by
> deterministic client order ID. The local journal is hash-chained to detect
> edits and carries Alpaca order IDs for broker-side reconciliation. We call it
> reconcilable, not tamper-proof.

## Evidence update templates

Use only after the corresponding gate actually passes:

> Dev venue proof complete on account […masked…]: notional $[…], exact entry
> fill […], exact cleanup […], zero residual orders, flat reconciliation […].
> Journal head: […].

> Reviewed SHA […] deployed to the supplied VPS. Soak duration […], restarts
> […], chain verification […], dashboard health […].

Until then, write:

> Local implementation verification is complete. Dev venue proof and VPS soak
> are pending external credentials/account IDs and a host target; no live paper
> order or deployment is claimed.

## Demo sequence

1. Show the deterministic SPY/QQQ candidates and immutable prices.
2. Show a valid candidate selection returning the exact original object.
3. Show malformed/unknown output abstaining.
4. Show the selected candidate passing or failing named kernel invariants.
5. Show cancel-confirm and accepted-then-timeout reconciliation tests.
6. Run the 13-check crash drill and journal verifier.
7. Open the read-only dashboard and point to the external-gate banner.

Describe the demo as Alpaca Trading/Data API code plus local CLI tooling only.
