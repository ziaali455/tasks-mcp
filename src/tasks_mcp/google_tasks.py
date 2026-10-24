"""Thin wrapper over the Google Tasks REST API (direct HTTP, no google-api-python-client)."""

from __future__ import annotations

from typing import Any

import httpx

from . import auth

BASE_URL = "https://tasks.googleapis.com/tasks/v1"


def _headers() -> dict[str, str]:
    creds = auth.get_credentials()
    return {"Authorization": f"Bearer {creds.token}"}


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_error:
        raise RuntimeError(f"Google Tasks API error {resp.status_code}: {resp.text}")


def list_task_lists() -> list[dict[str, Any]]:
    resp = httpx.get(f"{BASE_URL}/users/@me/lists", headers=_headers())
    _raise_for_status(resp)
    return resp.json().get("items", [])


def list_tasks(
    tasklist_id: str = "@default",
    show_completed: bool = False,
    due_min: str | None = None,
    due_max: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "showCompleted": show_completed,
        "showHidden": show_completed,
    }
    if due_min:
        params["dueMin"] = due_min
    if due_max:
        params["dueMax"] = due_max
    resp = httpx.get(f"{BASE_URL}/lists/{tasklist_id}/tasks", headers=_headers(), params=params)
    _raise_for_status(resp)
    items = resp.json().get("items", [])
    return [
        {
            "id": t.get("id"),
            "title": t.get("title"),
            "notes": t.get("notes"),
            "due": t.get("due"),
            "status": t.get("status"),
            "completed": t.get("completed"),
            "tasklist_id": tasklist_id,
        }
        for t in items
    ]


def create_task(
    tasklist_id: str = "@default",
    title: str = "",
    notes: str | None = None,
    due: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = due
    resp = httpx.post(f"{BASE_URL}/lists/{tasklist_id}/tasks", headers=_headers(), json=body)
    _raise_for_status(resp)
    return resp.json()


def update_task(
    tasklist_id: str,
    task_id: str,
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    if title is not None:
        patch["title"] = title
    if notes is not None:
        patch["notes"] = notes
    if due is not None:
        patch["due"] = due
    resp = httpx.patch(f"{BASE_URL}/lists/{tasklist_id}/tasks/{task_id}", headers=_headers(), json=patch)
    _raise_for_status(resp)
    return resp.json()


def complete_task(tasklist_id: str, task_id: str) -> dict[str, Any]:
    resp = httpx.patch(
        f"{BASE_URL}/lists/{tasklist_id}/tasks/{task_id}",
        headers=_headers(),
        json={"status": "completed"},
    )
    _raise_for_status(resp)
    return resp.json()


def delete_task(tasklist_id: str, task_id: str) -> None:
    resp = httpx.delete(f"{BASE_URL}/lists/{tasklist_id}/tasks/{task_id}", headers=_headers())
    if resp.status_code != 204:
        _raise_for_status(resp)
