"""Turning server data into queue items. Standard library only."""

import re
from urllib.parse import parse_qs, urlsplit

from hodljuice_cli.sanitize import clean, safe_url

EPISODE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_PLAY_URL_PATH = re.compile(r"^/e/([A-Za-z0-9_-]{11})/?$")
_MAX_TEXT = 300


def _text(value) -> str:
    return clean(value)[:_MAX_TEXT]


def item_from_episode(ep: dict) -> dict | None:
    """Queue item from an Episode or BrewEpisode dict, or None if it has no playable audio."""
    if not isinstance(ep, dict):
        return None
    audio = safe_url(ep.get("audio_url"))
    if not audio:
        return None
    ep_id = ep.get("id")
    duration = ep.get("duration_seconds")
    return {
        "id": ep_id if isinstance(ep_id, str) and EPISODE_ID_RE.fullmatch(ep_id) else None,
        "title": _text(ep.get("title")) or "Untitled episode",
        "podcast": _text(ep.get("podcast") or ep.get("show")),
        "published": _text(ep.get("published") or ep.get("date"))[:10],
        "play_url": safe_url(ep.get("play_url")),
        "audio_url": audio,
        "duration": duration if isinstance(duration, (int, float)) and duration > 0 else None,
    }


def item_from_url(url: str) -> dict | None:
    """Queue item for a URL: a HodlJuice player link (`?episodeUrl=…`) or a bare audio URL."""
    url = safe_url(url)
    if not url:
        return None
    shared = _from_player_link(url)
    if shared:
        return shared
    name = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or urlsplit(url).hostname
    return {
        "id": None,
        "title": _text(name),
        "podcast": "",
        "published": "",
        "play_url": None,
        "audio_url": url,
        "duration": None,
    }


def episode_id_from_play_url(url: str) -> str | None:
    """`https://hodljuice.app/e/<id>` → id."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in ("hodljuice.app", "www.hodljuice.app"):
        return None
    m = _PLAY_URL_PATH.match(parts.path)
    return m.group(1) if m else None


def item_key(item: dict) -> str:
    return item.get("id") or item.get("audio_url") or ""


def _from_player_link(url: str) -> dict | None:
    """`https://hodljuice.app/?episodeUrl=…&episodeTitle=…`, as the Daily Pint's play_url is."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in ("hodljuice.app", "www.hodljuice.app"):
        return None
    q = parse_qs(parts.query)
    audio = safe_url((q.get("episodeUrl") or [None])[0])
    if not audio:
        return None

    def first(key):
        return _text((q.get(key) or [""])[0])

    return {
        "id": None,
        "title": first("episodeTitle") or "Untitled episode",
        "podcast": first("trackName"),
        "published": first("episodeDate")[:10],
        "play_url": url,
        "audio_url": audio,
        "duration": None,
    }
