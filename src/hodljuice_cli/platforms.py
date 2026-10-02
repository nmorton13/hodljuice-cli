"""Everything that differs between macOS and Linux, in one place."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"

SHELL_BEGIN = "# >>> hodljuice fortune >>>"
SHELL_END = "# <<< hodljuice fortune <<<"
LAUNCHD_LABEL = "app.hodljuice.morning"
CRON_MARK = "# hodljuice morning"


# ---------------------------------------------------------------- shell fortune


def shell_rc() -> Path:
    return Path.home() / (".zshrc" if IS_MAC else ".bashrc")


def shell_block(hj_path: str) -> str:
    # Interactive shells only; never let a failure reach the prompt.
    return (
        f"{SHELL_BEGIN}\n"
        f"[[ $- == *i* ]] && command -v {hj_quote(hj_path)} >/dev/null 2>&1 && "
        f"{hj_quote(hj_path)} fortune 2>/dev/null\n"
        f"{SHELL_END}\n"
    )


def hj_quote(path: str) -> str:
    import shlex

    return shlex.quote(path)


def strip_block(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == SHELL_BEGIN:
            skipping = True
            if out and out[-1] == "\n":  # the blank line install_shell put before the block
                out.pop()
            continue
        if skipping and line.rstrip("\n") == SHELL_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


def install_shell(hj_path: str) -> tuple[Path, bool]:
    """Add the fortune block; returns (rc path, changed)."""
    rc = shell_rc()
    old = rc.read_text() if rc.exists() else ""
    base = strip_block(old)
    if base and not base.endswith("\n"):
        base += "\n"
    new = base + ("\n" if base else "") + shell_block(hj_path)
    if new == old:
        return rc, False
    rc.write_text(new)
    return rc, True


def uninstall_shell() -> tuple[Path, bool]:
    rc = shell_rc()
    if not rc.exists():
        return rc, False
    old = rc.read_text()
    new = strip_block(old)
    if new == old:
        return rc, False
    rc.write_text(new)
    return rc, True


# ---------------------------------------------------------------- speech


def speech_command() -> list[str] | None:
    if IS_MAC:
        return ["say"] if shutil.which("say") else None
    for cmd in ("spd-say", "espeak"):
        if shutil.which(cmd):
            return [cmd]
    return None


def speak(text: str) -> None:
    cmd = speech_command()
    if not cmd:
        return
    try:
        subprocess.run([*cmd, text], timeout=60, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- audio availability


def headless_reason() -> str | None:
    """Why sound probably won't reach anyone, or None if it should."""
    if IS_MAC:
        return None
    if os.environ.get("SSH_CONNECTION") and not (os.environ.get("PULSE_SERVER") or os.environ.get("DISPLAY")):
        return "This is an SSH session; sound plays on the remote machine, not here."
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    has_server = runtime and any((Path(runtime) / p).exists() for p in ("pulse/native", "pipewire-0"))
    if not has_server and not Path("/dev/snd").exists():
        return "No sound server or sound card found; mpv may have nowhere to play."
    return None


# ---------------------------------------------------------------- morning schedule


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def launchd_plist(hj_path: str, hour: int, minute: int) -> str:
    days = "\n".join(
        f"    <dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>{hour}</integer>"
        f"<key>Minute</key><integer>{minute}</integer></dict>"
        for d in range(1, 6)
    )
    from xml.sax.saxutils import escape

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{escape(hj_path)}</string><string>morning</string><string>--background</string></array>
  <key>StartCalendarInterval</key>
  <array>
{days}
  </array>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict>
</plist>
"""


def cron_line(hj_path: str, hour: int, minute: int) -> str:
    return f"{minute} {hour} * * 1-5 {hj_quote(hj_path)} morning --background >/dev/null 2>&1 {CRON_MARK}"


def morning_install_preview(hj_path: str, hour: int, minute: int) -> tuple[str, str]:
    """(where, what) for showing the user before asking."""
    if IS_MAC:
        return str(launchd_plist_path()), launchd_plist(hj_path, hour, minute)
    return "your crontab", cron_line(hj_path, hour, minute)


def morning_install(hj_path: str, hour: int, minute: int) -> str:
    if IS_MAC:
        p = launchd_plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.write_text(launchd_plist(hj_path, hour, minute))
        subprocess.run(["launchctl", "load", str(p)], capture_output=True, check=True)
        return f"Installed {p}"
    current = _crontab()
    lines = [ln for ln in current.splitlines() if CRON_MARK not in ln]
    lines.append(cron_line(hj_path, hour, minute))
    _write_crontab("\n".join(lines) + "\n")
    return "Added a line to your crontab."


def morning_uninstall() -> str:
    if IS_MAC:
        p = launchd_plist_path()
        if not p.exists():
            return "Nothing to remove."
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.unlink()
        return f"Removed {p}"
    current = _crontab()
    lines = [ln for ln in current.splitlines() if CRON_MARK not in ln]
    if len(lines) == len(current.splitlines()):
        return "Nothing to remove."
    _write_crontab("\n".join(lines) + ("\n" if lines else ""))
    return "Removed the line from your crontab."


def _crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _write_crontab(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)
