"""`hj ctl status --json` must stay fast: the Claude Code mod runs it every second."""

import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest


def installed_hj() -> str:
    beside = Path(sys.executable).parent / "hj"
    found = str(beside) if beside.exists() else shutil.which("hj")
    if not found:
        pytest.skip("hj isn't installed")
    return found


def test_ctl_status_is_fast_with_no_playerd(home):
    hj = installed_hj()
    env = {**os.environ, "HOME": str(home)}
    subprocess.run([hj, "ctl", "status", "--json"], env=env, capture_output=True)  # warm the disk cache
    times = []
    for _ in range(10):
        t = time.perf_counter()
        r = subprocess.run([hj, "ctl", "status", "--json"], env=env, capture_output=True, text=True)
        times.append(time.perf_counter() - t)
        assert r.returncode == 0
        assert json.loads(r.stdout)["state"] == "idle"
    assert statistics.median(times) < 0.150, f"median {statistics.median(times) * 1000:.0f} ms"


def test_ctl_never_loads_typer_rich_or_mcp(home):
    code = (
        "import sys\n"
        "sys.argv = ['hj', 'ctl', 'status', '--json']\n"
        "from hodljuice_cli.entry import main\n"
        "try:\n    main()\nexcept SystemExit:\n    pass\n"
        "print(sorted({m.split('.')[0] for m in sys.modules} & {'typer', 'rich', 'mcp', 'click', 'httpx', 'anyio'}))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
    assert r.stdout.strip().splitlines()[-1] == "[]", r.stdout + r.stderr


def test_fortune_is_silent_and_quick_when_the_server_is_down(home):
    hj = installed_hj()
    env = {**os.environ, "HOME": str(home), "HODLJUICE_MCP_URL": "http://127.0.0.1:9/mcp"}
    t = time.perf_counter()
    r = subprocess.run([hj, "fortune"], env=env, capture_output=True, text=True, timeout=10)
    assert time.perf_counter() - t < 3.5
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
