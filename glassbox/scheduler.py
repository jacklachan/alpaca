"""The clock. Everything internal is UTC.

Timezone bugs are the most reliable way to lose a trading hackathon, so there
is exactly one conversion, at the edge, and market state comes from Alpaca's
own clock rather than from any holiday logic of ours.

APScheduler configuration matters more than it looks. On a one-minute tick with
defaults, a job that overruns gets a second instance started alongside it, and
two instances hit the broker concurrently with the same view of the world.
Hence max_instances=1, coalesce=True, and an explicit misfire_grace_time so a
job skipped during a restart does not fire a backlog all at once.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import urllib.request
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config as C
from .macro import MEASUREMENT_ET
from .manage import measurement_countdown

log = logging.getLogger("glassbox.scheduler")

# Defaults applied to every job. See the module docstring.
JOB_DEFAULTS = {
    "max_instances": 1,      # never two copies of the same job at once
    "coalesce": True,        # a backlog collapses to one run, not N
    "misfire_grace_time": 30,
}


def discord(message: str, webhook: str | None = None) -> bool:
    url = webhook or os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"content": message[:1900]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as exc:
        log.warning("discord post failed: %s", exc)
        return False


class Agent:
    """Wires the pieces together and owns the schedule.

    Every job is wrapped so an exception is logged and journalled but never
    kills the scheduler. A crashed tick must cost one tick.
    """

    def __init__(self, broker, journal, kernel, manager, strategies: dict,
                 thesis=None):
        self.broker = broker
        self.journal = journal
        self.kernel = kernel
        self.manager = manager
        self.strategies = strategies
        self.thesis = thesis
        self.scheduler = BackgroundScheduler(
            timezone="UTC", job_defaults=JOB_DEFAULTS)
        self._positioned_for: set[str] = set()

    # -- safety wrapper --------------------------------------------------------

    def _guard(self, name: str, fn) -> None:
        def wrapped():
            try:
                fn()
            except Exception as exc:
                log.exception("%s failed", name)
                self.journal.append("scheduler", "JOB_FAILED",
                                    {"job": name, "error": str(exc)})
                discord(f":warning: glassbox job `{name}` failed: {exc}")
        return wrapped

    # -- jobs ------------------------------------------------------------------

    def equity_tick(self) -> None:
        """Reconcile, manage, then execute anything the kernel approves."""
        state = self.broker.reconcile(kill_switch_tripped=self.manager.kill.tripped)
        self.manager.tick(state)

        if self.manager.kill.tripped or not state.market_open:
            return

        for name, strategy in self.strategies.items():
            for plan in strategy.propose_from_state(state, self._positioned_for):
                verdict = self.kernel.review(plan, state)
                self.journal.append(
                    "risk.kernel",
                    "PLAN_APPROVED" if verdict.approved else "PLAN_REFUSED",
                    {"plan_id": plan.plan_id, "strategy": name,
                     "sleeve": plan.sleeve, "symbol": plan.symbol,
                     "thesis": plan.thesis, "evidence": plan.evidence,
                     "checks_passed": verdict.checks_passed,
                     "checks_total": verdict.checks_total,
                     "reason": verdict.reason,
                     "failed_invariant": verdict.failed_invariant})
                if verdict.approved:
                    self.execute(plan, verdict)
                    state = self.broker.reconcile(
                        kill_switch_tripped=self.manager.kill.tripped)

    def crypto_tick(self) -> None:
        """Same loop, crypto only. Runs 24/7 -- including the hour after the
        payrolls print, when the equity market is still closed."""
        state = self.broker.reconcile(kill_switch_tripped=self.manager.kill.tripped)
        self.manager.tick(state)

    def heartbeat(self) -> None:
        state = self.broker.reconcile(kill_switch_tripped=self.manager.kill.tripped)
        head = self.journal.head[:12]
        msg = (f":green_heart: glassbox | equity ${state.equity:,.2f} | "
               f"{len(state.positions)} positions | "
               f"journal seq {self.journal.seq} head {head} | "
               f"{measurement_countdown(state.now_et)}")
        if self.manager.kill.tripped:
            msg = f":red_circle: KILL SWITCH LATCHED | {msg}"
        discord(msg)
        self.journal.append("scheduler", "HEARTBEAT", {
            "equity": str(state.equity), "positions": len(state.positions),
            "kill_switch": self.manager.kill.tripped})

    def anchor(self) -> None:
        """Publish the journal head somewhere with a clock we do not control."""
        self.journal.anchor(os.getenv("DISCORD_WEBHOOK_URL"))

    def eod_manage(self) -> None:
        state = self.broker.reconcile(kill_switch_tripped=self.manager.kill.tripped)
        self.manager.tick(state)
        self.journal.append("scheduler", "EOD_POSTURE", {
            "equity": str(state.equity),
            "positions": [p.symbol for p in state.positions]})

    def daily_review(self) -> None:
        """Plain-English summary into the journal. The only scheduled job the
        model touches, and it cannot place an order."""
        if self.thesis is None:
            return
        state = self.broker.reconcile(kill_switch_tripped=self.manager.kill.tripped)
        try:
            summary = self.thesis.daily_review(state, self.journal)
        except Exception as exc:
            self.journal.append("thesis.llm", "REVIEW_FAILED", {"error": str(exc)})
            return
        self.journal.append("thesis.llm", "DAILY_REVIEW", {"summary": summary})

    def execute(self, plan, verdict) -> None:
        from .execute import ExecutionEngine
        engine = ExecutionEngine(self.broker, self.journal)
        result = engine.execute(plan, verdict)
        if result.ok:
            self._positioned_for.add(plan.symbol)
            if plan.time_exit or plan.stop or plan.target:
                for leg in (plan.option_legs or []):
                    self.manager.register(leg.symbol, stop=plan.stop,
                                          target=plan.target,
                                          time_exit=plan.time_exit)
                if not plan.option_legs:
                    self.manager.register(plan.symbol, stop=plan.stop,
                                          target=plan.target,
                                          time_exit=plan.time_exit)

    # -- schedule --------------------------------------------------------------

    def build(self) -> BackgroundScheduler:
        s, add = self.scheduler, self.scheduler.add_job

        def cron(**kw):
            return CronTrigger(timezone=C.ET, **kw)

        # Market hours only. Alpaca's clock is still checked inside the tick --
        # cron gets us close, get_clock() is the authority.
        add(self._guard("equity_tick", self.equity_tick),
            cron(day_of_week="mon-fri", hour="9-15", minute="*"), id="equity_tick")

        add(self._guard("crypto_tick", self.crypto_tick),
            cron(minute="*/5"), id="crypto_tick")

        add(self._guard("eod_manage", self.eod_manage),
            cron(day_of_week="mon-fri", hour=14, minute=30), id="eod_manage")

        add(self._guard("daily_review", self.daily_review),
            cron(day_of_week="mon-fri", hour=16, minute=15), id="daily_review")

        add(self._guard("heartbeat", self.heartbeat),
            cron(minute=f"*/{C.HEARTBEAT_INTERVAL_MIN}"), id="heartbeat")

        add(self._guard("anchor", self.anchor),
            cron(minute=0), id="anchor")

        return s

    def run(self) -> None:
        info = self.broker.assert_ready()
        log.info("glassbox starting against account %s (%s)",
                 info["account_number"], info["env"])
        discord(f":rocket: glassbox up | account {info['account_number']} | "
                f"equity ${float(info['equity']):,.2f} | env={info['env']}")

        s = self.build()
        s.start()
        for j in sorted(s.get_jobs(), key=lambda j: j.id):
            log.info("scheduled %s -> %s", j.id, j.trigger)

        stopping = {"now": False}

        def stop(signum, frame):
            stopping["now"] = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        import time
        try:
            while not stopping["now"]:
                time.sleep(1)
        finally:
            self.journal.append("scheduler", "SHUTDOWN",
                                {"at": datetime.now(timezone.utc).isoformat()})
            discord(":octagonal_sign: glassbox shutting down")
            s.shutdown(wait=False)
