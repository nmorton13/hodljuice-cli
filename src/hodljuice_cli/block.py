"""`hj block`: block height → date (via mempool.space)."""

import datetime as dt
import json
import os
import re
import urllib.request

MEMPOOL = os.environ.get("HODLJUICE_MEMPOOL_URL", "https://mempool.space").rstrip("/")
HALVING_INTERVAL = 210_000
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def label(height: int) -> str | None:
    if height == 0:
        return "genesis"
    if height == 57043:
        return "pizza day"
    if height > 0 and height % HALVING_INTERVAL == 0:
        n = height // HALVING_INTERVAL
        return f"the {ORDINALS.get(n, f'{n}th')} halving"
    return None


def _get(path: str, timeout: float = 10.0) -> str:
    req = urllib.request.Request(MEMPOOL + path, headers={"User-Agent": "hodljuice-cli"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(1_000_000).decode()


def block_time(height: int, get=_get) -> int:
    block_hash = get(f"/api/block-height/{height}").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", block_hash):
        raise ValueError(f"unexpected block hash from mempool.space: {block_hash[:80]!r}")
    return int(json.loads(get(f"/api/block/{block_hash}"))["timestamp"])


def height_to_date(height: int, get=_get) -> tuple[dt.date, bool]:
    """(UTC date of the block, estimated?). Heights above the tip are estimated at 10 min/block."""
    if height < 0:
        raise ValueError("Block heights start at 0.")
    tip = int(get("/api/blocks/tip/height").strip())
    if height <= tip:
        ts = block_time(height, get)
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date(), False
    tip_ts = block_time(tip, get)
    ts = tip_ts + (height - tip) * 600
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date(), True


def headline(height: int, date: dt.date, estimated: bool) -> str:
    text = f"Block {height:,} · {date.isoformat()}"
    extra = [x for x in (label(height), "estimated" if estimated else None) if x]
    if extra:
        text += f" ({', '.join(extra)})"
    return text
