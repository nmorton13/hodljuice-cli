import asyncio
import json

import pytest

from conftest import FakeMpv, episode
from hodljuice_cli import paths
from hodljuice_cli.playerd import CommandError, Mpv, Player, handle_client


class FakeSource:
    def __init__(self, episodes=None, radio=None):
        self.episodes = episodes or {}
        self.radio = list(radio or [])
        self.radio_calls = []

    async def episode(self, episode_id):
        if episode_id not in self.episodes:
            raise CommandError(f"No episode with id '{episode_id}'.")
        return self.episodes[episode_id]

    async def radio_pick(self, filters, seen):
        self.radio_calls.append((dict(filters), set(seen)))
        for ep in self.radio:
            if ep["id"] not in seen:
                return ep
        raise CommandError("out of episodes")


def run(sockdir, source, body):
    """Start a fake mpv and a Player wired to it, then run `body(player, mpv)`."""

    async def go():
        fake = FakeMpv(str(sockdir / "mpv.sock"))
        await fake.start()
        player = None

        def on_event(msg):
            if msg.get("event") == "end-file":
                asyncio.get_running_loop().create_task(player.on_end_file(msg["reason"]))

        mpv = Mpv(on_event)
        await mpv.connect(sockdir / "mpv.sock")
        player = Player(mpv, source)
        try:
            return await body(player, fake)
        finally:
            await mpv.close()
            await fake.stop()

    return asyncio.run(go())


async def settle():
    for _ in range(20):
        await asyncio.sleep(0.01)


def test_play_resolves_an_id_and_reports_status(sockdir):
    ep = episode(1)
    src = FakeSource(episodes={ep["id"]: ep})

    async def body(player, mpv):
        r = await player.handle({"cmd": "play", "target": ep["id"]})
        assert r["message"] == "Playing: Episode 1"
        assert mpv.loaded == [ep["audio_url"]]
        st = await player.handle({"cmd": "status"})
        assert st["state"] == "playing" and st["title"] == "Episode 1" and st["duration"] == 600.0

        await player.handle({"cmd": "pause"})
        assert (await player.handle({"cmd": "status"}))["state"] == "paused"
        await player.handle({"cmd": "seek", "seconds": 30})
        assert mpv.position == 30
        await player.handle({"cmd": "stop"})
        assert (await player.handle({"cmd": "status"}))["state"] == "idle"

    run(sockdir, src, body)


def test_queue_advances_on_end_of_file_and_goes_idle(sockdir):
    eps = [episode(i) for i in range(3)]

    async def body(player, mpv):
        await player.handle({"cmd": "play", "episodes": eps})
        assert player.current["title"] == "Episode 0"
        await mpv.end_file("eof")
        await settle()
        assert player.current["title"] == "Episode 1"
        await player.handle({"cmd": "next"})
        assert player.current["title"] == "Episode 2"
        await player.handle({"cmd": "prev"})  # position 0 → previous episode
        assert player.current["title"] == "Episode 1"
        await player.handle({"cmd": "next"})
        await mpv.end_file("eof")
        await settle()
        assert player.current is None
        assert mpv.loaded == [e["audio_url"] for e in (eps[0], eps[1], eps[2], eps[1], eps[2])]

    run(sockdir, FakeSource(), body)


def test_our_own_stops_dont_skip(sockdir):
    eps = [episode(i) for i in range(2)]

    async def body(player, mpv):
        await player.handle({"cmd": "play", "episodes": eps})
        await mpv.end_file("stop")  # what mpv sends when we load something else
        await settle()
        assert player.current["title"] == "Episode 0"

    run(sockdir, FakeSource(), body)


def test_append_queues_after_current(sockdir):
    async def body(player, mpv):
        await player.handle({"cmd": "play", "episodes": [episode(0)]})
        r = await player.handle({"cmd": "play", "episodes": [episode(1), episode(2)], "append": True})
        assert r["message"] == "Queued 2 episodes."
        assert [i["title"] for i in player.queue] == ["Episode 0", "Episode 1", "Episode 2"]
        assert mpv.loaded == [episode(0)["audio_url"]]

    run(sockdir, FakeSource(), body)


def test_radio_refills_without_repeats(sockdir):
    station = [episode(i) for i in range(5)]
    src = FakeSource(radio=station)

    async def body(player, mpv):
        r = await player.handle({"cmd": "radio", "filters": {"year": 2024, "topic": "climate"}})
        assert r["message"].startswith("Radio on. Playing: Episode 0")
        await settle()  # prefetch
        for _ in range(3):
            await mpv.end_file("eof")
            await settle()
        played = [i["title"] for i in player.queue[: player.index + 1]]
        assert played == ["Episode 0", "Episode 1", "Episode 2", "Episode 3"]
        assert len(set(mpv.loaded)) == len(mpv.loaded)
        assert src.radio_calls[0][0] == {"year": 2024, "topic": "climate"}
        st = await player.handle({"cmd": "status"})
        assert st["radio"] == {"year": 2024, "topic": "climate"}

    run(sockdir, src, body)


def test_radio_with_no_filters_keeps_going(sockdir):
    src = FakeSource(radio=[episode(i) for i in range(3)])

    async def body(player, mpv):
        await player.handle({"cmd": "radio", "filters": {}})
        r = await player.handle({"cmd": "next"})
        assert r["message"] == "Playing: Episode 1"
        assert (await player.handle({"cmd": "status"}))["state"] == "playing"

    run(sockdir, src, body)


def test_next_past_a_single_episode_starts_the_radio(sockdir):
    ep = episode(9)
    src = FakeSource(episodes={ep["id"]: ep}, radio=[episode(0)])

    async def body(player, mpv):
        await player.handle({"cmd": "play", "target": ep["id"]})
        r = await player.handle({"cmd": "next"})
        assert r["message"] == "Radio on. Playing: Episode 0"
        st = await player.handle({"cmd": "status"})
        assert st["state"] == "playing" and st["radio"] == {}

    run(sockdir, src, body)


def test_radio_filters_are_validated(sockdir):
    async def body(player, mpv):
        for bad in ({"topic": "weather"}, {"year": "2017; rm -rf ~"}, {"shell": "x"}):
            with pytest.raises(CommandError):
                await player.handle({"cmd": "radio", "filters": bad})
        assert player.radio is None

    run(sockdir, FakeSource(radio=[episode(0)]), body)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "ytdl://x", "-https://x", "https://ex ample.com/a.mp3", "ftp://a/b.mp3"],
)
def test_only_http_urls_reach_mpv(sockdir, url):
    async def body(player, mpv):
        with pytest.raises(CommandError):
            await player.handle({"cmd": "play", "target": url})
        r = await player.handle({"cmd": "play", "episodes": [episode(0), episode(1, audio_url=url)]})
        assert r["message"] == "Playing: Episode 0"
        assert len(player.queue) == 1
        assert mpv.loaded == [episode(0)["audio_url"]]

    run(sockdir, FakeSource(), body)


def test_player_link_keeps_its_title(sockdir):
    link = (
        "https://hodljuice.app/?episodeUrl=https%3A%2F%2Fdts.podtrac.com%2Fredirect.mp3%2Fepisodes.hodljuice.app"
        "%2FTheDailyPint2026-10-01.mp3&trackName=The+Daily+Pint&episodeTitle=Bitcoin+Market+Cycles&episodeDate=2026-10-01"
    )

    async def body(player, mpv):
        await player.handle({"cmd": "play", "target": link})
        assert player.current["title"] == "Bitcoin Market Cycles"
        assert player.current["podcast"] == "The Daily Pint"
        assert mpv.loaded == ["https://dts.podtrac.com/redirect.mp3/episodes.hodljuice.app/TheDailyPint2026-10-01.mp3"]

    run(sockdir, FakeSource(), body)


def test_unknown_commands_are_refused(sockdir):
    async def body(player, mpv):
        for cmd in ("loadfile", "run", "__init__", None):
            with pytest.raises(CommandError):
                await player.handle({"cmd": cmd})

    run(sockdir, FakeSource(), body)


def test_hostile_titles_are_cleaned_in_status(sockdir):
    from conftest import EVIL_TITLE

    async def body(player, mpv):
        await player.handle({"cmd": "play", "episodes": [episode(0, title=EVIL_TITLE)]})
        st = await player.handle({"cmd": "status"})
        assert "\x1b" not in st["title"] and "\x07" not in st["title"]

    run(sockdir, FakeSource(), body)


def test_save_appends_once(sockdir, home):
    async def body(player, mpv):
        await player.handle({"cmd": "play", "episodes": [episode(0)]})
        assert (await player.handle({"cmd": "save"}))["message"].startswith("Saved")
        assert (await player.handle({"cmd": "save"}))["message"].startswith("Already saved")

    run(sockdir, FakeSource(), body)
    saved = json.loads(paths.saved_file().read_text())
    assert [s["id"] for s in saved] == [episode(0)["id"]]


def test_socket_protocol(sockdir):
    async def body(player, mpv):
        server = await asyncio.start_unix_server(lambda r, w: handle_client(player, r, w), path=str(sockdir / "p.sock"))

        async def ask(raw: bytes):
            reader, writer = await asyncio.open_unix_connection(str(sockdir / "p.sock"))
            writer.write(raw)
            await writer.drain()
            line = await reader.readline()
            writer.close()
            return json.loads(line)

        assert (await ask(b'{"cmd": "status"}\n'))["state"] == "idle"
        assert (await ask(b"not json\n")) == {"ok": False, "error": "bad request"}
        assert (await ask(b'{"cmd": "pause"}\n')) == {"ok": False, "error": "Nothing playing."}
        server.close()

    run(sockdir, FakeSource(), body)


def test_runtime_dir_is_private(home):
    d = paths.ensure_runtime_dir()
    assert oct(d.stat().st_mode & 0o777) == "0o700"
    assert len(str(paths.playerd_socket())) <= 100
