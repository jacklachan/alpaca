from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "deploy" / "setup.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _bash() -> str:
    if os.name == "nt":
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if git_bash.exists():
            return str(git_bash)
    found = shutil.which("bash")
    if found:
        return found
    pytest.skip("bash is unavailable")


def _shell_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    return subprocess.check_output([_bash(), "-lc", f"cygpath -u {str(path)!r}"], text=True).strip()


def _write_stub(path: Path, body: str = "") -> None:
    path.write_text(
        f'#!/usr/bin/env bash\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$CALL_LOG"\n{body}\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


@pytest.fixture()
def deploy_harness(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    log.touch()

    for command in ("apt-get", "useradd", "cp", "python3.12", "pip", "chown", "chmod", "systemctl"):
        _write_stub(bin_dir / command)
    _write_stub(bin_dir / "id", "exit 0")
    _write_stub(
        bin_dir / "git",
        'if [[ "$*" == *"rev-parse HEAD"* ]]; then\n'
        '  printf "%s\\n" "${FAKE_HEAD:-$EXPECTED_SHA}"\n'
        "fi",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{_shell_path(bin_dir)}:/usr/bin:/bin",
            "CALL_LOG": _shell_path(log),
            "EXPECTED_SHA": SHA,
            "GLASSBOX_APP": _shell_path(tmp_path / "app"),
            "GLASSBOX_PYTHON_BIN": "python3.12",
            "GLASSBOX_PIP_BIN": "pip",
            "GLASSBOX_GIT_BIN": f"{_shell_path(bin_dir / 'git')}",
            "GLASSBOX_CP_BIN": f"{_shell_path(bin_dir / 'cp')}",
            "GLASSBOX_CHOWN_BIN": f"{_shell_path(bin_dir / 'chown')}",
            "GLASSBOX_CHMOD_BIN": f"{_shell_path(bin_dir / 'chmod')}",
            "GLASSBOX_SYSTEMCTL_BIN": f"{_shell_path(bin_dir / 'systemctl')}",
        }
    )
    return env, log


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), _shell_path(SETUP), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("args", [[], ["abc123"], ["z" * 40]])
def test_setup_refuses_missing_or_invalid_sha_before_mutation(
    deploy_harness, args: list[str]
) -> None:
    env, log = deploy_harness

    result = _run(args, env)

    assert result.returncode != 0
    assert log.read_text(encoding="utf-8") == ""


def test_setup_fetches_and_verifies_exact_detached_commit(deploy_harness) -> None:
    env, log = deploy_harness

    result = _run([SHA], env)

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert f"fetch --depth 1 origin {SHA}" in calls
    assert f"checkout --detach {SHA}" in calls
    assert "rev-parse HEAD" in calls
    assert "pull" not in calls
    assert "clone" not in calls
    assert "pip install --quiet --requirement" in calls
    assert calls.count("requirements.lock") == 1


def test_setup_stops_when_checked_out_head_does_not_match(deploy_harness) -> None:
    env, log = deploy_harness
    env["FAKE_HEAD"] = "f" * 40

    result = _run([SHA], env)

    assert result.returncode != 0
    calls = log.read_text(encoding="utf-8")
    assert "rev-parse HEAD" in calls
    assert "pip install" not in calls


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-dev.lock"])
def test_lock_files_contain_only_exact_versions(filename: str) -> None:
    lines = (ROOT / filename).read_text(encoding="utf-8").splitlines()
    requirements = [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith(("#", "-r "))
    ]

    assert requirements
    assert all(
        re.fullmatch(r"[A-Za-z0-9_.-]+==[^;\s]+(?:\s*;\s*.+)?", requirement)
        for requirement in requirements
    )


def test_requirements_entrypoint_uses_runtime_lock() -> None:
    assert (ROOT / "requirements.txt").read_text(encoding="utf-8").strip() == (
        "-r requirements.lock"
    )
