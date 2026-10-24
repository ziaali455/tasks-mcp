"""Google OAuth web flow (hosted, not installed-app) for a single personal user."""

from __future__ import annotations

import os
import secrets

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from . import store

SCOPES = ["https://www.googleapis.com/auth/tasks"]

# CSRF guard for the one-user OAuth dance: /oauth/login stores the state it
# generated here, /oauth/callback checks the one it got back against it.
_pending_state: str | None = None


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


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": _require_env("GOOGLE_CLIENT_ID"),
            "client_secret": _require_env("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=_redirect_uri())


def build_authorization_url() -> str:
    global _pending_state
    flow = _build_flow()
    state = secrets.token_urlsafe(24)
    authorization_url, returned_state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    _pending_state = returned_state
    return authorization_url


def exchange_code_for_token(code: str, state: str) -> None:
    if not _pending_state or state != _pending_state:
        raise RuntimeError("OAuth state mismatch — start the login flow again at /oauth/login")
    flow = _build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    store.save_token_data(
        {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }
    )


def is_authorized() -> bool:
    return store.load_token_data() is not None


def get_credentials() -> Credentials:
    """Load stored credentials, refreshing the access token if needed."""
    data = store.load_token_data()
    if data is None:
        raise RuntimeError(
            f"Not connected to Google Tasks yet. Visit {public_base_url()}/oauth/login to authorize."
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
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            }
        )
    return creds
