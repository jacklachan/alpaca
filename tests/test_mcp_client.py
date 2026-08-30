"""The MCP client, exercised across a real pipe to a real subprocess.

tests/mcp_fake_server.py speaks actual JSON-RPC 2.0 over stdio and advertises
the same dangerous tools the official Alpaca server does -- place, cancel,
close, exercise, account mutation. That is the point: the property worth
testing is that the client refuses to call them while they sit there, callable,
one string away.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from glassbox import mcp_client as mcp
from glassbox.mcp_client import MCPClient, MCPError, MCPToolRefused

SERVER = Path(__file__).resolve().parent / "mcp_fake_server.py"

# Assembled at runtime so no key-shaped literal is committed; the joined
# value is still key-shaped, so it exercises redaction.
FAKE_ANTHROPIC_KEY = "sk-ant-" + "abcdefghijklmnopqrst"


def client(mode: str = "normal", **kw) -> MCPClient:
    env = dict(os.environ)
    env["GLASSBOX_FAKE_MCP_MODE"] = mode
    return MCPClient([sys.executable, str(SERVER)], env=env, **kw)


# -- refusal, before anything leaves the process -------------------------------


@pytest.mark.parametrize(
    "tool",
    [
        "place_option_market_order",
        "cancel_all_orders",
        "close_position",
        "exercise_options_position",
        "update_account_configuration",
        "close_all_positions",
        "submit_order",
    ],
)
def test_every_mutating_tool_is_refused(tool: str):
    with pytest.raises(MCPToolRefused, match="mutating verb"):
        mcp.assert_callable(tool)


def test_an_unknown_read_tool_is_refused_by_the_allowlist():
    with pytest.raises(MCPToolRefused, match="allowlist"):
        mcp.assert_callable("get_something_undocumented")


def test_the_verb_scan_survives_a_bad_edit_to_the_allowlist(monkeypatch):
    """The allowlist is a list someone can edit. The verb scan is the barrier
    that still holds after they do."""
    monkeypatch.setattr(mcp, "READ_ONLY_TOOLS", frozenset(mcp.READ_ONLY_TOOLS | {"close_position"}))
    with pytest.raises(MCPToolRefused, match="mutating verb"):
        mcp.assert_callable("close_position")


def test_every_allowlisted_tool_passes_its_own_verb_scan():
    """A regression guard on the allowlist itself: if someone adds a tool whose
    name contains a mutating verb, this fails before it can ship."""
    for tool in mcp.READ_ONLY_TOOLS:
        assert not mcp.is_mutating(tool), f"{tool} is allowlisted but reads as mutating"


def test_a_refused_call_never_reaches_the_server():
    with client() as session:
        with pytest.raises(MCPToolRefused):
            session.call("place_option_market_order", {"symbol": "SPY"})


# -- discovery over the real protocol ------------------------------------------


def test_discovery_reports_the_servers_real_surface():
    with client() as session:
        surface = session.surface

    assert surface.server_name == "alpaca-mcp-fake"
    assert surface.server_version == "2.3.0"
    assert surface.protocol_version == "2024-11-05"
    assert "get_account_info" in surface.callable_tools
    # The dangerous tools are present. That is expected and recorded.
    assert "place_option_market_order" in surface.mutating_tools
    assert "close_position" in surface.mutating_tools
    # ...and none of them is callable.
    assert not set(surface.callable_tools) & set(surface.mutating_tools)


def test_the_surface_report_serialises_for_evidence():
    with client() as session:
        payload = session.surface.as_dict()

    assert payload["server_version"] == "2.3.0"
    assert payload["tools_advertised"] == 11
    assert "place_option_market_order" in payload["mutating_tools_present"]
    assert payload["callable_tools"], "no read-only tool was found callable"


def test_a_tool_cannot_be_called_before_discovery():
    session = MCPClient([sys.executable, str(SERVER)])
    with pytest.raises(MCPError, match="before the surface was discovered"):
        session.call("get_account_info")


def test_an_allowlisted_tool_the_server_does_not_offer_is_refused():
    with client() as session:
        with pytest.raises(MCPToolRefused, match="does not advertise"):
            session.call("get_market_calendar")


# -- real calls ----------------------------------------------------------------


def test_a_read_only_call_round_trips_over_stdio():
    with client() as session:
        result = session.call("get_account_info")

    assert "PA3XYZ789012" in result
    assert "ACTIVE" in result


def test_server_output_is_redacted_before_it_is_returned():
    """Whatever the server sends is untrusted. A key-shaped string in it must
    not survive into our evidence."""
    with client() as session:
        result = session.call("get_account_info")

    assert "TESTONLYNOTAREALKEY01" not in result
    assert "<redacted>" in result


def test_a_server_instruction_to_call_a_mutating_tool_is_just_text():
    """Prompt injection through a tool result. It is data; acting on it is
    impossible because the next call still goes through the allowlist."""
    with client() as session:
        result = session.call("get_positions")
        assert "place_option_market_order" in result  # it really did say that
        with pytest.raises(MCPToolRefused):
            session.call("place_option_market_order")


def test_a_banner_before_the_protocol_does_not_break_the_session():
    with client("banner") as session:
        assert session.surface.server_name == "alpaca-mcp-fake"
        assert "PA3XYZ789012" in session.call("get_account_info")


def test_a_jsonrpc_error_is_raised_not_swallowed():
    with client("error") as session:
        with pytest.raises(MCPError, match="upstream failed"):
            session.call("get_account_info")


def test_a_tool_level_error_result_is_raised():
    with client("toolerror") as session:
        with pytest.raises(MCPError, match="returned an error"):
            session.call("get_account_info")


def test_an_unresponsive_server_times_out_rather_than_hanging():
    session = client("slow", timeout=1.0)
    with pytest.raises(MCPError, match="did not respond"):
        session.open()
    session.close()


def test_a_server_that_dies_is_reported_not_ignored():
    session = client("crash")
    with pytest.raises(MCPError, match="closed the connection"):
        session.open()
    session.close()


def test_a_missing_server_binary_is_a_clear_error():
    session = MCPClient(["glassbox-no-such-mcp-server-binary"])
    with pytest.raises(MCPError, match="not found"):
        session.open()


def test_close_is_idempotent():
    session = client()
    session.open()
    session.close()
    session.close()


# -- bounded, redacted output --------------------------------------------------


def test_large_results_are_truncated():
    text = mcp.summarise_result("x" * (mcp.MAX_RESULT_CHARS + 5000))
    assert len(text) < mcp.MAX_RESULT_CHARS + 200
    assert "truncated" in text


def test_summarise_redacts_every_known_secret_shape():
    text = mcp.summarise_result(
        {
            "a": "PK" + "TESTONLYNOTAREALKEY01",
            "b": FAKE_ANTHROPIC_KEY,
            "c": "https://discord.com/api/webhooks/1/abc",
        }
    )
    assert "TESTONLYNOTAREALKEY01" not in text
    assert FAKE_ANTHROPIC_KEY not in text
    assert "discord.com/api/webhooks/1/abc" not in text


def test_summarise_never_raises_on_unserialisable_input():
    class Awkward:
        def __repr__(self) -> str:
            return "awkward-object"

    assert "awkward" in mcp.summarise_result(Awkward())


# -- the proof capture tool ----------------------------------------------------


def _verify_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import verify_mcp_surface

    return verify_mcp_surface


def test_proof_capture_records_surface_calls_and_refusals():
    tool = _verify_module()
    env = dict(os.environ)
    env["GLASSBOX_FAKE_MCP_MODE"] = "normal"

    proof = tool.capture([sys.executable, str(SERVER)], env=env, timeout=10.0)

    assert proof.surface["server_version"] == "2.3.0"
    # Every mutating probe was refused before a request was written.
    refused = {r["tool"] for r in proof.refusals}
    assert refused == set(tool.REFUSAL_PROBES)
    assert all("mutating verb" in r["refused_with"] for r in proof.refusals)
    # Read-only calls actually happened.
    assert any(c["tool"] == "get_account_info" and c["ok"] for c in proof.calls)


def test_proof_is_incomplete_when_the_account_is_not_the_expected_one():
    tool = _verify_module()
    env = dict(os.environ)
    env["GLASSBOX_FAKE_MCP_MODE"] = "normal"

    proof = tool.capture(
        [sys.executable, str(SERVER)], env=env, timeout=10.0, expected_account_id="PA-SOMEONE-ELSE"
    )

    assert proof.surface["account_id_matches_expected"] is False
    assert proof.complete is False


def test_proof_is_complete_for_the_expected_account():
    tool = _verify_module()
    env = dict(os.environ)
    env["GLASSBOX_FAKE_MCP_MODE"] = "normal"

    proof = tool.capture(
        [sys.executable, str(SERVER)], env=env, timeout=10.0, expected_account_id="PA3XYZ789012"
    )

    assert proof.surface["account_id_matches_expected"] is True
    assert proof.complete is True


def test_captured_proof_never_contains_a_key_shaped_string():
    tool = _verify_module()
    env = dict(os.environ)
    env["GLASSBOX_FAKE_MCP_MODE"] = "normal"

    proof = tool.capture([sys.executable, str(SERVER)], env=env, timeout=10.0)
    body = json.dumps(proof.as_dict())

    assert "TESTONLYNOTAREALKEY01" not in body


def test_every_proof_call_is_allowlisted():
    tool = _verify_module()
    for name, _arguments in tool.PROOF_CALLS:
        mcp.assert_callable(name)


def test_every_refusal_probe_really_is_mutating():
    tool = _verify_module()
    for name in tool.REFUSAL_PROBES:
        assert mcp.is_mutating(name), f"{name} is probed as mutating but does not read as one"
