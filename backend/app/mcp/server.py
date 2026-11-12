"""The MCP server Claude talks to at /mcp, and every tool it offers.

Tools return compact text written for Claude, never JSON dumps, and work on
the one account auth.py let in (`_user_id()`). They are `async` and do their
database work in a thread (`_in_thread`), so a slow query never holds up the
rest of the app.

McpApp is what main.py mounts. The SDK's session manager can only be started
once, so each start of the app (and each test) builds a fresh server.
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import TypeVar

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from .. import models
from ..database import SessionLocal
from ..planning import drafts
from ..planning.basics import fmt_day
from .auth import current_user_id

READ_ONLY = ToolAnnotations(readOnlyHint=True)
T = TypeVar("T")


async def _in_thread(work: Callable[[Session, models.User], T]) -> T:
    """Run `work(db, user)` off the event loop, with its own database session
    and the connector's account."""

    def run() -> T:
        with SessionLocal() as db:
            return work(db, db.get(models.User, current_user_id.get()))

    return await anyio.to_thread.run_sync(run)


def build_server() -> FastMCP:
    """A new server with every tool registered."""
    # FastMCP adds its own handler to the root logger, which would print every
    # line of the app's log twice (logs.py) and one line per request: undo it.
    # The SDK's warnings and errors still reach the console.
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    server = FastMCP(
        "training",
        instructions="Julien's training calendar, Garmin history and plan (Training App).",
        # One answer per request, nothing kept between them: works behind
        # Cloudflare and survives restarts.
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        # The SDK's own Host check only knows localhost. main.py already sends
        # anything not on https://APP_DOMAIN away, and every request needs a token.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    root.handlers[:] = handlers
    root.setLevel(level)

    @server.tool(annotations=READ_ONLY)
    async def connection_check() -> str:
        """Check that the connector works: says which Training App account is connected and today's date there."""
        return await _in_thread(
            lambda db, user: f"Connected to the Training App as {user.username}. "
            f"Today is {fmt_day(drafts.client_today(None))} ({drafts.client_today(None).isoformat()})."
        )

    return server


class McpApp:
    """The ASGI app behind /mcp (inside auth.BearerAuth). `running()` wraps the
    app's lifetime (main.lifespan); outside it every request gets a 503."""

    def __init__(self) -> None:
        self._manager = None

    @asynccontextmanager
    async def running(self):
        server = build_server()
        server.streamable_http_app()  # creates server.session_manager
        async with server.session_manager.run():
            self._manager = server.session_manager
            try:
                yield
            finally:
                self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._manager is None:
            await JSONResponse({"detail": "The connector is starting."}, status_code=503)(scope, receive, send)
            return
        await self._manager.handle_request(scope, receive, send)
