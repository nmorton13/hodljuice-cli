"""The `hj` commands (everything except `hj ctl`, `hj playerd` and `hj fortune`)."""

import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.text import Text

from hodljuice_cli import api, ctl, paths, platforms, render
from hodljuice_cli.api import HJError
from hodljuice_cli.entry import VERSION
from hodljuice_cli.episodes import EPISODE_ID_RE, episode_id_from_play_url
from hodljuice_cli.render import console
from hodljuice_cli.sanitize import clean, safe_url
from hodljuice_cli.topics import TOPICS, page_offset, server_topic

app = typer.Typer(
    help="HodlJuice in your terminal: Bitcoin podcasts to search, play, and travel through time with.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)

CHICAGO = "America/Chicago"

# Shared options
JsonOpt = typer.Option(False, "--json", help="Raw structured output.")
PlainOpt = typer.Option(False, "--plain", help="Tab-separated id, date, show, title (no colour).")
PlayOpt = typer.Option(False, "--play", help="Play the results.")


def _mode(as_json: bool, plain: bool) -> str:
    return "json" if as_json else "plain" if plain else "table"


def fail(msg: str, code: int = 1):
    render.error(msg)
    raise typer.Exit(code)


def call(tool: str, **args) -> dict:
    try:
        return api.run(api.call_once(tool, **args))
    except HJError as e:
        fail(str(e))


def with_session(fn):
    """Run `await fn(session)` in one MCP session; turn HJError into a clean exit."""

    async def go():
        async with api.Session() as s:
            return await fn(s)

    try:
        return api.run(go())
    except HJError as e:
        fail(str(e))


def hj_path() -> str:
    found = shutil.which("hj")
    if found:
        return found
    return str(Path(sys.argv[0]).resolve())


def chicago_today() -> dt.date:
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo(CHICAGO)).date()


# ---------------------------------------------------------------- playing


def _install_hint() -> str:
    if platforms.IS_MAC:
        return "Install mpv to play here: brew install mpv"
    return "Install mpv to play here (e.g. sudo apt install mpv or sudo dnf install mpv)."


def _browser_fallback(url: Optional[str]) -> None:
    import webbrowser

    render.error(_install_hint())
    url = safe_url(url)
    if url:
        webbrowser.open(url)
        console.print(Text(f"Opened {url} in your browser instead."), style="dim")
    raise typer.Exit(1)


def start_playback(req: dict, fallback_url: Optional[str], screen: bool = True) -> None:
    """Send a play/radio request to playerd, then show the now-playing screen."""
    if not shutil.which("mpv"):
        _browser_fallback(fallback_url)
    reason = platforms.headless_reason()
    if reason:
        render.note(reason)
    try:
        resp = ctl.request(req, timeout=60.0, start=True)
    except (OSError, ValueError, ctl.PlayerdError) as e:
        fail(f"Couldn't reach the player: {e}")
    if not resp or not resp.get("ok"):
        fail((resp or {}).get("error") or "The player didn't respond.")
    if screen and sys.stdout.isatty():
        from hodljuice_cli.nowplaying import run_screen

        run_screen()
    else:
        console.print(Text(clean(resp.get("message", ""))))


def play_episodes(episodes: list[dict]) -> None:
    episodes = [e for e in episodes if isinstance(e, dict)]
    if not episodes:
        fail("Nothing to play.")
    start_playback({"cmd": "play", "episodes": episodes}, episodes[0].get("play_url"))


def output(episodes: list[dict], as_json: bool, plain: bool, play: bool, raw=None, title=None) -> None:
    render.print_episodes(episodes, _mode(as_json, plain), raw=raw, title=title)
    if play:
        play_episodes(episodes)


# ---------------------------------------------------------------- lists


@app.command()
def search(
    query: str = typer.Argument(..., help="What to search for."),
    year: Optional[int] = typer.Option(None, help="Only this year (its own best matches)."),
    until: Optional[str] = typer.Option(None, help="Published on or before YYYY-MM-DD."),
    days: Optional[int] = typer.Option(None, help="Only the last N days (before --until if given)."),
    limit: int = typer.Option(10, min=1, max=25),
    as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt,
):
    """Search episodes, ranked by relevance."""
    if len(query) > 200:
        fail("Search terms are limited to 200 characters.")
    data = call("search_episodes", query=query, year=year, until=until, days=days, limit=limit)
    output(data.get("episodes") or [], as_json, plain, play, raw=data)


@app.command("random")
def random_cmd(
    year: Optional[int] = typer.Option(None),
    days: Optional[int] = typer.Option(None),
    show: Optional[str] = typer.Option(None, help="Part of a show name, e.g. TFTC."),
    as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt,
):
    """One random episode."""
    ep = call("random_episode", year=year, days=days, podcast=show)
    if as_json or plain:
        render.print_episodes([ep], _mode(as_json, plain), raw=ep)
    else:
        console.print(render.episode_panel(ep))
    if play:
        play_episodes([ep])


@app.command()
def latest(
    days: int = typer.Option(7, min=1, max=90),
    show: Optional[str] = typer.Option(None, help="Part of a show name."),
    limit: int = typer.Option(10, min=1, max=25),
    as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt,
):
    """Newest episodes first."""
    data = call("latest_episodes", days=days, limit=limit, podcast=show)
    output(data.get("episodes") or [], as_json, plain, play, raw=data)


@app.command()
def topic(
    name: str = typer.Argument(..., help=f"One of: {', '.join(TOPICS)}."),
    limit: int = typer.Option(10, min=1, max=25),
    page: int = typer.Option(1, min=1, help="Page number (1-based)."),
    as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt,
):
    """Episodes on a topic: money, climate or humanitarian."""
    try:
        t = server_topic(name)
        offset = page_offset(page, limit)
    except ValueError as e:
        fail(str(e))
    if offset > 500:
        fail("That page is past the end (offset is limited to 500).")
    data = call("topic_episodes", topic=t, limit=limit, offset=offset)
    title = f"{clean(data.get('topic')) or name} · page {page} · {data.get('hand_picked_count', 0)} hand-picked"
    output(data.get("episodes") or [], as_json, plain, play, raw=data, title=title)


@app.command()
def people(as_json: bool = JsonOpt, plain: bool = PlainOpt):
    """People with their own page on HodlJuice."""
    data = call("list_people")
    folks = data.get("people") or []
    if as_json:
        render.print_json(data)
    elif plain:
        for p in folks:
            sys.stdout.write(clean(p.get("name")) + "\n")
    else:
        from rich.columns import Columns

        console.print(Columns([Text(clean(p.get("name"))) for p in folks], equal=True, expand=False))
        console.print(Text(f"\n{len(folks)} people · hj person \"Name\"", style="dim"))


@app.command()
def person(
    name: str = typer.Argument(..., help="A name from `hj people`."),
    year: Optional[int] = typer.Option(None, help="Only episodes from this year."),
    limit: int = typer.Option(20, min=1, max=50),
    as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt,
):
    """Episodes featuring a person."""
    data = call("person_episodes", name=name, limit=50 if year else limit)
    eps = data.get("episodes") or []
    if year:
        eps = [e for e in eps if (e.get("published") or "").startswith(str(year))][:limit]
    output(eps, as_json, plain, play, raw=data if not year else {**data, "episodes": eps},
           title=clean(data.get("name")) or name)
    if not eps and not as_json:
        tip = f'Tip: people pages are hand-collected and may lag. Try: hj search "{clean(name)}"'
        render.note(tip + (f" --year {year}" if year else ""))


@app.command()
def saved(as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt):
    """Episodes you saved while listening (key s)."""
    try:
        items = json.loads(paths.saved_file().read_text())
    except FileNotFoundError:
        items = []
    except (OSError, ValueError) as e:
        fail(f"Couldn't read {paths.saved_file()}: {e}")
    items = [i for i in items if isinstance(i, dict)]
    output(items, as_json, plain, play, raw=items, title="Saved")


# ---------------------------------------------------------------- single episodes


def _brew(tool: str, date: Optional[str], as_json: bool, play: bool) -> None:
    try:
        ep = api.run(api.call_once(tool, date=date))
    except HJError as e:
        msg = str(e)
        render.error(msg)
        if tool == "daily_pint" and "weekly_brew" in msg.lower().replace(" ", "_"):
            if sys.stdin.isatty() and typer.confirm("Show the Weekly Brew instead?", default=True):
                return _brew("weekly_brew", date, as_json, play)
            render.note(f"Try: hj brew {date or ''}".strip())
        raise typer.Exit(1)
    if as_json:
        render.print_json(ep)
    else:
        console.print(render.brew_panel(ep))
    if play:
        play_episodes([ep])


@app.command()
def pint(
    date: Optional[str] = typer.Argument(None, help="YYYY-MM-DD (latest if omitted)."),
    as_json: bool = JsonOpt, play: bool = PlayOpt,
):
    """The Daily Pint (Mon–Sat)."""
    _brew("daily_pint", date, as_json, play)


@app.command()
def brew(
    date: Optional[str] = typer.Argument(None, help="A Sunday, YYYY-MM-DD (latest if omitted)."),
    as_json: bool = JsonOpt, play: bool = PlayOpt,
):
    """The Weekly Brew (Sundays)."""
    _brew("weekly_brew", date, as_json, play)


@app.command()
def show(
    episode_id: str = typer.Argument(..., help="Episode id (or a --plain line)."),
    as_json: bool = JsonOpt, play: bool = PlayOpt,
):
    """Details for one episode."""
    episode_id = _first_field(episode_id)
    if not EPISODE_ID_RE.fullmatch(episode_id):
        fail(f"{clean(episode_id)[:40]!r} isn't an episode id (11 characters: letters, digits, - or _).")
    ep = call("get_episode", episode_id=episode_id)
    if as_json:
        render.print_json(ep)
    else:
        console.print(render.episode_panel(ep))
    if play:
        play_episodes([ep])


def _first_field(line: str) -> str:
    return line.strip().split("\t", 1)[0].strip()


@app.command()
def play(target: Optional[str] = typer.Argument(None, help="Episode id, play_url or audio URL. Reads stdin if omitted.")):
    """Play an episode (also takes a --plain line on stdin, e.g. from fzf)."""
    if target is None:
        if sys.stdin.isatty():
            fail("Give an episode id or URL, or pipe in a line from `hj … --plain`.")
        line = next((ln for ln in sys.stdin if ln.strip()), "")
        target = _first_field(line)
        if not target:
            fail("Nothing selected.")
    target = target.strip()
    if EPISODE_ID_RE.fullmatch(target):
        fallback = f"https://hodljuice.app/e/{target}"
    elif safe_url(target):
        fallback = target
    else:
        fail(f"{clean(target)[:80]!r} isn't an episode id or an http(s) URL.")
    if episode_id_from_play_url(target):
        target = episode_id_from_play_url(target)
    start_playback({"cmd": "play", "target": target}, fallback)


@app.command()
def now(short: bool = typer.Option(False, "--short", help="One line for tmux or status bars (empty when idle).")):
    """Show what's playing (reattach the now-playing screen)."""
    st = ctl.request({"cmd": "status"}, timeout=2.0) or dict(ctl.IDLE_STATUS)
    if short:
        line = ctl.short_line(st)
        if line:
            print(line)
        return
    if st.get("state") in (None, "idle"):
        console.print("Nothing playing.", style="dim")
        return
    if sys.stdout.isatty():
        from hodljuice_cli.nowplaying import run_screen

        run_screen()
    else:
        print(ctl.short_line(st))


# ---------------------------------------------------------------- rituals


@app.command()
def radio(
    year: Optional[int] = typer.Option(None),
    show: Optional[str] = typer.Option(None, help="Part of a show name."),
    topic: Optional[str] = typer.Option(None, help=f"One of: {', '.join(TOPICS)}."),
    days: Optional[int] = typer.Option(None),
):
    """An endless station of random episodes, no repeats."""
    if topic:
        try:
            server_topic(topic)
        except ValueError as e:
            fail(str(e))
        topic = topic.lower()
        topic = next((k for k, v in TOPICS.items() if topic in (k, v)), topic)
    filters = ctl.radio_filters(year, show, topic, days)
    start_playback({"cmd": "radio", "filters": filters}, "https://hodljuice.app")


@app.command()
def morning(
    no_play: bool = typer.Option(False, "--no-play", help="Only print it."),
    say: bool = typer.Option(False, "--say", help="Also speak the title."),
    install: bool = typer.Option(False, "--install", help="Run every weekday morning (launchd or cron)."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Remove the weekday schedule."),
    at: str = typer.Option("07:00", "--at", help="Time for --install, HH:MM local."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before installing."),
    background: bool = typer.Option(False, "--background", hidden=True),
):
    """Today's Daily Pint (Weekly Brew on Sundays), then play it."""
    if uninstall:
        console.print(Text(platforms.morning_uninstall()))
        return
    if install:
        try:
            hour, minute = (int(x) for x in at.split(":"))
            assert 0 <= hour < 24 and 0 <= minute < 60
        except (ValueError, AssertionError):
            fail("--at takes HH:MM, e.g. 07:30.")
        where, what = platforms.morning_install_preview(hj_path(), hour, minute)
        console.print(Text(f"This will write to {where}:\n", style="bold"))
        console.print(Text(what))
        if not yes and not typer.confirm("Go ahead?", default=False):
            raise typer.Exit(1)
        console.print(Text(platforms.morning_install(hj_path(), hour, minute)))
        return

    today = chicago_today()
    sunday = today.weekday() == 6
    tool = "weekly_brew" if sunday else "daily_pint"

    async def fetch(s):
        try:
            return await s.call(tool, date=today.isoformat()), False
        except HJError:
            return await s.call(tool), True

    ep, fell_back = with_session(fetch)
    if fell_back and not background:
        render.note("Today's episode isn't out yet; here's the latest.")
    if not background:
        console.print(render.brew_panel(ep))
    if say:
        platforms.speak(f"{clean(ep.get('show'))}: {clean(ep.get('title'))}")
    if not no_play:
        if background:
            from hodljuice_cli.episodes import item_from_episode

            if shutil.which("mpv") and item_from_episode(ep):
                ctl.request({"cmd": "play", "episodes": [ep]}, timeout=60.0, start=True)
            return
        play_episodes([ep])


@app.command("install-shell")
def install_shell(yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask first.")):
    """Show a HodlJuice fortune in every new shell."""
    rc = platforms.shell_rc()
    console.print(Text(f"This adds these lines to {rc}:\n", style="bold"))
    console.print(Text(platforms.shell_block(hj_path())))
    if not yes and not typer.confirm("Go ahead?", default=False):
        raise typer.Exit(1)
    rc, changed = platforms.install_shell(hj_path())
    console.print(Text(f"Updated {rc}." if changed else f"{rc} already has it."))


@app.command("uninstall-shell")
def uninstall_shell():
    """Remove the fortune from your shell startup file."""
    rc, changed = platforms.uninstall_shell()
    console.print(Text(f"Removed it from {rc}." if changed else f"Nothing to remove in {rc}."))


@app.command(hidden=True)
def fortune():
    """One random episode as a fortune (runs via a fast path; see entry.py)."""
    from hodljuice_cli import fortune as f

    raise typer.Exit(f.main([]))


# ---------------------------------------------------------------- time travel


def _timemachine(date: dt.date, label: Optional[str], window: int, about: Optional[str],
                 as_json: bool, plain: bool, play: bool) -> None:
    from hodljuice_cli import timemachine as tm

    def widened(old, new, n):
        found = f"Only {n} episode{'s' if n != 1 else ''}" if n else "No episodes"
        render.note(f"{found} within ±{old} days; widening to ±{new}.")

    eps, used = with_session(lambda s: tm.travel(s, date, window, about, on_widen=widened))
    title = f"±{used} days around {date.isoformat()}"
    if label:
        title += f" ({label})"
    if about:
        title += f" · about “{about}”"
    title += f" · {len(eps)} episode{'s' if len(eps) != 1 else ''}"
    raw = {"date": date.isoformat(), "window": used, "about": about, "count": len(eps), "episodes": eps}
    output(eps, as_json, plain, play, raw=raw, title=title)


@app.command()
def timemachine(
    date: str = typer.Argument(..., help="YYYY-MM-DD or a name: ath2017, covid, ftx, halving2024, …"),
    window: int = typer.Option(3, min=0, max=45, help="± days around the date."),
    about: Optional[str] = typer.Option(None, help="Only episodes about this (ranked by relevance)."),
    as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt,
):
    """Episodes published around a date, oldest first."""
    from hodljuice_cli import timemachine as tm

    try:
        d, label = tm.parse_date(date)
    except ValueError as e:
        fail(str(e))
    if d < dt.date(2014, 1, 1):
        render.note("Coverage before 2014 is very thin; expect little or nothing.")
    _timemachine(d, label, window, about, as_json, plain, play)


@app.command()
def block(
    height: int = typer.Argument(..., help="Block height, e.g. 840000."),
    window: int = typer.Option(3, min=0, max=45),
    about: Optional[str] = typer.Option(None),
    as_json: bool = JsonOpt, plain: bool = PlainOpt, play: bool = PlayOpt,
):
    """What was on the air when a block was mined."""
    from hodljuice_cli import block as b

    try:
        d, estimated = b.height_to_date(height)
    except ValueError as e:
        fail(str(e))
    except OSError as e:
        fail(f"Couldn't reach mempool.space: {e}")
    headline = b.headline(height, d, estimated)
    if not as_json and not plain:
        console.print(Text(headline, style="bold"))
    else:
        render.note(headline)
    if d > dt.date.today():
        render.note("That block hasn't been mined yet, so there's nothing to play.")
        return
    _timemachine(d, b.label(height), window, about, as_json, plain, play)


# ---------------------------------------------------------------- the Claude Code mod


mod_app = typer.Typer(help="The Claude Code mod.", no_args_is_help=True)
app.add_typer(mod_app, name="mod")


MARKETPLACE = "hodljuice"
PLUGIN = "hodljuice@hodljuice"


def marketplace_dir() -> Optional[Path]:
    """The folder holding .claude-plugin/marketplace.json and claude-mod/."""
    import os

    candidates = []
    if os.environ.get("HJ_MOD_DIR"):
        candidates.append(Path(os.environ["HJ_MOD_DIR"]))
    # Homebrew: the venv lives in libexec/, the marketplace in share/hodljuice/.
    # Prefer the opt/ link, which survives `brew upgrade`, over the versioned Cellar folder.
    keg = Path(sys.prefix).parent
    if keg.parent.parent.name == "Cellar":
        candidates.append(keg.parent.parent.parent / "opt" / keg.parent.name / "share" / "hodljuice")
    candidates.append(keg / "share" / "hodljuice")
    # A source checkout (uv tool install ./hodljuice-cli, or an editable install).
    candidates.append(Path(__file__).resolve().parents[2])
    for c in candidates:
        if (c / ".claude-plugin" / "marketplace.json").exists() and (c / "claude-mod").is_dir():
            return c
    return None


def mod_dir() -> Optional[Path]:
    d = marketplace_dir()
    return d / "claude-mod" if d else None


def _need_marketplace() -> Path:
    d = marketplace_dir()
    if not d:
        fail("The mod isn't installed alongside this hj. In Claude Code, run:\n"
             "  /plugin marketplace add nmorton13/hodljuice-cli\n  /plugin install hodljuice@hodljuice")
    return d


@mod_app.command("path")
def mod_path():
    """Print where the mod is installed."""
    print(_need_marketplace() / "claude-mod")


def _claude(args: list[str]) -> int:
    import subprocess

    claude = shutil.which("claude")
    if not claude:
        fail("Claude Code (`claude`) isn't on PATH.")
    console.print(Text("$ claude " + " ".join(args), style="dim"))
    return subprocess.run([claude, *args]).returncode


@mod_app.command("install")
def mod_install(yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask first.")):
    """Add the mod to Claude Code (user scope)."""
    d = _need_marketplace()
    steps = [["plugin", "marketplace", "add", str(d)], ["plugin", "install", PLUGIN]]
    console.print(Text("This runs:\n" + "\n".join("  claude " + " ".join(a) for a in steps)))
    if not yes and not typer.confirm("Go ahead?", default=True):
        raise typer.Exit(1)
    for args in steps:
        if _claude(args) != 0:
            raise typer.Exit(1)
    console.print("Done. Restart Claude Code to load it; /hj, /pint, /brew and /hj-radio are then there.")


@mod_app.command("uninstall")
def mod_uninstall():
    """Remove the mod from Claude Code."""
    code = _claude(["plugin", "uninstall", PLUGIN])
    code |= _claude(["plugin", "marketplace", "remove", MARKETPLACE])
    raise typer.Exit(code)


def version_callback(value: bool):
    if value:
        print(f"hj {VERSION}")
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(False, "--version", "-V", callback=version_callback, is_eager=True)):
    """HodlJuice in your terminal. `hj ctl --help` for player controls."""
