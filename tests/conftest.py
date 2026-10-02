import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from hodljuice_cli import api

EVIL_TITLE = "[red]Evil[/red] \x1b[31mred\x1b[0m \x1b]8;;https://evil.example\x07click\x1b]8;;\x07 \x07bell"


def episode(i: int, **extra) -> dict:
    ep = {
        "id": f"ep{i:09d}",
        "title": f"Episode {i}",
        "podcast": "Test Show",
        "published": f"2024-04-{(i % 28) + 1:02d}",
        "play_url": f"https://hodljuice.app/e/ep{i:09d}",
        "audio_url": f"https://audio.example/{i}.mp3",
        "artwork_url": None,
        "hand_picked": i % 2 == 0,
        "snippet": None,
    }
    ep.update(extra)
    return ep


@pytest.fixture
def home(monkeypatch):
    # A short path: the runtime dir lives under HOME and must fit a Unix socket path.
    with tempfile.TemporaryDirectory(prefix="hj", dir="/tmp") as d:
        monkeypatch.setenv("HOME", d)
        for var in ("XDG_RUNTIME_DIR", "XDG_CONFIG_HOME"):
            monkeypatch.delenv(var, raising=False)
        yield Path(d)


class FakeServer:
    """Stands in for the MCP server: `responses[tool]` is a dict, a list (served in turn),
    a callable(args) or an api.HJError to raise. Every call is recorded."""

    def __init__(self):
        self.responses: dict = {}
        self.calls: list[tuple[str, dict]] = []

    def answer(self, tool: str, args: dict):
        self.calls.append((tool, args))
        r = self.responses.get(tool)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if callable(r):
            r = r(args)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise api.HJError(f"no fake response for {tool}")
        return r


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()

    class FakeSession:
        def __init__(self, url=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def call(self, tool, **args):
            return fake.answer(tool, {k: v for k, v in args.items() if v is not None})

    monkeypatch.setattr(api, "Session", FakeSession)
    return fake


class FakeMpv:
    """A tiny mpv JSON IPC server: tracks loadfile/pause/seek and can emit end-file."""

    def __init__(self, path: str):
        self.path = path
        self.loaded: list[str] = []
        self.commands: list[list] = []
        self.paused = False
        self.position = 0.0
        self.writers = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, path=self.path)

    async def _client(self, reader, writer):
        self.writers.append(writer)
        while line := await reader.readline():
            msg = json.loads(line)
            cmd = msg["command"]
            self.commands.append(cmd)
            data = None
            if cmd[0] == "loadfile":
                self.loaded.append(cmd[1])
                self.position = 0.0
            elif cmd[0] == "cycle" and cmd[1] == "pause":
                self.paused = not self.paused
            elif cmd[0] == "set_property" and cmd[1] == "pause":
                self.paused = cmd[2]
            elif cmd[0] == "seek":
                self.position = max(0.0, self.position + cmd[1]) if cmd[2] == "relative" else float(cmd[1])
            elif cmd[0] == "get_property":
                data = {"pause": self.paused, "time-pos": self.position, "duration": 600.0}.get(cmd[1])
            writer.write(json.dumps({"request_id": msg["request_id"], "error": "success", "data": data}).encode() + b"\n")
            await writer.drain()

    async def end_file(self, reason="eof"):
        for w in self.writers:
            w.write(json.dumps({"event": "end-file", "reason": reason}).encode() + b"\n")
            await w.drain()

    async def stop(self):
        self.server.close()
        for w in self.writers:
            w.close()


@pytest.fixture
def sockdir():
    with tempfile.TemporaryDirectory(prefix="hjs", dir="/tmp") as d:
        yield Path(d)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("HJ_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set HJ_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
