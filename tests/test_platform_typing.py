"""Cross-platform correctness of platform-conditional code.

A Windows-only branch is only type-checked on a machine where the Windows API
exists. That is how `ctypes.WinDLL` reached CI: mypy passed on the author's
Windows box and failed on every other platform, so the type gate was green
exactly where it could not catch the problem.

These tests pin the two things that prevent a silent recurrence: the guard
must be written so a type checker can narrow it, and the probe must behave
correctly on this machine.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from glassbox.state import _pid_is_alive

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "glassbox" / "state.py"


def test_platform_guards_use_sys_platform_so_a_checker_can_narrow():
    """`os.name == "nt"` is invisible to mypy's platform narrowing, so the
    Windows branch gets analysed on Linux, where its API does not exist.
    `sys.platform == "win32"` is the form mypy understands."""
    tree = ast.parse(STATE.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if (
            isinstance(left, ast.Attribute)
            and left.attr == "name"
            and isinstance(left.value, ast.Name)
            and left.value.id == "os"
        ):
            offenders.append(node.lineno)

    assert not offenders, (
        f"glassbox/state.py uses os.name for a platform guard at lines {offenders}; "
        "use sys.platform so the branch is narrowed off-platform"
    )


def test_the_windows_branch_is_guarded_by_sys_platform():
    source = STATE.read_text(encoding="utf-8")
    assert 'sys.platform == "win32"' in source
    # The Windows API is only reachable inside that guard.
    guard_at = source.index('sys.platform == "win32"')
    assert source.index("ctypes.WinDLL") > guard_at


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_types_check_cleanly_for_every_platform(platform: str):
    """The gate that actually failed. Running it for all three means whoever
    runs the suite sees what CI sees, whatever machine they are on."""
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--platform", platform, "glassbox"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"--platform {platform}:\n{result.stdout[-2000:]}"


# -- the probe itself ----------------------------------------------------------


def test_the_current_process_is_alive():
    assert _pid_is_alive(os.getpid()) is True


def test_a_nonsense_pid_is_not_alive():
    dead = 999_999
    while True:
        try:
            os.kill(dead, 0)
        except ProcessLookupError:
            break
        except OSError:
            pass
        dead -= 1
        if dead < 2:  # pragma: no cover - no free pid, skip rather than hang
            pytest.skip("no free pid found on this machine")
    assert _pid_is_alive(dead) is False


@pytest.mark.parametrize("pid", [0, -1, -999])
def test_an_invalid_pid_fails_closed_as_alive(pid: int):
    """Unknown means occupied. Treating an unparseable pid as dead would let a
    second scheduler claim a state directory that may still be owned."""
    assert _pid_is_alive(pid) is True
