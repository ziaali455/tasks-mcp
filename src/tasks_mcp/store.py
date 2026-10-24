"""Single-user token storage.

Render's free-tier disk is ephemeral across redeploys, so this is intentionally
just a JSON file on disk: good enough for one personal user, who re-does the
OAuth flow after a redeploy. See README for the tradeoff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _store_path() -> Path:
    return Path(os.environ.get("TOKEN_STORE_PATH", "./token_store.json"))


def save_token_data(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def load_token_data() -> dict[str, Any] | None:
    path = _store_path()
    if not path.exists():
        return None
    return json.loads(path.read_text())


def clear_token_data() -> None:
    path = _store_path()
    if path.exists():
        path.unlink()
