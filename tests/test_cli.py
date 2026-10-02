import datetime as dt
import json

import pytest
from typer.testing import CliRunner

from conftest import EVIL_TITLE, episode
from hodljuice_cli import api, cli, platforms, render
from hodljuice_cli.sanitize import clean, safe_url
from hodljuice_cli.topics import TOPICS, page_offset, server_topic

runner = CliRunner()


def invoke(*args, input=None):
    return runner.invoke(cli.app, list(args), input=input)


# ---------------------------------------------------------------- sanitizing


def test_clean_strips_escapes_and_controls():
    assert clean(EVIL_TITLE) == "[red]Evil[/red] red click bell"
    assert clean("a‮b​c\x9b31md") == "abcd"
    assert clean(None) == ""


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://a.example/x.mp3", True),
        ("http://a.example/x.mp3", True),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
        ("https://", False),
        ("https://a.example/\x1b[31m", False),
        ("https://a b/", False),
        (None, False),
    ],
)
def test_safe_url(url, ok):
    assert (safe_url(url) is not None) == ok


# ---------------------------------------------------------------- rendering


def test_table_shows_markup_literally_and_no_escapes(server):
    server.responses["search_episodes"] = {"count": 1, "episodes": [episode(1, title=EVIL_TITLE)]}
    r = invoke("search", "x")
    assert r.exit_code == 0, r.output
    assert "[red]Evil[/red]" in r.output
    assert "\x1b]8" not in r.output and "\x07" not in r.output and "evil.example" not in r.output


def test_plain_is_tab_separated_and_clean(server):
    server.responses["search_episodes"] = {"count": 2, "episodes": [episode(1, title="a\tb\nc"), episode(2, title=EVIL_TITLE)]}
    r = invoke("search", "x", "--plain")
    lines = r.output.splitlines()
    assert lines[0] == "ep000000001\t2024-04-02\tTest Show\ta b c"
    assert all(len(line.split("\t")) == 4 for line in lines)
    assert "\x1b" not in r.output


def test_json_is_the_raw_structured_output(server):
    data = {"count": 1, "episodes": [episode(1, title=EVIL_TITLE)]}
    server.responses["search_episodes"] = data
    r = invoke("search", "x", "--json")
    assert json.loads(r.stdout) == data
    assert "\x1b" not in r.output  # escaped as \u001b


def test_server_errors_show_the_message_not_a_traceback(server):
    server.responses["get_episode"] = api.HJError("No episode with id 'AAAAAAAAAAA'. Ids come from the other tools' results.")
    r = invoke("show", "AAAAAAAAAAA")
    assert r.exit_code == 1
    assert "No episode with id" in r.output and "Traceback" not in r.output


def test_tool_error_prefix_is_removed():
    assert api.tool_error_message("Error executing tool daily_pint: 2026-09-27 is a Sunday") == "2026-09-27 is a Sunday"


# ---------------------------------------------------------------- topics and paging


def test_topic_mapping():
    assert server_topic("climate") == "climate_energy"
    assert server_topic("Money") == "money"
    assert server_topic("climate_energy") == "climate_energy"
    with pytest.raises(ValueError):
        server_topic("weather")
    assert set(TOPICS) == {"money", "climate", "humanitarian"}


def test_page_offset():
    assert page_offset(1, 10) == 0
    assert page_offset(3, 25) == 50
    with pytest.raises(ValueError):
        page_offset(0, 10)


def test_topic_command_maps_name_and_page(server):
    server.responses["topic_episodes"] = {"topic": "Climate & Energy", "page_url": "x", "hand_picked_count": 3, "episodes": []}
    invoke("topic", "climate", "--limit", "5", "--page", "3")
    assert server.calls == [("topic_episodes", {"topic": "climate_energy", "limit": 5, "offset": 10})]


# ---------------------------------------------------------------- Sundays


def test_pint_on_sunday_points_to_the_brew(server):
    server.responses["daily_pint"] = api.HJError("2026-09-27 is a Sunday: Sunday's episode is The Weekly Brew. Use weekly_brew.")
    r = invoke("pint", "2026-09-27")
    assert r.exit_code == 1
    assert "Sunday" in r.output and "hj brew 2026-09-27" in r.output


@pytest.mark.parametrize("day,tool", [(dt.date(2026, 9, 27), "weekly_brew"), (dt.date(2026, 9, 28), "daily_pint")])
def test_morning_picks_the_show_by_chicago_date(server, monkeypatch, day, tool):
    monkeypatch.setattr(cli, "chicago_today", lambda: day)
    server.responses[tool] = {"show": "S", "date": day.isoformat(), "title": "T", "summary": "", "audio_url": "https://a.example/a.mp3", "play_url": None}
    r = invoke("morning", "--no-play")
    assert r.exit_code == 0, r.output
    assert server.calls == [(tool, {"date": day.isoformat()})]


def test_chicago_today_is_a_date():
    assert isinstance(cli.chicago_today(), dt.date)


# ---------------------------------------------------------------- time travel


def _window_server(server, per_window: dict[int, int]):
    """latest_episodes returns per_window[days] episodes, paged by offset/limit."""

    def latest(args):
        n = per_window.get(args["days"], 0)
        start, limit = args.get("offset", 0), args["limit"]
        eps = [episode(i) for i in range(start, min(n, start + limit))]
        return {"count": len(eps), "episodes": list(reversed(eps))}

    server.responses["latest_episodes"] = latest


def test_timemachine_pages_until_a_short_page(server):
    _window_server(server, {6: 60})
    r = invoke("timemachine", "halving2024", "--json")
    data = json.loads(r.stdout)
    assert data["count"] == 60 and data["window"] == 3
    offsets = [a["offset"] for t, a in server.calls]
    assert offsets == [0, 25, 50]
    assert server.calls[0][1]["until"] == "2024-04-23" and server.calls[0][1]["days"] == 6
    dates = [e["published"] for e in data["episodes"]]
    assert dates == sorted(dates)


def test_timemachine_widens_when_thin(server):
    _window_server(server, {6: 1, 28: 2, 90: 7})
    r = invoke("timemachine", "2014-06-01", "--json")
    data = json.loads(r.stdout)
    assert data["window"] == 45 and data["count"] == 7
    assert [a["days"] for t, a in server.calls if a.get("offset") == 0] == [6, 28, 90]


def test_timemachine_about_uses_search(server):
    server.responses["search_episodes"] = {"count": 3, "episodes": [episode(i) for i in range(3)]}
    invoke("timemachine", "ftx", "--about", "custody")
    assert server.calls == [("search_episodes", {"query": "custody", "limit": 25, "until": "2022-11-11", "days": 6})]


def test_timemachine_dedupes_across_pages(server):
    pages = [{"count": 25, "episodes": [episode(i) for i in range(25)]}, {"count": 3, "episodes": [episode(0), episode(1), episode(30)]}]
    server.responses["latest_episodes"] = pages
    data = json.loads(invoke("timemachine", "covid", "--json").stdout)
    assert data["count"] == 26


def test_named_dates():
    from hodljuice_cli import timemachine as tm

    assert tm.parse_date("FTX")[0] == dt.date(2022, 11, 8)
    assert all(dt.date.fromisoformat(d) >= dt.date(2014, 1, 1) for d, _ in tm.NAMED_DATES.values())
    with pytest.raises(ValueError):
        tm.parse_date("yesterday-ish")


# ---------------------------------------------------------------- blocks


def fake_mempool(tip: int, times: dict[int, int]):
    hashes = {h: f"{h:064x}" for h in times}

    def get(path):
        if path == "/api/blocks/tip/height":
            return str(tip)
        if path.startswith("/api/block-height/"):
            return hashes[int(path.rsplit("/", 1)[1])]
        h = next(k for k, v in hashes.items() if path.endswith(v))
        return json.dumps({"timestamp": times[h]})

    return get


def test_block_height_to_date():
    from hodljuice_cli import block

    get = fake_mempool(900_000, {840_000: 1713571767, 900_000: 1750000000})
    assert block.height_to_date(840_000, get) == (dt.date(2024, 4, 20), False)
    d, estimated = block.height_to_date(900_144, get)  # 144 blocks ≈ one day past the tip
    assert estimated and d == dt.date(2025, 6, 16)
    assert block.headline(840_000, dt.date(2024, 4, 20), False) == "Block 840,000 · 2024-04-20 (the 4th halving)"


def test_block_labels():
    from hodljuice_cli import block

    assert block.label(0) == "genesis"
    assert block.label(57043) == "pizza day"
    assert block.label(210_000) == "the 1st halving"
    assert block.label(1_050_000) == "the 5th halving"
    assert block.label(840_001) is None


def test_block_rejects_a_bad_hash():
    from hodljuice_cli import block

    with pytest.raises(ValueError):
        block.block_time(1, lambda path: "<html>nope</html>")


# ---------------------------------------------------------------- shell fortune


def test_install_shell_is_idempotent_and_reversible(home):
    rc = platforms.shell_rc()
    rc.write_text("export A=1\n")
    assert platforms.install_shell("/usr/local/bin/hj") == (rc, True)
    once = rc.read_text()
    assert platforms.install_shell("/usr/local/bin/hj") == (rc, False)
    assert rc.read_text() == once
    assert once.count(platforms.SHELL_BEGIN) == 1
    assert platforms.uninstall_shell() == (rc, True)
    assert rc.read_text() == "export A=1\n"
    assert platforms.uninstall_shell() == (rc, False)


def test_install_shell_asks_first(home):
    r = invoke("install-shell", input="n\n")
    assert r.exit_code == 1
    assert not platforms.shell_rc().exists()
    r = invoke("install-shell", "--yes")
    assert r.exit_code == 0 and platforms.SHELL_BEGIN in platforms.shell_rc().read_text()


def test_fortune_format_is_clean():
    from hodljuice_cli.fortune import format_fortune

    text = format_fortune(episode(1, title=EVIL_TITLE, snippet="x" * 500), color=False)
    assert "\x1b" not in text and "\x07" not in text
    assert len(text.splitlines()) == 4


def test_morning_schedule_text():
    assert platforms.cron_line("/usr/bin/hj", 7, 5).startswith("5 7 * * 1-5 /usr/bin/hj morning --background")
    plist = platforms.launchd_plist("/a&b/hj", 7, 0)
    assert "<string>/a&amp;b/hj</string>" in plist and plist.count("<key>Weekday</key>") == 5


def test_render_width_follows_fzf_preview(monkeypatch):
    monkeypatch.setenv("FZF_PREVIEW_COLUMNS", "42")
    assert render._preview_width() == 42
