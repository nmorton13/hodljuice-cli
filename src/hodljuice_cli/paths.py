"""Where hj keeps its socket, state and saved episodes. Standard library only."""

import os
import stat
import sys
import tempfile
from pathlib import Path

# macOS limits AF_UNIX paths to 104 bytes, Linux to 108.
_MAX_SOCK = 100


def runtime_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "hodljuice"
    else:
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        base = Path(xdg) / "hodljuice" if xdg else Path.home() / ".cache" / "hodljuice"
    if len(str(base / "playerd.sock")) > _MAX_SOCK:
        base = Path(tempfile.gettempdir()) / f"hodljuice-{os.getuid()}"
    return base


def ensure_runtime_dir() -> Path:
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The fallback lives in the shared temp dir: refuse one someone else made, or a symlink.
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise PermissionError(f"{d} isn't a folder owned by you; remove it and try again")
    os.chmod(d, 0o700)
    return d


def playerd_socket() -> Path:
    return runtime_dir() / "playerd.sock"


def mpv_socket() -> Path:
    return runtime_dir() / "mpv.sock"


def playerd_lock() -> Path:
    return runtime_dir() / "playerd.lock"


def playerd_log() -> Path:
    return runtime_dir() / "playerd.log"


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) / "hodljuice" if xdg else Path.home() / ".config" / "hodljuice"


def saved_file() -> Path:
    return config_dir() / "saved.json"
