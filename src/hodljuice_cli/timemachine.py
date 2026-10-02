"""`hj timemachine`: what people were saying around a date."""

import datetime as dt

PAGE = 25
MAX_OFFSET = 500
WIDEN = (3, 14, 45)  # ±days; latest_episodes allows days ≤ 90
MIN_RESULTS = 3

NAMED_DATES = {
    "mtgox": ("2014-02-24", "Mt. Gox halts withdrawals for good"),
    "halving2016": ("2016-07-09", "the 2nd halving"),
    "segwit": ("2017-08-24", "SegWit activates"),
    "ath2017": ("2017-12-17", "the 2017 all-time high"),
    "covid": ("2020-03-12", "the COVID crash"),
    "halving2020": ("2020-05-11", "the 3rd halving"),
    "elsalvador": ("2021-09-07", "Bitcoin becomes legal tender in El Salvador"),
    "ath2021": ("2021-11-10", "the 2021 all-time high"),
    "taproot": ("2021-11-14", "Taproot activates"),
    "luna": ("2022-05-09", "Terra/Luna collapses"),
    "ftx": ("2022-11-08", "FTX collapses"),
    "etf": ("2024-01-10", "US spot ETFs approved"),
    "halving2024": ("2024-04-20", "the 4th halving"),
}


def parse_date(value: str) -> tuple[dt.date, str | None]:
    """A YYYY-MM-DD date or a named date → (date, label)."""
    key = value.strip().lower()
    if key in NAMED_DATES:
        iso, label = NAMED_DATES[key]
        return dt.date.fromisoformat(iso), label
    try:
        return dt.date.fromisoformat(value.strip()), None
    except ValueError:
        names = ", ".join(NAMED_DATES)
        raise ValueError(f"{value!r} isn't a YYYY-MM-DD date or a named date ({names}).") from None


def window_args(date: dt.date, window: int) -> dict:
    """±window days around date, as latest_episodes/search_episodes arguments."""
    return {"until": (date + dt.timedelta(days=window)).isoformat(), "days": 2 * window}


def _key(ep: dict) -> str:
    return ep.get("id") or ep.get("audio_url") or f"{ep.get('podcast')}|{ep.get('title')}"


async def fetch_window(session, date: dt.date, window: int, about: str | None) -> list[dict]:
    args = window_args(date, window)
    if about:
        page = await session.call("search_episodes", query=about, limit=PAGE, **args)
        episodes = page.get("episodes") or []
    else:
        episodes, offset = [], 0
        while offset <= MAX_OFFSET:
            page = await session.call("latest_episodes", limit=PAGE, offset=offset, **args)
            batch = page.get("episodes") or []
            episodes.extend(batch)
            if len(batch) < PAGE:
                break
            offset += PAGE
    seen, unique = set(), []
    for ep in episodes:
        k = _key(ep)
        if k not in seen:
            seen.add(k)
            unique.append(ep)
    unique.sort(key=lambda e: (e.get("published") or "", e.get("podcast") or "", e.get("title") or ""))
    return unique


async def travel(session, date: dt.date, window: int, about: str | None, on_widen=None):
    """Episodes around `date`, widening ±3 → ±14 → ±45 when there are fewer than 3.

    Returns (episodes, window actually used).
    """
    window = max(0, min(window, WIDEN[-1]))
    episodes = await fetch_window(session, date, window, about)
    for wider in WIDEN:
        if len(episodes) >= MIN_RESULTS or wider <= window:
            continue
        if on_widen:
            on_widen(window, wider, len(episodes))
        window = wider
        episodes = await fetch_window(session, date, window, about)
    return episodes, window
