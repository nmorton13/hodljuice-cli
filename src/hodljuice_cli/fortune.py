"""`hj fortune`: one random episode, printed at shell startup.

It must never slow down or break a shell. So: print the fortune fetched last
time (instant), then refresh the cache in a detached background process. Only
the very first run, with no cache yet, fetches inline, under a 2-second limit.
Any error prints nothing and exits 0. No Typer, no Rich.
"""

import json
import os
import signal
import subprocess
import sys

from hodljuice_cli import paths
from hodljuice_cli.sanitize import clean, safe_url

TIMEOUT = 2.0


def _cache():
    return paths.runtime_dir() / "fortune.json"


def _fetch() -> dict:
    import asyncio

    from hodljuice_cli.api import call_once

    return asyncio.run(asyncio.wait_for(call_once("random_episode"), TIMEOUT))


def _store(ep: dict) -> None:
    paths.ensure_runtime_dir()
    tmp = _cache().with_suffix(".tmp")
    tmp.write_text(json.dumps(ep))
    os.replace(tmp, _cache())


def format_fortune(ep: dict, color: bool = True) -> str:
    def c(code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if color else text

    title = clean(ep.get("title"))[:160]
    if not title:
        return ""
    show = clean(ep.get("podcast"))[:60]
    year = clean(ep.get("published"))[:4]
    snippet = clean(ep.get("snippet"))
    if len(snippet) > 200:
        snippet = snippet[:200].rsplit(" ", 1)[0] + "…"
    url = safe_url(ep.get("play_url")) or ""
    lines = [c("1;33", "🍺 " + title)]
    meta = " · ".join(x for x in (show, year) if x)
    if meta:
        lines.append("   " + c("2", meta))
    if snippet:
        lines.append("   " + c("3", f"“{snippet}”"))
    if url:
        lines.append("   " + c("36", url))
    return "\n".join(lines)


def _refresh_in_background() -> None:
    subprocess.Popen(
        [sys.executable, "-m", "hodljuice_cli.entry", "fortune", "--refresh"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )


def _timeout(*_):
    raise TimeoutError


def main(argv: list[str]) -> int:
    try:
        signal.signal(signal.SIGALRM, _timeout)
        signal.setitimer(signal.ITIMER_REAL, TIMEOUT + 0.5)  # backstop for imports and DNS
        if "--refresh" in argv:
            _store(_fetch())
            return 0
        try:
            ep = json.loads(_cache().read_text())
        except (OSError, ValueError):
            ep = _fetch()
            _store(ep)
        else:
            _refresh_in_background()
        text = format_fortune(ep, color=sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
        if text:
            print(text)
    except BaseException:
        pass
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        except Exception:
            pass
    return 0
