from __future__ import annotations

import os

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

load_dotenv()

from mcp.server.auth.middleware.auth_context import get_access_token  # noqa: E402
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402  (after load_dotenv)

from . import auth, connector_auth, google_tasks  # noqa: E402

_base_url = auth.public_base_url()

mcp = MCPServer(
    name="google-tasks",
    instructions="Create, list, complete, update and delete the signed-in user's Google Tasks.",
    auth_server_provider=connector_auth.provider,
    auth=AuthSettings(
        issuer_url=_base_url,
        resource_server_url=f"{_base_url}/mcp",
        validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=connector_auth.SCOPES,
            default_scopes=connector_auth.SCOPES,
        ),
        required_scopes=connector_auth.SCOPES,
    ),
)


def _current_user_id() -> str:
    """The Google account (its `sub`) this request's connector token was issued
    for — set when that client completed Google sign-in during /authorize."""
    access_token = get_access_token()
    if access_token is None or not access_token.subject:
        raise RuntimeError("No Google account tied to this connection — reconnect the connector.")
    return access_token.subject


@mcp.tool()
def echo(message: str) -> str:
    """Dummy tool to confirm the server is wired up. Echoes back what you send it."""
    return f"echo: {message}"


@mcp.tool()
def auth_status() -> dict:
    """Check whether this connection's Google account still has a usable Tasks token."""
    user_id = _current_user_id()
    return {"authorized": auth.is_authorized(user_id)}


@mcp.tool()
def list_task_lists() -> list[dict]:
    """List the signed-in user's Google Tasks lists (id and title), to resolve which list a task belongs to."""
    user_id = _current_user_id()
    return [{"id": tl["id"], "title": tl["title"]} for tl in google_tasks.list_task_lists(user_id)]


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
    user_id = _current_user_id()
    return google_tasks.list_tasks(user_id, tasklist_id, show_completed, due_min, due_max)


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
    user_id = _current_user_id()
    return google_tasks.create_task(user_id, tasklist_id, title, notes, due)


@mcp.tool()
def update_task(
    task_id: str,
    tasklist_id: str = "@default",
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
) -> dict:
    """Update fields on an existing Google Task. Only fields you pass are changed."""
    user_id = _current_user_id()
    return google_tasks.update_task(user_id, tasklist_id, task_id, title, notes, due)


@mcp.tool()
def complete_task(task_id: str, tasklist_id: str = "@default") -> dict:
    """Mark a Google Task as completed."""
    user_id = _current_user_id()
    return google_tasks.complete_task(user_id, tasklist_id, task_id)


@mcp.tool()
def delete_task(task_id: str, tasklist_id: str = "@default") -> dict:
    """Permanently delete a Google Task."""
    user_id = _current_user_id()
    google_tasks.delete_task(user_id, tasklist_id, task_id)
    return {"deleted": task_id}


@mcp.custom_route("/oauth/login", methods=["GET"])
async def oauth_login(request: Request) -> Response:
    """Standalone Google sign-in, not tied to a connector client — mainly for
    manual testing. A connector's own login goes through /authorize instead,
    which drives the same Google flow but resumes a pending client auth after."""
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

    is_connector_login = connector_auth.provider.has_pending(state)
    user_id, email = auth.exchange_code_for_token(code, state)

    if is_connector_login:
        redirect_to = connector_auth.provider.complete_login(state, user_id)
        return RedirectResponse(redirect_to, status_code=303)
    return HTMLResponse(f"<p>Google Tasks connected as {email}. You can close this tab.</p>")


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return PlainTextResponse("ok")


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
    return mcp.streamable_http_app(transport_security=_transport_security())


app = build_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
