"""Cleaning untrusted strings from podcast feeds. Standard library only."""

import re
from urllib.parse import urlsplit

# CSI, OSC (terminated by BEL or ST), and other two-byte ESC sequences.
_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[PX^_][^\x1b]*(?:\x1b\\)?"
    r"|\x1b[@-Z\\-_]?"
    r"|\x9b[0-?]*[ -/]*[@-~]"
    r"|\x9d[^\x07\x9c]*[\x07\x9c]?"
)
# C0 and C1 controls (keeps nothing; newlines and tabs become spaces first).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_SPACES = re.compile(r"[ \t\r\n]+")


def clean(value, multiline: bool = False) -> str:
    """Return `value` as display-safe text: no escapes, no control characters."""
    if value is None:
        return ""
    s = _ANSI.sub("", str(value))
    if multiline:
        lines = [_CONTROL.sub("", _SPACES.sub(" ", ln)).strip() for ln in s.splitlines()]
        return "\n".join(lines).strip()
    return _CONTROL.sub("", _SPACES.sub(" ", s)).strip()


def safe_url(value) -> str | None:
    """Return the URL if it's plain http(s) with a host, else None."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if _CONTROL.search(value) or " " in value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return value
