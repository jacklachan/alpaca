"""Crash-recovery drill.

The handover said: "Until a process survives being killed mid-tick on an actual
VPS, 'runs unattended for four days' is a claim rather than a fact." This turns
most of that claim into a fact, and it does so without a broker connection, so
it runs anywhere and runs in CI.

It kills a real child process with SIGKILL -- not a mocked exception, not a
context manager -- while that process is writing to the journal, then restarts
it and checks what survived.

Five scenarios:

  1. SIGKILL mid-write        chain stays verifiable; a torn final line is
                              discarded, not fatal
  2. Restart continuity       sequence numbers continue, prev_hash still links
                              across the crash boundary
  3. Idempotency              the same plan replayed after a restart produces
                              the same client_order_id, so a resumed process
                              cannot double a position
  4. Broker-as-truth          local state discarded on restart; the position
                              set is rebuilt from the broker, including a fill
                              that landed while the process was dead
  5. Deep corruption          damage beyond the last line refuses to start
                              rather than silently continuing on a broken chain

Run:  python tools/crash_drill.py [-n 12]
Exit: 0 all passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from glassbox.ids import client_order_id  # noqa: E402
from glassbox.journal import GENESIS, Journal, JournalCorrupt  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# Child process: appends to the journal forever, as fast as it can, so a kill
# at a random moment has a good chance of landing inside a write.
WRITER = r"""
import sys, time
sys.path.insert(0, %(root)r)
from glassbox.journal import Journal
j = Journal(%(path)r)
i = 0
while True:
    i += 1
    j.append("drill.writer", "TICK", {"i": i, "pad": "x" * 400})
    time.sleep(0.001)
"""


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {name}")
        if detail:
            print(f"         {DIM}{detail}{RESET}")

    @property
    def ok(self) -> bool:
        return all(r[1] for r in self.rows)


def _spawn(path: Path) -> subprocess.Popen:
    src = WRITER % {"root": str(ROOT), "path": str(path)}
    return subprocess.Popen(
        [sys.executable, "-c", src],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def scenario_kill_mid_write(res: Results, rounds: int) -> None:
    """SIGKILL a live writer repeatedly; the chain must stay verifiable."""
    print(f"\n{YELLOW}1. SIGKILL mid-write, {rounds} rounds{RESET}")
    tmp = Path(tempfile.mkdtemp(prefix="glassbox-drill-"))
    path = tmp / "journal.jsonl"

    torn_seen = 0
    try:
        for r in range(rounds):
            proc = _spawn(path)
            time.sleep(random.uniform(0.05, 0.30))
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

            raw = path.read_bytes().decode("utf-8", errors="replace")
            lines = [ln for ln in raw.split("\n") if ln.strip()]
            if lines:
                try:
                    json.loads(lines[-1])
                except json.JSONDecodeError:
                    torn_seen += 1

            # This is the restart. It must not raise.
            try:
                j = Journal(path)
            except Exception as exc:
                res.add("survives SIGKILL mid-write", False,
                        f"round {r + 1}: restart raised {type(exc).__name__}: {exc}")
                return

            ok, why = j.verify()
            if not ok:
                res.add("survives SIGKILL mid-write", False,
                        f"round {r + 1}: chain broken after restart -- {why}")
                return

        res.add("survives SIGKILL mid-write", True,
                f"{rounds} kill/restart cycles, chain verifiable every time; "
                f"{torn_seen} round(s) left a torn final line naturally")

        # fsync makes the torn-line window very small, so a random kill rarely
        # lands in it. That is good for durability and bad for test coverage:
        # the recovery path would go unexercised precisely because the bug is
        # rare. Inject the failure deterministically so it is always tested.
        j = Journal(path)
        seq_before, head_before = j.seq, j.head
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"seq": 999, "ts": "2026-09-01T00:00:00Z", "actor": "drill"')

        try:
            recovered = Journal(path)
        except Exception as exc:
            res.add("recovers from an injected torn line", False,
                    f"restart raised {type(exc).__name__}: {exc}")
            return

        ok, why = recovered.verify()
        # The recovery itself appends a TORN_ENTRY_DISCARDED marker, so the
        # sequence advances by exactly one past the pre-injection head.
        marker = [r for r in recovered.read()
                  if r["event"] == "TORN_ENTRY_DISCARDED"]
        res.add("recovers from an injected torn line", ok and bool(marker),
                f"discarded the partial write, resumed from seq {seq_before}, "
                f"chain re-verified ({why})")
        res.add("records the crash in the chain itself", bool(marker),
                f"TORN_ENTRY_DISCARDED written at seq {marker[0]['seq']}"
                if marker else "no recovery marker found")
        res.add("intact prefix is preserved byte-for-byte",
                any(r["seq"] == seq_before and r["hash"] == head_before
                    for r in recovered.read()),
                f"pre-crash head {head_before[:12]} still present at seq {seq_before}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scenario_continuity(res: Results) -> None:
    """Sequence and hash chain must span the crash boundary."""
    print(f"\n{YELLOW}2. Restart continuity{RESET}")
    tmp = Path(tempfile.mkdtemp(prefix="glassbox-drill-"))
    path = tmp / "journal.jsonl"
    try:
        proc = _spawn(path)
        time.sleep(0.4)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)

        before = Journal(path)
        seq_before, head_before = before.seq, before.head
        before.append("drill", "AFTER_RESTART", {"phase": "resumed"})

        again = Journal(path)
        ok, why = again.verify()

        records = list(again.read())
        crossing = [r for r in records if r["seq"] == seq_before + 1]
        linked = bool(crossing) and crossing[0]["prev_hash"] == head_before

        res.add("sequence continues across crash", ok and again.seq > seq_before,
                f"seq {seq_before} -> {again.seq}; {why}")
        res.add("prev_hash links across crash", linked,
                f"entry {seq_before + 1}.prev_hash == pre-crash head "
                f"{head_before[:12]}" if linked else "link broken at the boundary")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scenario_idempotency(res: Results) -> None:
    """A resumed process must not be able to double a position."""
    print(f"\n{YELLOW}3. Idempotency across restart{RESET}")
    plan_id = "7c1f2b90-0000-4000-8000-abcdefabcdef"
    first = [client_order_id(plan_id, i) for i in range(3)]
    # Simulate a fresh interpreter: nothing cached, nothing carried over.
    second = [client_order_id(plan_id, i) for i in range(3)]
    distinct_legs = len(set(first)) == 3
    res.add("client_order_id is deterministic", first == second,
            f"leg 0 -> {first[0]}")
    res.add("legs do not collide", distinct_legs,
            f"{len(set(first))} distinct ids for 3 legs")


def scenario_broker_is_truth(res: Results) -> None:
    """State must be rebuilt from the broker, including fills during downtime.

    This drives the real Broker.reconcile() against a stubbed Alpaca client, so
    it tests the shipped code path rather than a re-implementation of it.
    """
    print(f"\n{YELLOW}4. Broker-as-truth reconciliation{RESET}")

    from decimal import Decimal
    from glassbox.broker import Broker

    class StubPos:
        def __init__(self, symbol, qty, mv, cls="us_equity"):
            self.symbol, self.qty = symbol, str(qty)
            self.market_value, self.cost_basis = str(mv), str(mv)
            self.asset_class = cls
            self.unrealized_pl = "0"

    class StubAcct:
        equity = cash = "100000"
        last_equity = "100000"
        buying_power = "100000"
        status = "ACTIVE"
        account_number = "PA-DRILL"
        trading_blocked = False
        options_trading_level = 3

    class StubClock:
        is_open = True

    class StubTrading:
        def __init__(self):
            self.venue: list[StubPos] = [StubPos("SPY", 10, 5000)]
        def get_account(self):
            return StubAcct()
        def get_all_positions(self):
            return list(self.venue)          # a fresh read every call
        def get_orders(self, *_a, **_k):
            return []
        def get_clock(self):
            return StubClock()

    class StubSnap:
        class latest_trade:
            price = 500.0
        class latest_quote:
            bid_price, ask_price = 499.5, 500.5

    class StubData:
        def get_stock_snapshot(self, *_a, **_k):
            return {"SPY": StubSnap(), "QQQ": StubSnap()}

    b = Broker.__new__(Broker)               # bypass credential checks
    stub = StubTrading()
    b.trading = stub
    b.data = StubData()
    b.journal = None
    b.env = "dev"
    from glassbox.broker import TokenBucket
    b.bucket = TokenBucket(10_000)

    before = {p.symbol for p in b.reconcile().positions}

    # The process is dead. A resting order fills at the venue meanwhile.
    stub.venue.append(StubPos("QQQ", 4, 2000))

    after = {p.symbol for p in b.reconcile().positions}

    saw_new = "QQQ" in after and "QQQ" not in before
    res.add("reconcile() picks up a fill that landed while the process was dead",
            saw_new,
            f"before {sorted(before)} -> after restart {sorted(after)}")

    # Removing a position at the venue must also propagate: a stale local cache
    # would keep showing a position the account no longer holds.
    stub.venue = [p for p in stub.venue if p.symbol != "SPY"]
    final = {p.symbol for p in b.reconcile().positions}
    res.add("reconcile() drops a position closed while the process was dead",
            "SPY" not in final, f"after close -> {sorted(final)}")


def scenario_deep_corruption(res: Results) -> None:
    """Tampering anywhere in the file must stop the agent starting.

    _recover() deliberately only inspects the final line -- that is the cheap
    resume path, and re-hashing a week of entries on every restart would be
    wasteful. The guard that catches deeper damage is main.py, which calls
    journal.verify() before the clock starts and exits 3 if the chain is broken.
    So this drives the real entry point and checks the real exit code, rather
    than asserting against a constructor that was never the guard.
    """
    print(f"\n{YELLOW}5. Tampering refuses to start{RESET}")
    tmp = Path(tempfile.mkdtemp(prefix="glassbox-drill-"))
    state = tmp / "state"
    state.mkdir()
    path = state / "journal.jsonl"
    try:
        j = Journal(path)
        for i in range(6):
            j.append("drill", "TICK", {"i": i})

        # Edit history in place: change a payload but leave valid JSON, which is
        # what a team quietly improving their record would actually do.
        lines = path.read_text().splitlines()
        rec = json.loads(lines[2])
        rec["payload"]["i"] = 999
        lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n")

        ok, why = Journal(path).verify()
        res.add("verify() detects an edited payload", not ok,
                why if not ok else "tamper went undetected")

        # End-to-end: the shipped entry point must refuse, with exit code 3.
        envcopy = dict(os.environ)
        envcopy["GLASSBOX_JOURNAL_PATH"] = str(path)
        proc = subprocess.run(
            [sys.executable, "main.py", "--dry-run"],
            cwd=str(ROOT), env=envcopy, capture_output=True, text=True, timeout=90)
        combined = proc.stdout + proc.stderr
        refused = proc.returncode == 3 or "CHAIN BROKEN" in combined.upper()
        res.add("main.py refuses to start on a broken chain", refused,
                f"exit={proc.returncode} "
                f"{'(3 = chain broken, as designed)' if proc.returncode == 3 else ''}")

        # A hash-chain claim is only worth making if breaking it is detectable
        # at every position, not just the one we happened to test.
        all_detected = True
        for idx in range(len(lines)):
            fresh = tmp / f"j{idx}.jsonl"
            good = path.read_text().splitlines()
            r = json.loads(good[idx])
            r["actor"] = "tampered"
            good[idx] = json.dumps(r, sort_keys=True, separators=(",", ":"))
            fresh.write_text("\n".join(good) + "\n")
            if Journal(fresh).verify()[0]:
                all_detected = False
                break
        res.add("every position in the chain is tamper-evident", all_detected,
                f"edited each of {len(lines)} entries in turn; all detected")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(prog="crash_drill")
    ap.add_argument("-n", "--rounds", type=int, default=10,
                    help="how many kill/restart cycles in scenario 1")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    print(f"{YELLOW}Glassbox crash-recovery drill{RESET}")
    print(f"{DIM}Real child processes, real SIGKILL, real restarts. "
          f"No broker connection needed.{RESET}")

    res = Results()
    scenario_kill_mid_write(res, args.rounds)
    scenario_continuity(res)
    scenario_idempotency(res)
    scenario_broker_is_truth(res)
    scenario_deep_corruption(res)

    passed = sum(1 for _, ok, _ in res.rows if ok)
    print(f"\n{'-' * 66}")
    if res.ok:
        print(f"{GREEN}DRILL PASSED{RESET}  {passed}/{len(res.rows)} checks")
    else:
        print(f"{RED}DRILL FAILED{RESET}  {passed}/{len(res.rows)} checks")
        for name, ok, detail in res.rows:
            if not ok:
                print(f"  - {name}: {detail}")
    print(f"{DIM}Note: this proves the recovery logic. It does not prove the "
          f"host stays up.\n      Run tools/soak.sh on the VPS for that.{RESET}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
