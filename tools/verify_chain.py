"""Standalone journal verifier.

    python tools/verify_chain.py [path]

No imports from the agent beyond the journal itself, no credentials, no
network. Point it at a journal file and it recomputes the entire hash chain.

This is the demo: run it, show it pass, change one byte of the log, run it
again, show it fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from glassbox.journal import Journal  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "state/journal.jsonl")
    if not path.exists():
        print(f"no journal at {path}")
        return 1

    j = Journal(path)
    ok, reason = j.verify()

    entries = list(j.read())
    print(f"journal   {path}")
    print(f"entries   {len(entries)}")
    if entries:
        print(f"first     {entries[0]['ts']}")
        print(f"last      {entries[-1]['ts']}")
        actors: dict[str, int] = {}
        for e in entries:
            actors[e["actor"]] = actors.get(e["actor"], 0) + 1
        print("actors    " + ", ".join(f"{a}={n}" for a, n in sorted(actors.items())))
        refusals = [e for e in entries if e["event"] == "PLAN_REFUSED"]
        print(f"refusals  {len(refusals)}")
    print()
    print(("PASS  " if ok else "FAIL  ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
