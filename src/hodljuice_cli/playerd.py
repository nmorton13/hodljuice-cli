"""`hj playerd`: the background player.

Runs one mpv in idle mode, talks to it over mpv's JSON IPC, keeps the queue and
"now playing" info, and refills radio stations when an episode ends. `hj ctl`,
the terminal now-playing screen and the Claude Code mod all control it through
its Unix socket (one JSON request per line, one JSON response per line).

`mcp` is only imported inside functions (via `api`), never at module top.
"""

import asyncio
import fcntl
import json
import os
import random
import shutil
import signal
import sys
import time

from hodljuice_cli import paths
from hodljuice_cli.episodes import episode_id_from_play_url, item_from_episode, item_from_url, item_key
from hodljuice_cli.sanitize import clean, safe_url

IDLE_EXIT_SECONDS = 600
MAX_REQUEST_BYTES = 64 * 1024
COMMANDS = {"ping", "status", "play", "pause", "next", "prev", "seek", "speed", "stop", "save", "radio"}
MIN_SPEED, MAX_SPEED = 0.5, 3.0
RADIO_KEYS = {"year", "show", "topic", "days"}
_EPISODE_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


class CommandError(Exception):
    pass


# ---------------------------------------------------------------- mpv IPC


class Mpv:
    """Client for mpv's JSON IPC (`--input-ipc-server`)."""

    def __init__(self, on_event):
        self._on_event = on_event
        self._reader = None
        self._writer = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._task = None
        self.closed = asyncio.Event()

    async def connect(self, sock_path, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(str(sock_path))
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.05)
        self._task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "event" in msg:
                    self._on_event(msg)
                    continue
                fut = self._pending.pop(msg.get("request_id"), None)
                if fut and not fut.done():
                    fut.set_result(msg)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("mpv went away"))
            self.closed.set()

    async def command(self, *args, timeout: float = 5.0):
        """Run an mpv command; returns `data`, raises CommandError on mpv errors."""
        if self.closed.is_set():
            raise CommandError("mpv isn't running")
        self._next_id += 1
        rid = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self._writer.write(json.dumps({"command": list(args), "request_id": rid}).encode() + b"\n")
        await self._writer.drain()
        msg = await asyncio.wait_for(fut, timeout)
        if msg.get("error") != "success":
            raise CommandError(f"mpv: {msg.get('error')}")
        return msg.get("data")

    async def get(self, prop: str):
        try:
            return await self.command("get_property", prop, timeout=2.0)
        except (CommandError, asyncio.TimeoutError, ConnectionError):
            return None

    async def close(self) -> None:
        if self._writer:
            self._writer.close()


# ---------------------------------------------------------------- data source


class ServerSource:
    """Episode lookups and radio picks from the HodlJuice MCP server."""

    async def episode(self, episode_id: str) -> dict:
        from hodljuice_cli.api import call_once

        return await call_once("get_episode", episode_id=episode_id)

    async def radio_pick(self, filters: dict, seen: set) -> dict:
        from hodljuice_cli.api import HJError, Session

        async with Session() as s:
            if filters.get("topic"):
                return await _topic_pick(s, filters, seen)
            for _ in range(8):
                ep = await s.call(
                    "random_episode",
                    year=filters.get("year"),
                    days=filters.get("days"),
                    podcast=filters.get("show"),
                )
                item = item_from_episode(ep)
                if item and item_key(item) not in seen:
                    return ep
            raise HJError("This station has run out of new episodes. Try different filters.")


async def _topic_pick(s, filters: dict, seen: set) -> dict:
    from hodljuice_cli.api import HJError
    from hodljuice_cli.topics import server_topic

    topic = server_topic(filters["topic"])
    limit = 25
    upper = 500  # offset ceiling; lowered when a page comes back empty
    offsets = list(range(0, upper + 1, limit))
    random.shuffle(offsets)
    tried = 0
    for offset in offsets:
        if offset > upper or tried >= 6:
            continue
        tried += 1
        page = await s.call("topic_episodes", topic=topic, limit=limit, offset=offset)
        eps = page.get("episodes") or []
        if not eps:
            upper = min(upper, offset - limit)
            continue
        fresh = [e for e in eps if _matches(e, filters) and (item := item_from_episode(e)) and item_key(item) not in seen]
        if fresh:
            return random.choice(fresh)
    raise HJError("This station has run out of new episodes. Try different filters.")


def _matches(ep: dict, filters: dict) -> bool:
    published = ep.get("published") or ""
    if filters.get("year") and not published.startswith(str(filters["year"])):
        return False
    if filters.get("show") and filters["show"].lower() not in (ep.get("podcast") or "").lower():
        return False
    if filters.get("days") and published:
        import datetime as dt

        try:
            age = (dt.date.today() - dt.date.fromisoformat(published)).days
        except ValueError:
            return False
        if age > filters["days"]:
            return False
    return True


# ---------------------------------------------------------------- player


class Player:
    def __init__(self, mpv: Mpv, source):
        self.mpv = mpv
        self.source = source
        self.queue: list[dict] = []
        self.index = -1  # position of the current item; -1 or len(queue) means nothing current
        self.radio: dict | None = None
        self.seen: set[str] = set()
        self.lock = asyncio.Lock()
        self.idle_since = time.monotonic()
        self._errors_in_a_row = 0
        self._prefetch: asyncio.Task | None = None
        self._loading = False
        self.speed = load_speed()

    # -- state

    @property
    def current(self) -> dict | None:
        if 0 <= self.index < len(self.queue):
            return self.queue[self.index]
        return None

    async def status(self) -> dict:
        cur = self.current
        if cur is None:
            return {
                "state": "idle", "title": None, "podcast": None, "published": None, "play_url": None,
                "position": None, "duration": None, "radio": self.radio, "queue": len(self.queue),
                "speed": self.speed,
            }
        paused = await self.mpv.get("pause")
        pos = await self.mpv.get("time-pos")
        dur = await self.mpv.get("duration") or cur.get("duration")
        return {
            "state": "paused" if paused else "playing",
            "id": cur.get("id"),
            "title": cur["title"],
            "podcast": cur["podcast"],
            "published": cur["published"],
            "play_url": cur.get("play_url"),
            "audio_url": cur["audio_url"],
            "position": round(pos, 1) if isinstance(pos, (int, float)) else None,
            "duration": round(dur, 1) if isinstance(dur, (int, float)) else None,
            "radio": self.radio,
            "queue": len(self.queue) - self.index - 1,
            "speed": self.speed,
        }

    # -- playback

    async def _load_current(self) -> None:
        cur = self.current
        if cur is None:
            await self.mpv.command("stop")
            self.idle_since = time.monotonic()
            return
        url = safe_url(cur["audio_url"])
        if not url:  # items are validated on the way in; this is belt and braces
            raise CommandError("refusing a non-http(s) URL")
        self.seen.add(item_key(cur))
        self._loading = True
        try:
            await self.mpv.command("loadfile", url, "replace")
            await self.mpv.command("set_property", "speed", self.speed)
            await self.mpv.command("set_property", "pause", False)
        finally:
            self._loading = False
        if self.radio is not None:
            self._start_prefetch()

    async def _advance(self) -> str:
        """Move to the next item (fetching a radio episode if needed)."""
        if self.index + 1 < len(self.queue):
            self.index += 1
        elif self.radio is not None:
            item = await self._radio_item()
            self.queue.append(item)
            self.index = len(self.queue) - 1
        else:
            self.index = len(self.queue)
            await self._load_current()
            return "End of the queue."
        await self._load_current()
        return f"Playing: {self.current['title']}"

    async def _radio_item(self) -> dict:
        ep = await self.source.radio_pick(self.radio, self.seen | {item_key(i) for i in self.queue})
        item = item_from_episode(ep)
        if not item:
            raise CommandError("The station returned an episode without playable audio.")
        return item

    def _start_prefetch(self) -> None:
        if self._prefetch and not self._prefetch.done():
            return
        if self.index + 1 < len(self.queue):
            return

        async def fetch():
            try:
                item = await self._radio_item()
            except Exception as e:  # keep playing; we'll try again at end-file
                log(f"radio prefetch failed: {e}")
                return
            if self.radio is not None and self.index + 1 >= len(self.queue):
                self.queue.append(item)

        self._prefetch = asyncio.create_task(fetch())

    async def on_end_file(self, reason: str) -> None:
        # "stop"/"redirect"/"quit" come from our own loadfile/stop; only real endings advance.
        if reason not in ("eof", "error") or self._loading:
            return
        async with self.lock:
            if self.current is None:
                return
            if reason == "error":
                self._errors_in_a_row += 1
                if self._errors_in_a_row > 5:
                    log("too many failed episodes in a row; stopping")
                    self.radio = None
                    self.index = len(self.queue)
                    await self._load_current()
                    return
            else:
                self._errors_in_a_row = 0
            try:
                await self._advance()
            except Exception as e:
                log(f"advance failed: {e}")
                self.index = len(self.queue)
                self.idle_since = time.monotonic()

    # -- commands

    async def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd not in COMMANDS:
            raise CommandError(f"unknown command {clean(str(cmd))[:40]!r}")
        if cmd == "ping":
            return {"pid": os.getpid()}
        if cmd == "status":
            return await self.status()
        async with self.lock:
            return await getattr(self, f"cmd_{cmd}")(req)

    async def cmd_play(self, req: dict) -> dict:
        append = bool(req.get("append"))
        if "episodes" in req:
            eps = req["episodes"]
            if not isinstance(eps, list) or not eps or len(eps) > 500:
                raise CommandError("`episodes` must be a non-empty list")
            items = [i for i in (item_from_episode(e) for e in eps) if i]
            if not items:
                raise CommandError("none of those episodes has a playable http(s) audio URL")
        else:
            items = [await self._resolve(req.get("target"))]
        if append and self.current is not None:
            self.queue.extend(items)
            n = len(items)
            return {"message": f"Queued {n} episode{'s' if n != 1 else ''}."}
        if not append:
            self.radio = None
        # Play now: drop anything already finished, put the new items next and start the first.
        upcoming = self.queue[self.index + 1:] if append else []
        self.queue = items + upcoming
        self.index = 0
        await self._load_current()
        extra = f" (+{len(items) - 1} queued)" if len(items) > 1 else ""
        return {"message": f"Playing: {items[0]['title']}{extra}"}

    async def _resolve(self, target) -> dict:
        if not isinstance(target, str) or not target.strip():
            raise CommandError("give an episode id or an http(s) URL")
        target = target.strip()
        ep_id = target if len(target) == 11 and set(target) <= _EPISODE_ID_CHARS else None
        if not ep_id and safe_url(target):
            ep_id = episode_id_from_play_url(target)
            if not ep_id:
                item = item_from_url(target)
                if item:
                    return item
        if not ep_id:
            raise CommandError(f"not an episode id or http(s) URL: {clean(target)[:80]}")
        try:
            ep = await self.source.episode(ep_id)
        except Exception as e:
            raise CommandError(str(e)) from None
        item = item_from_episode(ep)
        if not item:
            raise CommandError("that episode has no playable audio URL")
        return item

    async def cmd_pause(self, req: dict) -> dict:
        if self.current is None:
            raise CommandError("Nothing playing.")
        await self.mpv.command("cycle", "pause")
        paused = await self.mpv.get("pause")
        return {"message": "Paused." if paused else "Playing."}

    async def cmd_next(self, req: dict) -> dict:
        if self.current is None and self.radio is None:
            raise CommandError("Nothing playing.")
        # Skipping past the last queued episode keeps the music going: it turns the radio on.
        started_radio = self.radio is None and self.index + 1 >= len(self.queue)
        if started_radio:
            self.radio = {}
        try:
            message = await self._advance()
            return {"message": f"Radio on. {message}" if started_radio else message}
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(str(e)) from None

    async def cmd_prev(self, req: dict) -> dict:
        if self.current is None:
            raise CommandError("Nothing playing.")
        pos = await self.mpv.get("time-pos")
        if (isinstance(pos, (int, float)) and pos > 5) or self.index == 0:
            await self.mpv.command("seek", 0, "absolute")
            return {"message": "Back to the start."}
        self.index -= 1
        await self._load_current()
        return {"message": f"Playing: {self.current['title']}"}

    async def cmd_seek(self, req: dict) -> dict:
        if self.current is None:
            raise CommandError("Nothing playing.")
        secs = req.get("seconds")
        if not isinstance(secs, (int, float)) or isinstance(secs, bool) or abs(secs) > 86400:
            raise CommandError("seek needs a number of seconds")
        # Right after a load, mpv refuses to seek until the stream is open: wait a moment.
        for _ in range(25):
            try:
                await self.mpv.command("seek", secs, "relative")
                return {}
            except CommandError:
                await asyncio.sleep(0.2)
        raise CommandError("Can't seek in this episode yet; try again in a moment.")

    async def cmd_speed(self, req: dict) -> dict:
        speed = req.get("speed")
        if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not MIN_SPEED <= speed <= MAX_SPEED:
            raise CommandError(f"speed must be a number from {MIN_SPEED} to {MAX_SPEED}")
        self.speed = round(float(speed), 2)
        await self.mpv.command("set_property", "speed", self.speed)
        save_speed(self.speed)
        return {"message": f"Speed {self.speed:g}×"}

    async def cmd_stop(self, req: dict) -> dict:
        self.radio = None
        self.queue = []
        self.index = -1
        await self._load_current()
        return {"message": "Stopped."}

    async def cmd_save(self, req: dict) -> dict:
        cur = self.current
        if cur is None:
            raise CommandError("Nothing playing.")
        added = save_episode(cur)
        return {"message": f"Saved: {cur['title']}" if added else f"Already saved: {cur['title']}"}

    async def cmd_radio(self, req: dict) -> dict:
        filters = req.get("filters") or {}
        if not isinstance(filters, dict) or set(filters) - RADIO_KEYS:
            raise CommandError("radio filters are year, show, topic and days")
        clean_filters = {}
        for key in ("year", "days"):
            if filters.get(key) is not None:
                if not isinstance(filters[key], int) or isinstance(filters[key], bool):
                    raise CommandError(f"--{key} must be a whole number")
                clean_filters[key] = filters[key]
        if filters.get("show"):
            clean_filters["show"] = clean(filters["show"])[:100]
        if filters.get("topic"):
            from hodljuice_cli.topics import TOPICS

            if filters["topic"] not in TOPICS:
                raise CommandError(f"--topic is one of: {', '.join(TOPICS)}")
            clean_filters["topic"] = filters["topic"]
        self.radio = clean_filters
        self.seen = set()
        try:
            item = await self._radio_item()
        except Exception as e:
            self.radio = None
            raise CommandError(str(e)) from None
        self.queue = [item]
        self.index = 0
        await self._load_current()
        return {"message": f"Radio on. Playing: {item['title']}"}


def save_episode(item: dict) -> bool:
    """Append to saved.json; returns False if it was already there."""
    path = paths.saved_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        saved = json.loads(path.read_text())
        if not isinstance(saved, list):
            saved = []
    except (OSError, ValueError):
        saved = []
    key = item_key(item)
    if any(isinstance(s, dict) and item_key(s) == key for s in saved):
        return False
    entry = {k: item.get(k) for k in ("id", "title", "podcast", "published", "play_url", "audio_url")}
    entry["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    saved.append(entry)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=2))
    os.replace(tmp, path)
    return True


# ---------------------------------------------------------------- server


def load_speed() -> float:
    """The speed set last time, kept in player.json; 1.0 when there is none."""
    try:
        speed = json.loads(paths.settings_file().read_text()).get("speed")
    except (OSError, ValueError, AttributeError):
        return 1.0
    if isinstance(speed, (int, float)) and not isinstance(speed, bool) and MIN_SPEED <= speed <= MAX_SPEED:
        return float(speed)
    return 1.0


def save_speed(speed: float) -> None:
    f = paths.settings_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            settings = json.loads(f.read_text())
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, ValueError):
            settings = {}
        settings["speed"] = speed
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings))
        os.replace(tmp, f)
    except OSError as e:
        log(f"couldn't save the speed: {e}")


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", file=sys.stderr, flush=True)


async def handle_client(player: Player, reader, writer) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), 10)
        if not line or len(line) > MAX_REQUEST_BYTES:
            return
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            resp = {"ok": False, "error": "bad request"}
        else:
            try:
                resp = {"ok": True, **(await player.handle(req))}
            except CommandError as e:
                resp = {"ok": False, "error": str(e)}
            except Exception as e:
                log(f"error handling {req.get('cmd')}: {e!r}")
                resp = {"ok": False, "error": str(e) or type(e).__name__}
        writer.write(json.dumps(resp).encode() + b"\n")
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, ValueError):
        pass
    finally:
        writer.close()


async def serve(mpv_sock, ctl_sock, source=None, spawn_mpv: bool = True, idle_exit: float = IDLE_EXIT_SECONDS):
    proc = None
    if spawn_mpv:
        mpv_bin = shutil.which("mpv")
        if not mpv_bin:
            log("mpv not found")
            return 1
        await _quit_stale_mpv(mpv_sock)
        proc = await asyncio.create_subprocess_exec(
            mpv_bin, "--idle=yes", "--no-video", "--no-terminal", "--ytdl=no", "--no-config",
            "--audio-display=no", f"--input-ipc-server={mpv_sock}",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )

    player: Player

    def on_event(msg: dict) -> None:
        if msg.get("event") == "end-file":
            asyncio.get_running_loop().create_task(player.on_end_file(msg.get("reason", "")))

    mpv = Mpv(on_event)
    await mpv.connect(mpv_sock)
    player = Player(mpv, source or ServerSource())

    try:
        os.unlink(ctl_sock)
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(lambda r, w: handle_client(player, r, w), path=str(ctl_sock))
    os.chmod(ctl_sock, 0o600)
    log(f"playerd {os.getpid()} listening on {ctl_sock}")

    async def watchdog():
        while True:
            await asyncio.sleep(min(5, idle_exit))
            if player.current is not None:
                player.idle_since = time.monotonic()
            elif time.monotonic() - player.idle_since > idle_exit:
                log("idle; exiting")
                return

    # SIGTERM/SIGHUP/SIGINT end playerd cleanly, taking mpv with it.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    try:
        await asyncio.wait(
            [asyncio.create_task(watchdog()), asyncio.create_task(mpv.closed.wait()),
             asyncio.create_task(stop.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        server.close()
        try:
            os.unlink(ctl_sock)
        except FileNotFoundError:
            pass
        if proc and proc.returncode is None:
            try:
                await mpv.command("quit", timeout=1)
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                proc.kill()
        await mpv.close()
    return 0


async def _quit_stale_mpv(mpv_sock) -> None:
    """An mpv left behind by a playerd that died (we hold the lock, so it's ours): stop it."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(mpv_sock)), 1)
    except (OSError, asyncio.TimeoutError):
        pass
    else:
        log("quitting a leftover mpv")
        writer.write(b'{"command": ["quit"]}\n')
        try:
            await writer.drain()
            await asyncio.wait_for(reader.read(), 2)
        except (OSError, asyncio.TimeoutError):
            pass
        writer.close()
    try:
        os.unlink(mpv_sock)
    except FileNotFoundError:
        pass


def main(argv: list[str]) -> int:
    d = paths.ensure_runtime_dir()
    lock = open(paths.playerd_lock(), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # another playerd is running
    os.umask(0o077)
    try:
        return asyncio.run(serve(paths.mpv_socket(), paths.playerd_socket())) or 0
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            os.unlink(d / "mpv.sock")
        except FileNotFoundError:
            pass
