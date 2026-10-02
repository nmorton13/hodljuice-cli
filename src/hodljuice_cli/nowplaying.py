"""The terminal now-playing screen (`hj play`, `hj now`, `--play`, `hj radio`, `hj morning`).

Keys are read from /dev/tty, not stdin, so `… | fzf | hj play` still gets them.
macOS and Linux only (termios).
"""

import os
import select
import termios
import time
import tty
import webbrowser

from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from hodljuice_cli import ctl
from hodljuice_cli.render import console
from hodljuice_cli.sanitize import clean, safe_url

KEYS_HELP = "space pause · n next · ←/→ 15 s · s save · o open · d detach · q quit"


def progress_bar(position, duration, width: int = 24) -> str:
    if not duration or position is None:
        return "─" * width
    filled = max(0.0, min(1.0, position / duration)) * width
    full = int(filled)
    bar = "━" * full
    if full < width:
        bar += "╸" if filled - full >= 0.5 else "─"
        bar += "─" * (width - full - 1)
    return bar


def render(st: dict, message: str = "") -> Panel:
    body = Text()
    if st.get("state") in (None, "idle"):
        body.append("Nothing playing.", style="dim")
    else:
        icon = "▶" if st["state"] == "playing" else "⏸"
        body.append(f"{icon} ", style="green" if st["state"] == "playing" else "yellow")
        body.append(clean(st.get("title")) or "Unknown episode", style="bold")
        meta = " · ".join(x for x in (clean(st.get("podcast")), clean(st.get("published"))) if x)
        if meta:
            body.append("\n  " + meta, style="dim")
        body.append(f"\n  {ctl.fmt_time(st.get('position'))} ")
        body.append(progress_bar(st.get("position"), st.get("duration")), style="cyan")
        body.append(f" {ctl.fmt_time(st.get('duration'))}")
        radio = st.get("radio")
        if radio is not None:
            filters = ", ".join(f"{k} {clean(str(v))}" for k, v in radio.items()) or "anything"
            body.append(f"\n  📻 radio: {filters}", style="magenta")
    if message:
        body.append("\n  " + clean(message), style="italic")
    body.append("\n\n" + KEYS_HELP, style="dim")
    return Panel(body, title="HodlJuice", title_align="left", expand=False, width=min(console.width, 80))


class _Keys:
    """cbreak-mode key reader on /dev/tty."""

    def __init__(self):
        self.fd = os.open("/dev/tty", os.O_RDONLY)
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def read(self, timeout: float) -> str | None:
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        ch = os.read(self.fd, 1)
        if ch == b"\x1b":
            seq = b""
            while select.select([self.fd], [], [], 0.02)[0] and len(seq) < 4:
                seq += os.read(self.fd, 1)
            return {b"[C": "right", b"[D": "left", b"OC": "right", b"OD": "left"}.get(seq, "esc")
        return ch.decode(errors="ignore")

    def close(self):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        os.close(self.fd)


def _status() -> dict:
    try:
        st = ctl.request({"cmd": "status"}, timeout=2.0)
    except Exception:
        st = None
    return st or dict(ctl.IDLE_STATUS)


def _do(cmd: str, **extra) -> str:
    try:
        resp = ctl.request({"cmd": cmd, **extra}, timeout=30.0)
    except Exception as e:
        return str(e)
    if resp is None:
        return "Nothing playing."
    return resp.get("message") or ("" if resp.get("ok") else resp.get("error", ""))


def run_screen() -> None:
    """Show the screen until the player goes idle, `q` (stop) or `d` (detach)."""
    try:
        keys = _Keys()
    except OSError:
        st = _status()
        console.print(Text(ctl.short_line(st) or "Nothing playing."))
        console.print("No terminal for keys; playing in the background. `hj now` shows it, `hj ctl stop` stops it.",
                      style="dim")
        return
    message = ""
    message_until = 0.0
    detached = False
    seen_playing = False
    idle_polls = 0
    try:
        with Live(render(_status()), console=console, refresh_per_second=4, transient=False) as live:
            while True:
                key = keys.read(0.5)
                if key is not None:
                    msg = None
                    if key == " ":
                        msg = _do("pause")
                    elif key == "n":
                        msg = _do("next")
                    elif key == "right":
                        msg = _do("seek", seconds=15) or "+15 s"
                    elif key == "left":
                        msg = _do("seek", seconds=-15) or "−15 s"
                    elif key == "s":
                        msg = _do("save")
                    elif key == "o":
                        st = _status()
                        url = safe_url(st.get("play_url")) or safe_url(st.get("audio_url"))
                        if url:
                            webbrowser.open(url)
                            msg = "Opened in your browser."
                        else:
                            msg = "Nothing to open."
                    elif key in ("q", "esc"):
                        _do("stop")
                        live.update(render(_status(), "Stopped."))
                        break
                    elif key == "d":
                        detached = True
                        break
                    if msg is not None:
                        message, message_until = msg, time.monotonic() + 3
                st = _status()
                if st.get("state") in ("playing", "paused"):
                    seen_playing = True
                    idle_polls = 0
                else:
                    idle_polls += 1
                # Leave once playback has finished (or never started after ~5 s).
                if (seen_playing and idle_polls >= 2) or idle_polls >= 10:
                    live.update(render(st, "Finished."))
                    break
                live.update(render(st, message if time.monotonic() < message_until else ""))
    except KeyboardInterrupt:
        detached = True
    finally:
        keys.close()
    if detached:
        state = "paused" if _status().get("state") == "paused" else "playing"
        console.print(f"Left it {state} in the background. `hj now` brings this screen back; "
                      "`hj ctl stop` stops it.", style="dim")
