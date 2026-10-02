"""Tables, plain lines, JSON and panels.

Every server string goes through `clean()` and into a Rich `Text` object, so
markup or escape sequences in feed data can never style or hijack the terminal.
"""

import json
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hodljuice_cli.sanitize import clean, safe_url

def _preview_width() -> int | None:
    # Inside `fzf --preview`, fit the preview pane rather than the whole terminal.
    try:
        return int(os.environ["FZF_PREVIEW_COLUMNS"])
    except (KeyError, ValueError):
        return None


console = Console(highlight=False, width=_preview_width())
err = Console(stderr=True, highlight=False)


def episodes_table(episodes: list[dict], title: str | None = None) -> Table:
    table = Table(title=Text(clean(title)) if title else None, title_justify="left", expand=False)
    table.add_column("Date", no_wrap=True, style="cyan")
    table.add_column("Show", style="magenta", max_width=28, overflow="ellipsis", no_wrap=True)
    table.add_column("Title", overflow="fold")
    table.add_column("★", justify="center", style="yellow", no_wrap=True)
    table.add_column("Id", style="dim", no_wrap=True)
    for ep in episodes:
        table.add_row(
            Text(clean(ep.get("published")) or "—"),
            Text(clean(ep.get("podcast") or ep.get("show"))),
            Text(clean(ep.get("title"))),
            Text("★" if ep.get("hand_picked") else ""),
            Text(clean(ep.get("id")) or "—"),
        )
    return table


def plain_line(ep: dict) -> str:
    fields = (ep.get("id"), ep.get("published"), ep.get("podcast") or ep.get("show"), ep.get("title"))
    # clean() turns tabs and newlines into spaces, so a field can't break the format.
    return "\t".join(clean(f) or "-" for f in fields)


def print_json(data) -> None:
    # ensure_ascii escapes every control character, so this is terminal-safe too.
    sys.stdout.write(json.dumps(data, indent=2) + "\n")


def print_episodes(episodes: list[dict], mode: str, raw=None, title: str | None = None) -> None:
    if mode == "json":
        print_json(raw if raw is not None else episodes)
    elif mode == "plain":
        for ep in episodes:
            sys.stdout.write(plain_line(ep) + "\n")
    else:
        if not episodes:
            console.print("No episodes found.", style="dim")
            return
        console.print(episodes_table(episodes, title))


def fmt_duration(seconds) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    m = int(seconds) // 60
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def brew_panel(ep: dict) -> Panel:
    body = Text()
    body.append(clean(ep.get("title")), style="bold")
    meta = " · ".join(x for x in (clean(ep.get("date")), fmt_duration(ep.get("duration_seconds"))) if x)
    if meta:
        body.append("\n" + meta, style="dim")
    summary = clean(ep.get("summary"), multiline=True)
    if summary:
        body.append("\n\n" + summary)
    for label, key in (("Listen", "play_url"), ("Recap", "recap_url")):
        url = safe_url(ep.get(key))
        if url:
            body.append(f"\n{label}: ", style="dim")
            body.append(url, style="underline")
    return Panel(body, title=Text(clean(ep.get("show")) or "HodlJuice", style="bold yellow"),
                 title_align="left", expand=False, width=min(console.width, 90))


def episode_panel(ep: dict) -> Panel:
    body = Text()
    body.append(clean(ep.get("title")), style="bold")
    meta = " · ".join(x for x in (clean(ep.get("podcast")), clean(ep.get("published"))) if x)
    if ep.get("hand_picked"):
        meta += " · ★ hand-picked"
    body.append("\n" + meta, style="dim")
    desc = clean(ep.get("description") or ep.get("snippet"), multiline=True)
    if desc:
        if len(desc) > 1500:
            desc = desc[:1500].rstrip() + "…"
        body.append("\n\n" + desc)
    for label, key in (("Listen", "play_url"), ("Audio", "audio_url")):
        url = safe_url(ep.get(key))
        if url:
            body.append(f"\n{label}: ", style="dim")
            body.append(url)
    if ep.get("id"):
        body.append(f"\nId: {clean(ep['id'])}", style="dim")
    return Panel(body, expand=False, width=min(console.width, 100))


def error(msg: str) -> None:
    err.print(Text(clean(msg, multiline=True), style="red"))


def note(msg: str) -> None:
    err.print(Text(clean(msg), style="dim"))
