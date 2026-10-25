"""Google OAuth web flow (hosted, not installed-app), generalized to any Google
account Google itself lets through: while this app's OAuth consent screen is in
"Testing" status, that's exactly the Test users added in Google Cloud Console —
Google enforces that restriction before the callback below ever runs.

Each in-flight authorization is keyed by `state`. For a connector login,
`state` is connector_auth's own request_id, so the same /oauth/callback can
hand the result back to the right pending connector authorization; for a
standalone visit to /oauth/login it's just a random nonce.
"""

from __future__ import annotations

import os
import secrets

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from . import store

SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# In-flight PKCE verifiers, keyed by `state`. In-memory only: a flow started
# before a redeploy just has to be restarted, same tradeoff as everything else
# here that doesn't survive Render's ephemeral disk.
_pending_flows: dict[str, str] = {}


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def public_base_url() -> str:
    # Render sets RENDER_EXTERNAL_URL automatically; locally, fall back to a default.
    return os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI") or f"{public_base_url()}/oauth/callback"


def _build_flow(code_verifier: str | None = None) -> Flow:
    client_config = {
        "web": {
            "client_id": _require_env("GOOGLE_CLIENT_ID"),
            "client_secret": _require_env("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=_redirect_uri(), code_verifier=code_verifier
    )


def build_authorization_url(state: str | None = None) -> str:
    """Start a Google sign-in. `state` ties the callback back to a pending
    connector authorization; omit it for a standalone /oauth/login visit."""
    state = state or secrets.token_urlsafe(24)
    flow = _build_flow()
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    _pending_flows[state] = flow.code_verifier
    return authorization_url


def exchange_code_for_token(code: str, state: str) -> tuple[str, str]:
    """Exchanges Google's code, persists the token under the signed-in
    account's Google user id, and returns (user_id, email)."""
    code_verifier = _pending_flows.pop(state, None)
    if code_verifier is None:
        raise RuntimeError("OAuth state mismatch or expired — start the login flow again")
    flow = _build_flow(code_verifier=code_verifier)
    # Some Google Workspace admins block specific scopes (e.g. Tasks) for
    # third-party apps without an explicit error — Google just grants fewer
    # scopes than requested. oauthlib treats that mismatch as fatal unless
    # told not to; we'd rather check creds.scopes ourselves and report it clearly.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    flow.fetch_token(code=code)
    creds = flow.credentials
    if "https://www.googleapis.com/auth/tasks" not in (creds.scopes or []):
        raise RuntimeError(
            "Google signed you in but did not grant Tasks access for this account — "
            "likely blocked by its Google Workspace admin (common for school/work accounts). "
            "Try a personal Google account instead, or ask your Workspace admin to allow "
            "this app's access to the Tasks API."
        )

    userinfo = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {creds.token}"})
    userinfo.raise_for_status()
    identity = userinfo.json()
    user_id = identity["sub"]
    email = identity.get("email", user_id)

    store.save_token_data(
        user_id,
        {
            "email": email,
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        },
    )
    return user_id, email


def is_authorized(user_id: str) -> bool:
    return store.load_token_data(user_id) is not None


def get_credentials(user_id: str) -> Credentials:
    """Load a specific user's stored credentials, refreshing if needed."""
    data = store.load_token_data(user_id)
    if data is None:
        raise RuntimeError(
            f"This Google account hasn't connected Tasks yet. Visit {public_base_url()}/oauth/login to authorize."
        )
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        store.save_token_data(
            user_id,
            {
                "email": data.get("email"),
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            },
        )
    return creds
