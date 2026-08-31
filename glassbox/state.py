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


def read_json(path: str | Path, *, default: T, validate: Callable[[Any], T]) -> T:
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
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        ) as fh:
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


class StateLocked(StateError):
    """Another live process already owns this state directory."""


def _pid_is_alive(pid: int) -> bool:
    """Return false only when the operating system proves the PID is absent.

    CPython's ``os.kill(pid, 0)`` can terminate the interpreter for some
    invalid PIDs on Windows. OpenProcess is a bounded existence probe there;
    every result other than ERROR_INVALID_PARAMETER fails closed as alive.
    """
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() != error_invalid_parameter
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


class ProcessLock:
    """Exclusive ownership of one state directory.

    Two schedulers against one account is not a degraded mode, it is two
    independent decision loops reconciling against each other's orders. Refusing
    to start is strictly safer, so acquisition is exclusive-create and failure
    is fatal by default.

    A lock left behind by a killed process is detected by probing the recorded
    pid, not by age: a stale file must not outlive its owner, and a live owner
    must never be evicted because it was slow.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._descriptor: int | None = None

    def _owner_alive(self) -> bool:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(raw["pid"])
        except Exception:
            # Unreadable lock: treat as held. Fail closed.
            return True
        if pid == os.getpid():
            return True
        return _pid_is_alive(pid)

    def acquire(self) -> ProcessLock:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateLocked(f"{self.path}: cannot create lock directory: {exc}") from exc
        try:
            self._descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if self._owner_alive():
                raise StateLocked(
                    f"{self.path}: another scheduler already owns this state directory"
                ) from None
            # The recorded owner is gone. Reclaim, then retry exactly once.
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise StateLocked(f"{self.path}: cannot reclaim stale lock: {exc}") from exc
            try:
                self._descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise StateLocked(f"{self.path}: lost the race to reclaim a stale lock") from None
            except OSError as exc:
                raise StateLocked(f"{self.path}: cannot acquire reclaimed lock: {exc}") from exc
        except OSError as exc:
            raise StateLocked(f"{self.path}: cannot acquire runtime lock: {exc}") from exc
        try:
            payload = json.dumps({"pid": os.getpid()}).encode("utf-8")
            os.write(self._descriptor, payload)
            os.fsync(self._descriptor)
        except OSError as exc:
            try:
                os.close(self._descriptor)
            finally:
                self._descriptor = None
                try:
                    self.path.unlink()
                except OSError:
                    pass
            raise StateLocked(f"{self.path}: cannot persist runtime lock: {exc}") from exc
        return self

    def release(self) -> None:
        if self._descriptor is not None:
            try:
                os.close(self._descriptor)
            finally:
                self._descriptor = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> ProcessLock:
        return self.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self.release()
