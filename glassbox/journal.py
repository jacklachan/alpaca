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
import os
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64


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
        self._seq, self._head = self._recover()

    def _recover(self) -> tuple[int, str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS
        last = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return 0, GENESIS
        rec = json.loads(last)
        return int(rec["seq"]), str(rec["hash"])

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
        for rec in self.read():
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
