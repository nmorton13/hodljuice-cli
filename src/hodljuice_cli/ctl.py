"""`hj ctl`: talk to playerd over its socket.

Standard library only. This module must never import Typer, Rich or mcp,
directly or through another module: `hj ctl status --json` has to finish in
under 150 ms because the Claude Code mod polls it every second.
"""

import argparse
import json
import socket
import subprocess
import sys
import time

from hodljuice_cli import paths
from hodljuice_cli.sanitize import clean
from hodljuice_cli.topics import TOPICS

IDLE_STATUS = {
    "state": "idle",
    "title": None,
    "podcast": None,
    "published": None,
    "play_url": None,
    "position": None,
    "duration": None,
    "radio": None,
}

# Commands that start playerd when it isn't running.
_STARTS_PLAYERD = {"play", "radio"}


class PlayerdError(Exception):
    pass


def _send(sock_path, request: dict, timeout: float) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall(json.dumps(request).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if not buf:
        raise PlayerdError("playerd closed the connection")
    return json.loads(buf)


def start_playerd() -> None:
    paths.ensure_runtime_dir()
    log = open(paths.playerd_log(), "ab")
    subprocess.Popen(
        [sys.executable, "-m", "hodljuice_cli.entry", "playerd"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
        close_fds=True,
    )
    log.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            _send(paths.playerd_socket(), {"cmd": "ping"}, 0.5)
            return
        except (OSError, ValueError, PlayerdError):
            time.sleep(0.05)
    raise PlayerdError(f"playerd didn't start; see {paths.playerd_log()}")


def request(req: dict, timeout: float = 5.0, start: bool = False) -> dict | None:
    """Send one request. Returns None when playerd isn't running and `start` is False."""
    sock = paths.playerd_socket()
    try:
        return _send(sock, req, timeout)
    except (FileNotFoundError, ConnectionRefusedError):
        if not start:
            return None
    start_playerd()
    return _send(sock, req, timeout)


def fmt_time(seconds) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def short_line(st: dict) -> str:
    if st.get("state") in (None, "idle"):
        return ""
    icon = "▶" if st["state"] == "playing" else "⏸"
    parts = [_trim(clean(st.get("title")) or "Unknown episode", 50)]
    if st.get("podcast"):
        parts.append(_trim(clean(st["podcast"]), 24))
    parts.append(f"{fmt_time(st.get('position'))}/{fmt_time(st.get('duration'))}")
    return f"{icon} " + " · ".join(parts)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hj ctl", description="Control the hj background player.")
    sub = p.add_subparsers(dest="cmd", required=True)
    play = sub.add_parser("play", help="play an episode id or http(s) URL")
    play.add_argument("target")
    play.add_argument("--append", action="store_true", help="add to the queue instead of playing now")
    sub.add_parser("pause", help="toggle pause")
    sub.add_parser("next", help="next in queue (or next radio episode)")
    sub.add_parser("prev", help="previous in queue")
    seek = sub.add_parser("seek", help="seek ±SECONDS")
    seek.add_argument("seconds", type=float)
    sub.add_parser("stop", help="stop and clear the queue")
    sub.add_parser("save", help="save the current episode")
    sub.add_parser("open", help="open the current episode in your browser")
    radio = sub.add_parser("radio", help="start an endless station")
    add_radio_args(radio)
    status = sub.add_parser("status", help="what's playing")
    status.add_argument("--json", action="store_true")
    status.add_argument("--short", action="store_true", help="one line, empty when idle")
    return p


def add_radio_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--year", type=int)
    p.add_argument("--show")
    p.add_argument("--topic", choices=sorted(TOPICS))
    p.add_argument("--days", type=int)


def radio_filters(year=None, show=None, topic=None, days=None) -> dict:
    return {k: v for k, v in (("year", year), ("show", show), ("topic", topic), ("days", days)) if v}


def _open_current() -> int:
    from hodljuice_cli.sanitize import safe_url

    try:
        st = request({"cmd": "status"}, timeout=2.0) or {}
    except (OSError, ValueError, PlayerdError):
        st = {}
    url = safe_url(st.get("play_url")) or safe_url(st.get("audio_url"))
    if not url:
        print("Nothing playing.", file=sys.stderr)
        return 1
    import webbrowser

    if not webbrowser.open(url):
        print(url)
    return 0


def main(argv: list[str]) -> int:
    # `hj ctl seek -15` must not be read as an option.
    if len(argv) >= 2 and argv[0] == "seek":
        argv = ["seek", "--", *argv[1:]]
    args = _parser().parse_args(argv)
    cmd = args.cmd
    if cmd == "status":
        try:
            st = request({"cmd": "status"}, timeout=2.0) or dict(IDLE_STATUS)
            st.pop("ok", None)
        except (OSError, ValueError, PlayerdError):
            st = dict(IDLE_STATUS)
        if args.json:
            print(json.dumps(st))
        elif args.short:
            line = short_line(st)
            if line:
                print(line)
        else:
            print(short_line(st) or "Nothing playing.")
        return 0

    if cmd == "open":
        return _open_current()

    req: dict = {"cmd": cmd}
    timeout = 5.0
    if cmd == "play":
        req.update(target=args.target, append=args.append)
        timeout = 30.0  # playerd may need to look the id up first
    elif cmd == "seek":
        req["seconds"] = args.seconds
    elif cmd == "radio":
        req["filters"] = radio_filters(args.year, args.show, args.topic, args.days)
        timeout = 30.0
    try:
        resp = request(req, timeout=timeout, start=cmd in _STARTS_PLAYERD)
    except (OSError, ValueError, PlayerdError) as e:
        print(f"hj ctl: {e}", file=sys.stderr)
        return 1
    if resp is None:
        print("Nothing playing.", file=sys.stderr)
        return 1
    if not resp.get("ok"):
        print(f"hj ctl: {clean(resp.get('error', 'failed'))}", file=sys.stderr)
        return 1
    msg = resp.get("message")
    if msg:
        print(clean(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
