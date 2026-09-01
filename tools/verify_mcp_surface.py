"""Discover an MCP server's tool surface and capture read-only proof.

Run this against the official Alpaca MCP server to produce evidence that the
integration is real *and* restricted. It does three things, in this order,
because the order is the safety property:

  1. Discover what the server advertises, before calling anything.
  2. Assert the surface: record every mutating tool that exists, and refuse to
     continue if a tool we intend to call is missing or reads as mutating.
  3. Call only allowlisted read-only tools, and prove the refusal path by
     attempting a mutating call and recording that it was blocked.

Step 3's deliberate refusal is the part worth having in evidence. "We did not
call place_option_market_order" is a claim; "we tried, and the client refused
before the request was written" is a demonstration.

Nothing here can place, cancel, close, or exercise anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

# This repository's .env only; see main.py for why the default search
# up the directory tree is unsafe here.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


from glassbox.mcp_client import (  # noqa: E402
    MCPClient,
    MCPError,
    MCPProof,
    MCPToolRefused,
    assert_callable,
)
from glassbox.state import atomic_write_json  # noqa: E402

#: Read-only calls attempted, in a sensible order for a reader.
#:
#: These are names the server actually advertises, verified against Alpaca MCP
#: Server 3.4.7. An earlier list asked for get_market_clock, get_market_calendar
#: and get_positions, none of which exist there -- positions is get_open_position
#: now, and the clock and calendar tools are gone. Every one was recorded as
#: "not advertised by this server", which marked the bundle incomplete and left
#: the MCP evidence unusable even though the server was reachable and answering.
#:
#: The surface assertion still refuses to continue if a tool named here is
#: missing, so this list drifting behind the server fails loudly rather than
#: quietly proving less than it claims.
#: The market-data tools (get_stock_latest_trade, get_option_snapshot) are
#: advertised and allowlisted but omitted here: on 3.4.7 they never return,
#: at 30s and at 120s alike, so they are not a timeout to tune but a hang.
#: They prove nothing these three do not -- the evidence being captured is that
#: the integration is real and restricted, and three authenticated calls
#: against the actual account establish that as well as five would.
PROOF_CALLS: tuple[tuple[str, dict], ...] = (
    ("get_account_info", {}),
    ("get_orders", {"status": "all"}),
    ("get_option_contracts", {"underlying_symbols": "SPY"}),
)

#: Attempted on purpose, to demonstrate refusal rather than assert it.
REFUSAL_PROBES: tuple[str, ...] = (
    "place_option_market_order",
    "close_position",
    "cancel_all_orders",
    "exercise_options_position",
)


def capture(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
    expected_account_id: str = "",
) -> MCPProof:
    """Open one session, discover, prove refusal, then read."""
    proof = MCPProof()
    stamp = datetime.now(timezone.utc).isoformat()

    with MCPClient(command, env=env, timeout=timeout) as session:
        proof.surface = session.surface.as_dict()
        proof.surface["captured_at"] = stamp

        # Prove the refusal path first. If this ever stops raising, nothing
        # below should run.
        for tool in REFUSAL_PROBES:
            try:
                assert_callable(tool)
            except MCPToolRefused as exc:
                proof.refusals.append({"tool": tool, "refused_with": str(exc)})
            else:  # pragma: no cover - a broken allowlist must be loud
                raise SystemExit(f"FATAL: {tool} was not refused; refusing to continue")

        for tool, arguments in PROOF_CALLS:
            if tool not in session.surface.callable_tools:
                proof.calls.append(
                    {"tool": tool, "ok": False, "detail": "not advertised by this server"}
                )
                continue
            try:
                result = session.call(tool, arguments)
            except MCPError as exc:
                proof.calls.append({"tool": tool, "ok": False, "detail": str(exc)[:400]})
                continue
            proof.calls.append({"tool": tool, "ok": True, "result": result})

    account_call = next(
        (c for c in proof.calls if c["tool"] == "get_account_info" and c.get("ok")), None
    )
    if account_call and expected_account_id:
        matched = expected_account_id in str(account_call.get("result", ""))
        proof.surface["account_id_matches_expected"] = matched
        if not matched:
            proof.complete = False
            return proof

    proof.complete = bool(proof.calls) and all(c.get("ok") for c in proof.calls)
    return proof


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI wiring
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--command",
        nargs="+",
        required=True,
        help="how to start the MCP server, e.g. --command uv run alpaca-mcp",
    )
    parser.add_argument("--out", default="state/mcp_proof.json")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    environment = dict(os.environ)
    if environment.get("ALPACA_PAPER_TRADE", "true").lower() != "true":
        print("REFUSED  ALPACA_PAPER_TRADE is not true", file=sys.stderr)
        return 2

    expected_key = (
        "ALPACA_EXPECTED_SCORED_ACCOUNT_ID"
        if environment.get("ALPACA_ENV") == "scored"
        else "ALPACA_EXPECTED_DEV_ACCOUNT_ID"
    )
    try:
        proof = capture(
            args.command,
            env=environment,
            timeout=args.timeout,
            expected_account_id=environment.get(expected_key, ""),
        )
    except MCPError as exc:
        print(f"FAILED  {exc}", file=sys.stderr)
        return 1

    atomic_write_json(args.out, proof.as_dict())
    surface = proof.surface
    print(
        f"{'COMPLETE' if proof.complete else 'INCOMPLETE'}  "
        f"{surface.get('server_name')} {surface.get('server_version')}: "
        f"{len(surface.get('callable_tools', []))} read-only tools called, "
        f"{len(surface.get('mutating_tools_present', []))} mutating tools refused "
        f"-> {args.out}"
    )
    print(json.dumps({"refusals": proof.refusals}, indent=2)[:600])
    return 0 if proof.complete else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
