from __future__ import annotations

import os

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

load_dotenv()

from mcp.server.mcpserver import MCPServer  # noqa: E402  (after load_dotenv)

from . import auth, google_tasks  # noqa: E402

mcp = MCPServer(
    name="google-tasks",
    instructions="Create, list, complete, update and delete the user's Google Tasks.",
)


@mcp.tool()
def echo(message: str) -> str:
    """Dummy tool to confirm the server is wired up. Echoes back what you send it."""
    return f"echo: {message}"


@mcp.tool()
def auth_status() -> dict:
    """Check whether this server is currently connected to a Google account."""
    base_url = auth.public_base_url()
    if auth.is_authorized():
        return {"authorized": True}
    return {
        "authorized": False,
        "login_url": f"{base_url}/oauth/login",
        "message": "Not connected yet. Tell the user to open the login_url in a browser to authorize.",
    }


@mcp.tool()
def list_task_lists() -> list[dict]:
    """List the user's Google Tasks lists (id and title), to resolve which list a task belongs to."""
    return [{"id": tl["id"], "title": tl["title"]} for tl in google_tasks.list_task_lists()]


@mcp.tool()
def list_tasks(
    tasklist_id: str = "@default",
    show_completed: bool = False,
    due_min: str | None = None,
    due_max: str | None = None,
) -> list[dict]:
    """List tasks in a Google Tasks list.

    Args:
        tasklist_id: Tasklist id from list_task_lists, or "@default" for the user's default list.
        show_completed: Include completed tasks.
        due_min: RFC3339 timestamp; only tasks due on or after this.
        due_max: RFC3339 timestamp; only tasks due on or before this.

    Returns each task's id, title, notes, due, status and tasklist_id, so the caller
    can resolve a natural-language reference (e.g. "the dry cleaning one") itself.
    """
    return google_tasks.list_tasks(tasklist_id, show_completed, due_min, due_max)


@mcp.tool()
def create_task(
    title: str,
    tasklist_id: str = "@default",
    notes: str | None = None,
    due: str | None = None,
) -> dict:
    """Create a new Google Task.

    Args:
        title: Task title.
        tasklist_id: Tasklist id from list_task_lists, or "@default".
        notes: Optional free-text notes.
        due: Optional RFC3339 due timestamp (e.g. "2026-10-31T00:00:00.000Z"). Google Tasks
            only stores the date part of this, not a time of day.
    """
    return google_tasks.create_task(tasklist_id, title, notes, due)


@mcp.tool()
def update_task(
    task_id: str,
    tasklist_id: str = "@default",
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
) -> dict:
    """Update fields on an existing Google Task. Only fields you pass are changed."""
    return google_tasks.update_task(tasklist_id, task_id, title, notes, due)


@mcp.tool()
def complete_task(task_id: str, tasklist_id: str = "@default") -> dict:
    """Mark a Google Task as completed."""
    return google_tasks.complete_task(tasklist_id, task_id)


@mcp.tool()
def delete_task(task_id: str, tasklist_id: str = "@default") -> dict:
    """Permanently delete a Google Task."""
    google_tasks.delete_task(tasklist_id, task_id)
    return {"deleted": task_id}


@mcp.custom_route("/oauth/login", methods=["GET"])
async def oauth_login(request: Request) -> Response:
    return RedirectResponse(auth.build_authorization_url())


@mcp.custom_route("/oauth/callback", methods=["GET"])
async def oauth_callback(request: Request) -> Response:
    error = request.query_params.get("error")
    if error:
        return PlainTextResponse(f"Google returned an error: {error}", status_code=400)
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return PlainTextResponse("Missing code or state in callback.", status_code=400)
    auth.exchange_code_for_token(code, state)
    return HTMLResponse("<p>Google Tasks connected. You can close this tab.</p>")


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return PlainTextResponse("ok")


class BearerAuthMiddleware:
    """Optional static-token gate for the /mcp endpoint, set via MCP_AUTH_TOKEN.

    Off by default (no env var set) since some MCP clients can't send custom
    headers; the real access control is Google OAuth itself. When set, requires
    `Authorization: Bearer <token>` on everything except the OAuth/health routes.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(("/oauth/", "/health")):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode()
        if auth_header != f"Bearer {self.token}":
            response = PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _transport_security():
    from urllib.parse import urlsplit

    from mcp.server.transport_security import TransportSecuritySettings

    host = urlsplit(auth.public_base_url()).netloc
    allowed_hosts = {host, "localhost:8000", "127.0.0.1:8000"}
    allowed_origins = {auth.public_base_url(), "http://localhost:8000", "http://127.0.0.1:8000"}
    return TransportSecuritySettings(
        allowed_hosts=list(allowed_hosts),
        allowed_origins=list(allowed_origins),
    )


def build_app() -> Starlette:
    app = mcp.streamable_http_app(transport_security=_transport_security())
    token = os.environ.get("MCP_AUTH_TOKEN")
    if token:
        app = BearerAuthMiddleware(app, token)
    return app


app = build_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
