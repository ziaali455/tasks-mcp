"""Per-Google-account token storage, keyed by the Google account's stable user id (sub).

Render's free-tier disk is ephemeral across redeploys, so this is intentionally
just a JSON file on disk: good enough for a handful of personal Test users, who
re-do Google sign-in after a redeploy. See README for the tradeoff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _store_path() -> Path:
    return Path(os.environ.get("TOKEN_STORE_PATH", "./token_store.json"))


def _load_all() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_all(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def save_token_data(user_id: str, data: dict[str, Any]) -> None:
    all_data = _load_all()
    all_data[user_id] = data
    _save_all(all_data)


def load_token_data(user_id: str) -> dict[str, Any] | None:
    return _load_all().get(user_id)
