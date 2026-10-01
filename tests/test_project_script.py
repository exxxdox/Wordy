"""Run the unified Windows launcher with fake uv; never install or launch Wordy."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def script_project(tmp_path):
    # A path with spaces catches quoting regressions in both PowerShell and cmd.
    root = tmp_path / "project with spaces"
    root.mkdir()
    for name in ("wordy.ps1", "dev.cmd"):
        shutil.copyfile(PROJECT / name, root / name)
    (root / "uv.cmd").write_text(
        '@echo off\n'
        'echo %*>> "%WORDY_TEST_LOG%"\n'
        'if "%WORDY_TEST_FAIL%"=="%1" exit /b 23\n'
        'exit /b 0\n', encoding="ascii",
    )
    log = tmp_path / "uv.log"
    env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
               WORDY_TEST_LOG=str(log), WORDY_TEST_FAIL="")
    return root, log, env


def run_script(project, *args, via_cmd=False):
    root, log, env = project
    if via_cmd:
        # Execute the real cmd entry point from another directory.
        command = ["cmd.exe", "/d", "/c", str(root / "dev.cmd"), *args]
    else:
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", str(root / "wordy.ps1"), *args]
    result = subprocess.run(command, cwd=root.parent, env=env, capture_output=True,
                            text=True, errors="replace", timeout=30)
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_init_pins_only_when_missing_and_stops_on_pin_failure(script_project):
    root, log, env = script_project
    result, calls = run_script(script_project, "init", "-NoPause", "-PythonVersion", "3.12.9")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["--version", "python pin 3.12.9", "sync"]
    (root / ".python-version").write_text("3.12\n")
    log.unlink()
    result, calls = run_script(script_project, "init", "-NoPause")
    assert result.returncode == 0
    assert calls == ["--version", "sync"]
    (root / ".python-version").unlink()
    log.unlink()
    env["WORDY_TEST_FAIL"] = "python"
    result, calls = run_script(script_project, "init", "-NoPause")
    assert result.returncode == 23
    assert calls == ["--version", "python pin 3.12"]


@pytest.mark.parametrize("args", [(), ("dev",)])
def test_cmd_starts_default_or_explicit_dev_and_preserves_exit_code(script_project, args):
    result, calls = run_script(script_project, *args, via_cmd=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["run python -m wordy"]
    _, log, env = script_project
    log.unlink()
    env["WORDY_TEST_FAIL"] = "run"
    result, calls = run_script(script_project, *args, via_cmd=True)
    assert result.returncode == 23
    assert calls == ["run python -m wordy"]


def test_build_forwards_flags_and_cleans_only_its_artifacts(script_project):
    root, _, _ = script_project
    (root / "build").mkdir()
    (root / "build" / "temporary.txt").write_text("build")
    (root / "Wordy.spec").write_text("spec")
    (root / "keep.txt").write_text("keep")
    result, calls = run_script(script_project, "build", "-Clean", "-NoPause", via_cmd=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[0] == "sync --group dev"
    assert len(calls) == 2
    build = calls[1]
    for expected in ("run python -m PyInstaller", "--onefile", "--windowed", "--clean",
                     "--add-data src/wordy/ui/icons/settings.svg;wordy/ui/icons",
                     "--hidden-import wordy.ui.overlay_widgets", "--collect-all keyring",
                     "--collect-submodules websockets", "src/wordy/__main__.py"):
        assert expected in build
    assert not (root / "build").exists()
    assert not (root / "Wordy.spec").exists()
    assert (root / "keep.txt").read_text() == "keep"


def test_sync_failure_stops_build_and_invalid_action_never_calls_uv(script_project):
    _, log, env = script_project
    env["WORDY_TEST_FAIL"] = "sync"
    result, calls = run_script(script_project, "build", "-NoPause")
    assert result.returncode == 23
    assert calls == ["sync --group dev"]
    log.unlink()
    result, calls = run_script(script_project, "invalid", "-NoPause")
    assert result.returncode != 0
    assert not calls
