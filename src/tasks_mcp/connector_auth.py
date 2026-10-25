"""A minimal OAuth 2.1 authorization server for this MCP server itself.

This is a *different* OAuth hop from auth.py's Google flow: Google authorizes
*this server* to touch your Google Tasks; this one authorizes an MCP client
(Claude, ChatGPT) to talk to *this server*. Claude's and ChatGPT's custom
connector UIs expect an MCP server to be its own OAuth provider (dynamic
client registration + authorization code + PKCE) before they'll connect at
all — see https://modelcontextprotocol.io/specification/draft/basic/authorization.

For a single personal user, "authorization" here just means typing the
MCP_AUTH_TOKEN password at the /login page; there is no real user database.
Everything is in-memory, which is fine since a lost login (e.g. on redeploy)
just means reconnecting the client, same tradeoff as the Google token store.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    OAuthToken,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from . import auth as google_auth

SCOPES = ["tasks"]
AUTH_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days; reconnect after that or after a redeploy


@dataclass
class _PendingAuthorization:
    client_id: str
    params: AuthorizationParams


class SingleUserOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self) -> None:
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending: dict[str, _PendingAuthorization] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._tokens: dict[str, AccessToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = _PendingAuthorization(client_id=client.client_id, params=params)
        return f"{google_auth.public_base_url()}/login?request_id={request_id}"

    def complete_login(self, request_id: str) -> str:
        """Called by the /login route once the password checks out. Returns the
        client's redirect_uri with a fresh authorization code attached."""
        pending = self._pending.pop(request_id, None)
        if pending is None:
            raise AuthorizeError(error="access_denied", error_description="Login session expired, try again")
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=pending.params.scopes or SCOPES,
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=pending.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
        )
        return construct_redirect_uri(str(pending.params.redirect_uri), code=code, state=pending.params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        if code.expires_at < time.time():
            del self._codes[authorization_code]
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = AccessToken(
            token=token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time() + ACCESS_TOKEN_TTL_SECONDS),
        )
        return OAuthToken(
            access_token=token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return None  # not supported; client re-does the authorization-code flow instead

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        raise TokenError(error="unsupported_grant_type", error_description="Refresh tokens are not supported")

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self._tokens.get(token)
        if access_token is None:
            return None
        if access_token.expires_at is not None and access_token.expires_at < time.time():
            del self._tokens[token]
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._tokens.pop(token.token, None)


provider = SingleUserOAuthProvider()


_LOGIN_FORM = """<!doctype html>
<html><body style="font-family: sans-serif; max-width: 360px; margin: 80px auto;">
<h2>Connect to Google Tasks MCP</h2>
<form method="post">
<input type="hidden" name="request_id" value="{request_id}">
<label>Password<br><input type="password" name="password" autofocus style="width: 100%; padding: 8px;"></label>
{error}
<button type="submit" style="margin-top: 12px; padding: 8px 16px;">Continue</button>
</form>
</body></html>"""


async def login_page(request: Request) -> Response:
    request_id = request.query_params.get("request_id", "")
    return HTMLResponse(_LOGIN_FORM.format(request_id=request_id, error=""))


async def login_submit(request: Request) -> Response:
    form = await request.form()
    request_id = str(form.get("request_id", ""))
    password = str(form.get("password", ""))
    expected = os.environ.get("MCP_AUTH_TOKEN")
    if expected and password != expected:
        return HTMLResponse(
            _LOGIN_FORM.format(
                request_id=request_id, error='<p style="color:red">Wrong password</p>'
            ),
            status_code=401,
        )
    try:
        redirect_to = provider.complete_login(request_id)
    except AuthorizeError as e:
        return HTMLResponse(f"<p>{e.error_description}</p>", status_code=400)
    return RedirectResponse(redirect_to, status_code=303)
