"""Hash-chained, append-only decision journal.

Each entry carries the hash of the entry before it, so editing history in place
breaks the chain and a verifier detects it.

Honest limits -- read this before making claims about it in a pitch:

  A hash chain written by the same process that writes the entries proves the
  file has not been *casually edited*. It does NOT prove the authors did not
  regenerate the whole chain from altered content, because we control every
  input to the hash. Self-attestation is not third-party attestation.

  Two things close most of that gap, and both are implemented here:
    1. anchor()  -- publish the current head hash to somewhere with a
       server-side timestamp we do not control (Discord webhook). An anchor
       posted at 09:14 constrains every entry before it.
    2. every ORDER_* entry carries Alpaca's own broker_order_id and
       broker-side timestamp, which we also do not control and which judges
       can reconcile against the account.

  Claim "reconcilable against broker-side records", not "tamper-proof".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("glassbox.journal")

GENESIS = "0" * 64


class JournalCorrupt(RuntimeError):
    """Damage beyond a single interrupted write. Never auto-repaired."""


def _canonical(obj: Any) -> Any:
    """Deterministic JSON-able form. Hashes must be reproducible across runs."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(obj, dict):
        return {k: _canonical(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return _canonical(obj.model_dump(mode="json"))
    return obj


def entry_hash(seq: int, ts: str, actor: str, event: str, payload: Any, prev_hash: str) -> str:
    body = json.dumps(
        {"seq": seq, "ts": ts, "actor": actor, "event": event,
         "payload": _canonical(payload), "prev_hash": prev_hash},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


class Journal:
    """Append-only JSONL with a SHA-256 chain. One writer, many readers."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._truncated: str | None = None
        self._seq, self._head = self._recover()
        if self._truncated is not None:
            # Record the repair inside the chain itself, so the fact that a
            # crash happened is part of the audit trail rather than a detail
            # buried in a log file the judges never see.
            self.append("journal.recover", "TORN_ENTRY_DISCARDED",
                        {"bytes": len(self._truncated),
                         "resumed_at_seq": self._seq,
                         "note": "interrupted write; entry was never acknowledged"})

    def _recover(self) -> tuple[int, str]:
        """Resume the chain after a restart, tolerating a torn final line.

        append() is write + flush + fsync, but a SIGKILL can still land between
        the write and the fsync and leave a partial line at the end of the file.
        Parsing that line used to raise, which meant one unlucky `kill -9`
        permanently bricked startup: the process could never boot again, and the
        agent that was supposed to survive four days unattended would be dead
        until a human noticed and hand-edited the file.

        A torn line is by definition the entry we were mid-way through writing,
        so it was never acknowledged to anyone and no order depends on it. The
        safe move is to truncate it, keep the intact prefix, and say so loudly.
        We refuse only when the damage is deeper than the last line, because
        that is corruption rather than an interrupted write.
        """
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS

        raw = self.path.read_bytes()
        lines = [ln for ln in raw.decode("utf-8", errors="replace").split("\n") if ln.strip()]
        if not lines:
            return 0, GENESIS

        try:
            rec = json.loads(lines[-1])
            return int(rec["seq"]), str(rec["hash"])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        self._truncated = lines[-1]
        good = lines[:-1]
        if not good:
            log.error("journal: only entry is torn; starting a fresh chain")
            self._rewrite(good)
            return 0, GENESIS

        try:
            rec = json.loads(good[-1])
            seq, head = int(rec["seq"]), str(rec["hash"])
        except Exception as exc:
            raise JournalCorrupt(
                f"{self.path}: damage extends beyond the final line ({exc}). "
                f"This is not an interrupted write. Do not start the agent -- "
                f"copy the file aside and inspect it."
            ) from exc

        log.error("journal: discarded a torn final line (%d bytes) left by an "
                  "interrupted write; resuming from seq %d",
                  len(self._truncated), seq)
        self._rewrite(good)
        return seq, head

    def _rewrite(self, lines: list[str]) -> None:
        """Atomically replace the file with the intact prefix."""
        tmp = self.path.with_suffix(self.path.suffix + ".repair")
        with tmp.open("w", encoding="utf-8") as fh:
            for ln in lines:
                fh.write(ln + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    @property
    def head(self) -> str:
        return self._head

    @property
    def seq(self) -> int:
        return self._seq

    def append(self, actor: str, event: str, payload: Any) -> dict:
        """Write one entry. Durable before returning -- a crash must not lose it."""
        with self._lock:
            seq = self._seq + 1
            ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            prev = self._head
            h = entry_hash(seq, ts, actor, event, payload, prev)
            rec = {
                "seq": seq, "ts": ts, "actor": actor, "event": event,
                "payload": _canonical(payload), "prev_hash": prev, "hash": h,
            }
            line = json.dumps(rec, sort_keys=True, separators=(",", ":"))
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._seq, self._head = seq, h
            return rec

    def read(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> tuple[bool, str]:
        """Recompute the whole chain. Returns (ok, human-readable reason)."""
        prev = GENESIS
        expected_seq = 0
        count = 0
        try:
            records = list(self.read())
        except json.JSONDecodeError as exc:
            # Report, never raise. verify() is called on the startup path and
            # by the demo verifier; both need a verdict, not a traceback.
            return False, f"unparseable entry after {count} records: {exc}"
        for rec in records:
            expected_seq += 1
            count += 1
            if rec.get("seq") != expected_seq:
                return False, f"seq gap at entry {count}: expected {expected_seq}, got {rec.get('seq')}"
            if rec.get("prev_hash") != prev:
                return False, f"chain break at seq {rec['seq']}: prev_hash does not match seq {expected_seq - 1}"
            recomputed = entry_hash(
                rec["seq"], rec["ts"], rec["actor"], rec["event"], rec["payload"], rec["prev_hash"]
            )
            if recomputed != rec.get("hash"):
                return False, f"content altered at seq {rec['seq']}: hash mismatch"
            prev = rec["hash"]
        return True, f"chain intact: {count} entries, head {prev[:12]}"

    def anchor(self, webhook_url: str | None) -> str | None:
        """Publish the head hash somewhere we do not control the clock.

        Cheap third-party timestamping: an anchor accepted by Discord at a given
        wall-clock time constrains every entry written before it.
        """
        if not webhook_url:
            return None
        import urllib.request

        msg = f"glassbox anchor · seq {self._seq} · head {self._head}"
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps({"content": msg}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
            self.append("journal.anchor", "ANCHOR_PUBLISHED",
                        {"seq": self._seq, "head": self._head})
            return self._head
        except Exception as exc:  # never let an anchor failure stop trading
            self.append("journal.anchor", "ANCHOR_FAILED", {"error": str(exc)})
            return None
