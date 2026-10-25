"""An OAuth 2.1 authorization server for this MCP server itself, backed by Google sign-in.

This is a *different* OAuth hop from auth.py's low-level Google flow, though it
drives that flow: Google authorizes a specific Google account to touch *its
own* Tasks; this one authorizes an MCP client (Claude, ChatGPT) to talk to
*this server*, as that same Google account. Claude's and ChatGPT's custom
connector UIs expect an MCP server to be its own OAuth provider (dynamic
client registration + authorization code + PKCE) before they'll connect at
all — see https://modelcontextprotocol.io/specification/draft/basic/authorization.

Rather than a password this server invents, "authorization" here just *is*
signing in with Google — so access is naturally limited to whoever Google
lets through, which while this app's OAuth consent screen is in "Testing"
status means exactly the Test users added in Google Cloud Console. Each
connector client ends up bound to one Google account (AccessToken.subject,
the Google account's `sub`), so tool calls act on that account's own Tasks.

Everything here is in-memory, which is fine since a lost login (e.g. on
redeploy) just means reconnecting the client, same tradeoff as the Google
token store.
"""

from __future__ import annotations

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

from . import auth as google_auth

SCOPES = ["tasks"]
AUTH_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days; reconnect after that or after a redeploy


@dataclass
class _PendingAuthorization:
    client_id: str
    params: AuthorizationParams


class MultiUserOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
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
        # `request_id` doubles as the state Google redirects back with, so
        # /oauth/callback can find this pending authorization again.
        request_id = secrets.token_urlsafe(24)
        self._pending[request_id] = _PendingAuthorization(client_id=client.client_id, params=params)
        return google_auth.build_authorization_url(state=request_id)

    def has_pending(self, request_id: str) -> bool:
        return request_id in self._pending

    def complete_login(self, request_id: str, google_user_id: str) -> str:
        """Called from /oauth/callback once Google sign-in succeeds. Returns the
        connector client's redirect_uri with a fresh authorization code attached."""
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
            subject=google_user_id,
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
            subject=authorization_code.subject,
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


provider = MultiUserOAuthProvider()
