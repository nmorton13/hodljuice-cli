"""Against the real server. Opt in: HJ_LIVE=1 pytest -m live"""

import asyncio
import datetime as dt

import pytest

from hodljuice_cli import api, timemachine as tm

pytestmark = pytest.mark.live


def call(tool, **args):
    return asyncio.run(api.call_once(tool, **args))


def test_each_tool_once():
    assert call("search_episodes", query="mining", limit=2)["episodes"]
    assert call("random_episode", year=2019)["title"]
    assert "episodes" in call("latest_episodes", days=7, limit=2)
    assert call("topic_episodes", topic="climate_energy", limit=2)["episodes"]
    people = call("list_people")["people"]
    assert people
    assert "episodes" in call("person_episodes", name=people[0]["name"], limit=2)
    assert call("daily_pint")["title"]
    assert call("weekly_brew")["title"]
    ep = call("random_episode")
    if ep.get("id"):
        assert call("get_episode", episode_id=ep["id"])["title"]
    with pytest.raises(api.HJError, match="Sunday"):
        call("daily_pint", date="2026-09-27")


@pytest.mark.parametrize("name", ["halving2024", "ftx", "covid"])
def test_timemachine_windows(name):
    date, _ = tm.parse_date(name)

    async def go():
        async with api.Session() as s:
            # Page by hand so we can check each page.
            args = tm.window_args(date, 3)
            pages, offset = [], 0
            while True:
                page = (await s.call("latest_episodes", limit=tm.PAGE, offset=offset, **args))["episodes"]
                pages.append(page)
                if len(page) < tm.PAGE:
                    break
                offset += tm.PAGE
            return pages

    pages = asyncio.run(go())
    eps = [e for p in pages for e in p]
    assert len(eps) >= 1
    lo, hi = date - dt.timedelta(days=3), date + dt.timedelta(days=3)
    assert all(lo.isoformat() <= e["published"] <= hi.isoformat() for e in eps)
    ids = [e["id"] for e in eps if e.get("id")]
    assert len(ids) == len(set(ids)), "an episode appeared on two pages"
    assert len(pages[-1]) < tm.PAGE and all(len(p) == tm.PAGE for p in pages[:-1])
