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

from dotenv import load_dotenv

load_dotenv()

from glassbox import config as C           # noqa: E402
from glassbox.broker import Broker, NotPaperTrading  # noqa: E402
from glassbox.data import MarketData        # noqa: E402
from glassbox.journal import Journal        # noqa: E402
from glassbox.kernel import RiskKernel      # noqa: E402
from glassbox.manage import KillSwitch, PositionManager  # noqa: E402
from glassbox.scheduler import Agent, discord  # noqa: E402
from glassbox.strategies.core import CoreStrategy      # noqa: E402
from glassbox.strategies.crypto import CryptoStrategy  # noqa: E402
from glassbox.strategies.event_vol import EventVolStrategy  # noqa: E402


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)-18s %(message)s",
        stream=sys.stdout)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build(thesis_enabled: bool = True):
    journal = Journal(C.JOURNAL_PATH)
    broker = Broker(journal=journal)
    data = MarketData(broker)
    kill = KillSwitch(journal=journal)
    manager = PositionManager(broker, journal, kill)

    strategies = {
        "event_vol": EventVolStrategy(underlying="SPY", data=data),
        "core": CoreStrategy(),
        "crypto": CryptoStrategy(data=data),
    }

    thesis = None
    if thesis_enabled:
        try:
            from glassbox.thesis import ThesisLayer
            thesis = ThesisLayer()
        except Exception as exc:
            logging.warning("thesis layer unavailable (%s); "
                            "deterministic sleeves continue", exc)

    agent = Agent(broker, journal, RiskKernel(), manager, strategies, thesis)
    return agent, journal, broker


def main() -> int:
    ap = argparse.ArgumentParser(prog="glassbox")
    ap.add_argument("--dry-run", action="store_true",
                    help="wire up, verify the account, print the schedule, exit")
    ap.add_argument("--once", action="store_true", help="one tick, then exit")
    ap.add_argument("--no-thesis", action="store_true",
                    help="run the deterministic sleeves only")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    log = logging.getLogger("glassbox")

    try:
        agent, journal, broker = build(thesis_enabled=not args.no_thesis)
    except NotPaperTrading as exc:
        log.critical("REFUSING TO START: %s", exc)
        return 2
    except Exception as exc:
        log.critical("startup failed: %s", exc)
        return 1

    ok, why = journal.verify()
    log.info("journal: %s", why)
    if not ok:
        log.critical("JOURNAL CHAIN BROKEN: %s", why)
        discord(f":rotating_light: glassbox journal chain broken: {why}")
        return 3

    if args.dry_run:
        info = broker.assert_ready()
        log.info("account %s | equity %s | env=%s | options level %s",
                 info["account_number"], info["equity"], info["env"],
                 info["options_level"])
        for job in sorted(agent.build().get_jobs(), key=lambda j: j.id):
            log.info("  %-14s %s", job.id, job.trigger)
        if agent.manager.kill.tripped:
            log.warning("kill switch is LATCHED: %s",
                        agent.manager.kill.state().get("reason"))
        log.info("dry run complete; clock not started")
        return 0

    if args.once:
        broker.assert_ready()
        agent.equity_tick()
        log.info("single tick complete")
        return 0

    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
