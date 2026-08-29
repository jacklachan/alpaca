"""Prove that systemd's EnvironmentFile parser and python-dotenv disagree, and
that on a systemd host the disagreement wins.

systemd treats '#' as a comment ONLY at the start of a line. python-dotenv strips
inline comments from unquoted values. So a .env that works when you run
`python main.py` by hand can hand the service a mangled value under systemd --
and because load_dotenv() does not override variables that are already set, the
mangled value is the one the process actually sees.

Run:  python tools/env_parity.py [path-to-.env]
Exit: 0 = parity, 1 = mismatch that will break the service.
"""

from __future__ import annotations

import os
import sys

from dotenv import dotenv_values

SENSITIVE = ("KEY", "SECRET", "TOKEN", "WEBHOOK")


def systemd_values(path: str) -> dict[str, str]:
    """Replicate systemd EnvironmentFile semantics."""
    out: dict[str, str] = {}
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        # systemd strips matching surrounding quotes, nothing else.
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def redact(k: str, v: str) -> str:
    return "<redacted>" if any(s in k.upper() for s in SENSITIVE) else v


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ".env"
    if not os.path.exists(path):
        print(f"no such file: {path}")
        return 1

    sysd = systemd_values(path)
    dot = {k: (v or "") for k, v in dotenv_values(path).items()}

    mismatches = []
    for key in sorted(set(sysd) | set(dot)):
        s, d = sysd.get(key, "<missing>"), dot.get(key, "<missing>")
        if s != d:
            mismatches.append((key, s, d))

    dupes = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(open(path, encoding="utf-8"), 1):
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k in seen:
                dupes.append((k, seen[k], i))
            seen[k] = i

    for k, first, second in dupes:
        print(f"DUPLICATE  {k}: defined on line {first} and line {second} "
              f"(last one wins in both parsers)")

    if not mismatches:
        print(f"PARITY OK  {path}: systemd and python-dotenv agree on "
              f"all {len(dot)} variables.")
        return 0

    print(f"MISMATCH   {path}: {len(mismatches)} variable(s) parse differently.\n")
    for key, s, d in mismatches:
        print(f"  {key}")
        print(f"    systemd sees : [{redact(key, s)}]")
        print(f"    dotenv  sees : [{redact(key, d)}]")
    print("\nOn a systemd host the systemd value wins: load_dotenv() does not")
    print("override variables that are already set in the environment.")
    print("Fix: move every '#' comment onto its own line in the .env file.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
