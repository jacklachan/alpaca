"""Glassbox entry point.

    python main.py                 run the agent
    python main.py --dry-run       wire everything up, print the schedule, exit
    python main.py --once          run a single tick and exit

The dry run is what you use before cutover: it builds every component and
asserts the account is correct, without starting the clock.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from glassbox import config as C  # noqa: E402
from glassbox import env  # noqa: E402
from glassbox.broker import Broker, NotPaperTrading  # noqa: E402
from glassbox.candidates import CANDIDATE_SCHEMA_VERSION  # noqa: E402
from glassbox.data import MarketData  # noqa: E402
from glassbox.journal import Journal  # noqa: E402
from glassbox.kernel import RiskKernel  # noqa: E402
from glassbox.manage import KillSwitch, PositionManager  # noqa: E402
from glassbox.position_ledger import PositionLedger  # noqa: E402
from glassbox.release import (
    ReleaseError,  # noqa: E402
    load_approved,  # noqa: E402
)
from glassbox.release import build as build_release  # noqa: E402
from glassbox.scheduler import Agent, discord  # noqa: E402
from glassbox.state import ProcessLock, StateLocked  # noqa: E402
from glassbox.strategies.core import CoreStrategy  # noqa: E402
from glassbox.strategies.crypto import CryptoStrategy  # noqa: E402
from glassbox.strategies.event_vol import EventVolStrategy  # noqa: E402


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)-18s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def strategy_names(environment: str) -> tuple[str, ...]:
    """The strategy families one account role may register, without building
    any of them. The manifest and the agent read the same source, so a
    manifest cannot claim options-only while the agent registers more."""
    if environment not in {"dev", "scored"}:
        raise ValueError(f"unknown environment {environment!r}")
    return ("event_vol",) if environment == "scored" else ("core", "crypto", "event_vol")


def strategy_set(environment: str, data: Any) -> dict[str, Any]:
    """Construct the explicit strategy surface for one account role."""
    if environment not in {"dev", "scored"}:
        raise ValueError(f"unknown environment {environment!r}")

    strategies: dict[str, Any] = {
        f"event_vol_{underlying.lower()}": EventVolStrategy(underlying=underlying, data=data)
        for underlying in C.SCORED_OPTION_UNDERLYINGS
    }
    if environment == "dev":
        strategies.update(
            {
                "core": CoreStrategy(),
                "crypto": CryptoStrategy(data=data),
            }
        )
    return strategies


def release_manifest():
    """Describe this running release from the tree and the environment.

    The strategy allowlist comes from the same function the agent composes
    from, so the manifest cannot claim options-only while the agent registers
    something else.
    """
    environment = env.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev")
    expected_key = (
        "ALPACA_EXPECTED_SCORED_ACCOUNT_ID"
        if environment == "scored"
        else "ALPACA_EXPECTED_DEV_ACCOUNT_ID"
    )
    allowlist = strategy_names(environment)
    return build_release(
        root=Path(__file__).resolve().parent,
        environment=environment,
        resolved_endpoint=env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        expected_account_id=env.get(expected_key, ""),
        strategy_allowlist=allowlist,
        option_underlyings=tuple(C.SCORED_OPTION_UNDERLYINGS),
        candidate_schema_version=int(CANDIDATE_SCHEMA_VERSION),
        policy={
            "convex_sleeve_usd": str(C.CONVEX_SLEEVE_USD),
            "event_trade_daily_cap": str(C.EVENT_TRADE_DAILY_CAP),
            "option_underlyings": list(C.SCORED_OPTION_UNDERLYINGS),
        },
        built_at=datetime.now(timezone.utc).isoformat(),
    )


def approved_release_manifest():
    """Return scored authority or fail before broker construction.

    Development runs have no scored authority and therefore need no release
    evidence. A scored run cannot turn this gate off: a missing or false flag
    is itself a refusal.
    """
    environment = env.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev")
    if environment == "dev":
        return None
    if env.get("GLASSBOX_RELEASE_GATE", "") != "1":
        raise ReleaseError("scored mode requires GLASSBOX_RELEASE_GATE=1")

    approved_commit = env.get("GLASSBOX_APPROVED_COMMIT_SHA", "")
    path = Path(env.get("GLASSBOX_RELEASE_MANIFEST_PATH", "state/release.json"))
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return load_approved(
        path,
        current=release_manifest(),
        approved_commit=approved_commit,
    )


def build(thesis_enabled: bool = True):
    journal = Journal(C.JOURNAL_PATH)
    broker = Broker(journal=journal)
    data = MarketData(broker)
    kill = KillSwitch(journal=journal)

    # The scored account owns option contracts per-contract, so it gets a
    # ledger and the exact-quantity exit path. The development sleeves are
    # equity and crypto; they own no contracts and keep the symbol-wide path.
    ledger = None
    ledger_path = None
    if broker.env == "scored":
        ledger_path = Path(C.LEDGER_STATE_FILE)
        expected = env.get("ALPACA_EXPECTED_SCORED_ACCOUNT_ID", "")
        ledger = PositionLedger.load(ledger_path, account_id=expected, environment=broker.env)

    manager = PositionManager(broker, journal, kill, ledger=ledger, ledger_path=ledger_path)

    strategies = strategy_set(broker.env, data)

    thesis = None
    if thesis_enabled:
        try:
            from glassbox.thesis import ThesisLayer

            thesis = ThesisLayer()
        except Exception as exc:
            logging.warning("thesis layer unavailable (%s); deterministic sleeves continue", exc)

    agent = Agent(
        broker,
        journal,
        RiskKernel(),
        manager,
        strategies,
        thesis,
        ledger=ledger,
        ledger_path=ledger_path,
    )
    return agent, journal, broker


def _runtime_lock_path() -> Path:
    """Derive singleton ownership from the authoritative journal directory."""
    journal_path = Path(C.JOURNAL_PATH)
    if not journal_path.is_absolute():
        journal_path = Path(__file__).resolve().parent / journal_path
    return journal_path.parent / "scheduler.lock"


def _run_runtime(args: argparse.Namespace, log: logging.Logger) -> int:
    """Build and run while the caller retains any scored ownership lock."""
    try:
        agent, journal, broker = build(thesis_enabled=not args.no_thesis)
    except NotPaperTrading as exc:
        log.critical("REFUSING TO START: %s", exc)
        return 2
    except Exception as exc:
        log.critical("startup failed: %s", exc)
        return 1

    if args.dry_run:
        try:
            info = broker.assert_ready()
        except Exception as exc:
            transient = any(
                t in str(exc).lower()
                for t in ("proxy", "timeout", "connection", "temporarily", "unreachable", "resolve")
            )
            if transient:
                log.critical("cannot reach Alpaca: %s", exc)
                log.critical(
                    "this looks transient; the service will retry. Check egress/DNS if it persists."
                )
                return 4
            log.critical("ACCOUNT CHECK FAILED: %s", exc)
            log.critical("this is not transient. Do not start the agent until it is understood.")
            return 5
        log.info(
            "account %s | equity %s | env=%s | options level %s",
            info["account_number"],
            info["equity"],
            info["env"],
            info["options_level"],
        )
        for job in sorted(agent.build().get_jobs(), key=lambda j: j.id):
            log.info("  %-14s %s", job.id, job.trigger)
        if agent.manager.kill.tripped:
            log.warning("kill switch is LATCHED: %s", agent.manager.kill.state().get("reason"))
        log.info("dry run complete; clock not started")
        return 0

    if args.once:
        broker.assert_ready()
        agent.equity_tick()
        log.info("single tick complete")
        return 0

    agent.run()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="glassbox")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="wire up, verify the account, print the schedule, exit",
    )
    ap.add_argument("--once", action="store_true", help="one tick, then exit")
    ap.add_argument(
        "--no-thesis",
        action="store_true",
        help="disable AI selection; scored cycles safely abstain",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    log = logging.getLogger("glassbox")

    try:
        environment = env.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev")
    except env.EnvError as exc:
        log.critical("RELEASE GATE REFUSED START: %s", exc)
        return 6

    runtime_lock = None
    try:
        # Scored ownership begins before Journal construction because recovery
        # may repair and append to the authoritative state. It remains held
        # through build, account assertion, every tick, and normal shutdown.
        if environment == "scored":
            runtime_lock = ProcessLock(_runtime_lock_path()).acquire()

        # Verify the audit trail before anything that needs credentials. The
        # lock above is filesystem-only and therefore preserves that ordering.
        journal = Journal(C.JOURNAL_PATH)
        ok, why = journal.verify()
        log.info("journal: %s", why)
        if not ok:
            log.critical("JOURNAL CHAIN BROKEN: %s", why)
            discord(f":rotating_light: glassbox journal chain broken: {why}")
            return 3

        # Release identity, before credentials are used for anything. Scored
        # mode cannot disable this gate; development has no scored authority.
        try:
            manifest = approved_release_manifest()
        except (ReleaseError, env.EnvError) as exc:
            log.critical("RELEASE GATE REFUSED START: %s", exc)
            return 6
        if manifest is not None:
            log.info("release gate: commit %s, options-only, paper", manifest.commit[:12])

        return _run_runtime(args, log)
    except StateLocked as exc:
        log.critical("SCORED RUNTIME OWNERSHIP REFUSED: %s", exc)
        return 7
    finally:
        if runtime_lock is not None:
            runtime_lock.release()


if __name__ == "__main__":
    sys.exit(main())
