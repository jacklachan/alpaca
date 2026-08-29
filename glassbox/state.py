"""Atomic, fail-closed JSON persistence for trading safety state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class StateError(RuntimeError):
    """Base class for durable safety-state failures."""


class StateCorrupt(StateError):
    """A state file exists but cannot be parsed or validated."""


class StateWriteError(StateError):
    """A durable atomic state replacement failed."""


def read_json(path: str | Path, *, default: T,
              validate: Callable[[Any], T]) -> T:
    """Read and validate JSON; only a missing file receives `default`."""
    target = Path(path)
    if not target.exists():
        return default
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return validate(raw)
    except StateCorrupt:
        raise
    except Exception as exc:
        raise StateCorrupt(f"{target}: corrupt safety state: {exc}") from exc


def _fsync_parent(path: Path) -> None:
    """Persist the directory entry where the platform permits directory fsync."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_json(path: str | Path, value: Any) -> None:
    """Write complete JSON through a same-directory fsync-and-replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", delete=False,
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp") as fh:
            temporary = Path(fh.name)
            json.dump(value, fh, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, target)
        temporary = None
        _fsync_parent(target.parent)
    except Exception as exc:
        raise StateWriteError(f"{target}: atomic state write failed: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
