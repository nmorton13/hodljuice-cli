"""Thin wrapper over the HodlJuice MCP server.

One `Session` per command (not one per call). `mcp` is imported lazily so
modules that import this one stay cheap until they actually talk to the server.
"""

import os
import re

DEFAULT_URL = "https://hodljuice.app/mcp"
EPISODE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ERR_PREFIX = re.compile(r"^Error executing tool \w+:\s*")


class HJError(Exception):
    """A message for the user: a tool error or a connection problem. Never a traceback."""


def server_url() -> str:
    return os.environ.get("HODLJUICE_MCP_URL") or DEFAULT_URL


def tool_error_message(text: str) -> str:
    from hodljuice_cli.sanitize import clean

    return clean(_ERR_PREFIX.sub("", text or "")) or "The server returned an error."


class Session:
    """`async with Session() as s: data = await s.call("random_episode", year=2017)`"""

    def __init__(self, url: str | None = None):
        self.url = url or server_url()
        self._client = None

    async def __aenter__(self):
        from mcp import Client

        self._client = Client(self.url)
        try:
            await self._client.__aenter__()
        except Exception as e:  # network, DNS, TLS, HTTP status…
            raise HJError(f"Couldn't reach {self.url}: {_describe(e)}") from None
        return self

    async def __aexit__(self, *exc):
        try:
            await self._client.__aexit__(*exc)
        except Exception:
            pass
        return False

    async def call(self, tool: str, **args) -> dict:
        args = {k: v for k, v in args.items() if v is not None}
        try:
            r = await self._client.call_tool(tool, args)
        except Exception as e:
            raise HJError(f"{tool} failed: {_describe(e)}") from None
        if r.is_error:
            text = r.content[0].text if r.content and hasattr(r.content[0], "text") else ""
            raise HJError(tool_error_message(text))
        data = r.structured_content
        if not isinstance(data, dict):
            raise HJError(f"{tool} returned no data.")
        return data


def _describe(e: BaseException) -> str:
    # ExceptionGroups from anyio hide the real cause one level down.
    while getattr(e, "exceptions", None):
        e = e.exceptions[0]
    msg = str(e).strip() or type(e).__name__
    return msg.splitlines()[0][:200]


async def call_once(tool: str, **args) -> dict:
    async with Session() as s:
        return await s.call(tool, **args)


def run(coro):
    import asyncio

    return asyncio.run(coro)
