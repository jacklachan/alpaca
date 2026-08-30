# Operations

Everything below is read-only or local unless the heading says otherwise.
Nothing here places an order.

## Daily verification

```bash
make verify                      # format, lint, types, tests, drills, claims
python tools/verify_submission.py  # the checks a judge would run
```

`verify_submission.py` reads local artifacts only. A `SKIP` means the evidence
does not exist yet; it is never a waived check. A `FAIL` means something we
recorded contradicts something we claim, and it should be treated as a stop.

## Starting the agent

```bash
python main.py --dry-run   # wire up, prove account identity, print the schedule
python main.py --once      # one tick, then exit
python main.py             # run the schedule
```

`--dry-run` contacts Alpaca and asserts account identity but never starts the
clock. Normal and `--once` modes are order-capable.

Scored mode always requires `GLASSBOX_RELEASE_GATE=1`; an absent, false, or
mistyped value refuses startup before broker construction. Also set
`GLASSBOX_APPROVED_COMMIT_SHA` to the externally reviewed full commit SHA and
`GLASSBOX_RELEASE_MANIFEST_PATH` to its evidence-complete manifest. The process
refuses a dirty or drifted checkout, a non-exact paper endpoint, pending gates,
stale evidence, non-PASS mandatory checks, invalid proof hashes, wrong account,
or mismatched commit/lock/policy/candidate identity. Development dry runs do not
gain scored authority and remain usable without these scored-release inputs.
Expect:

```
release gate: commit f8821cf17009, options-only, paper
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | normal |
| 1 | startup failed |
| 2 | not paper trading — refused to boot |
| 3 | journal chain broken |
| 4 | cannot reach Alpaca (transient; the service will retry) |
| 5 | account check failed (not transient; a human must look) |
| 6 | release gate refused the start |

## Capturing integration evidence

Both are read-only and refuse mutating operations before a process starts.

```bash
python tools/capture_alpaca_proof.py --out state/cli_proof.json
python tools/verify_mcp_surface.py --command <how-to-start-the-mcp-server>
```

The MCP tool deliberately attempts `place_option_market_order`,
`close_position`, `cancel_all_orders` and `exercise_options_position` and
records that each was refused. That refusal record is the evidence; asserting
"we did not call them" would not be.

## When something latches

The agent fails closed rather than guessing. Each latch has a distinct cause
and a distinct resolution.

| Symptom in the journal | Cause | What to do |
| --- | --- | --- |
| `POSITION_RECONCILE_FAULT` | expected position does not match the venue exactly | Reconcile by hand. Do not clear the ledger; find the missing fill. |
| `EXIT_STATE_UNCERTAIN` | an exit order's terminal state could not be proven | Look the order up by its client id at the venue, settle it, then clear that symbol from `state/exit_state.json`. |
| `EXIT_REFUSED_UNOWNED` | a position exists that this strategy never recorded | Expected if the account is shared. The agent will not touch it. |
| `ORDER_SUBMIT_AMBIGUOUS` | a submit timed out and lookup could not resolve it | Look up the deterministic client id. Never resubmit. |
| `STATE_FAULT_LATCHED` | safety state failed to parse or validate | Stop. Restore state and binary together; never downgrade state in place. |
| kill switch tripped | drawdown limit hit | Human decision only. It does not re-arm itself. |

## Rotating or resetting state

State lives outside the repository in production. Never edit a state file to
make an error go away: the files are checksummed, and a corrupt one refuses to
load precisely so that a bad edit cannot become a silent wrong answer.

To roll back, stop the service and restore the previous approved commit *and*
its compatible state together.
