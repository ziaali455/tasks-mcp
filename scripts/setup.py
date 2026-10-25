#!/usr/bin/env python3
"""Interactive setup wizard for deploying this MCP server to Render.

Run from the repo root with:

    uv run python scripts/setup.py

Walks through the parts that have to be manual (Google Cloud Console has no
API for creating an OAuth client) and automates the parts that don't (pushing
to GitHub, creating and configuring the Render service) when you give it a
Render API key. Safe to re-run — it only overwrites what you confirm.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_API = "https://api.render.com/v1"


def header(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{prompt} ({hint}): ").strip().lower()
    if not value:
        return default
    return value.startswith("y")


def open_url(url: str) -> None:
    print(f"  -> {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, **kwargs)


def which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ---------------------------------------------------------------------------
# Step 1: prerequisites
# ---------------------------------------------------------------------------


def check_prerequisites() -> None:
    header("1. Checking prerequisites")
    missing = []
    if not which("uv"):
        missing.append("uv (https://docs.astral.sh/uv/getting-started/installation/)")
    if not which("git"):
        missing.append("git")
    if missing:
        print("Missing required tools:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)
    print("uv and git found.")
    if which("gh"):
        gh_auth = run(["gh", "auth", "status"])
        print("gh CLI found and authenticated." if gh_auth.returncode == 0 else "gh CLI found but not logged in.")
    else:
        print("gh CLI not found (optional — only used to auto-create a GitHub repo).")


# ---------------------------------------------------------------------------
# Step 2: Google Cloud Console (no API for this part, so it's guided manually)
# ---------------------------------------------------------------------------


def google_cloud_console_setup() -> tuple[str, str]:
    header("2. Google Cloud Console setup (manual — Google has no API for this)")
    print(
        "You'll create a Google Cloud project, enable the Tasks API, configure an\n"
        "OAuth consent screen, and create an OAuth client. Follow each step; press\n"
        "Enter after each one to continue.\n"
    )

    print("a) Create or pick a Google Cloud project:")
    open_url("https://console.cloud.google.com/projectcreate")
    input("   Press Enter once you have a project selected...")

    print("\nb) Enable the Google Tasks API for that project:")
    open_url("https://console.cloud.google.com/apis/library/tasks.googleapis.com")
    print("   Click 'Enable'.")
    input("   Press Enter once enabled...")

    print("\nc) Configure the OAuth consent screen:")
    open_url("https://console.cloud.google.com/auth/overview")
    print(
        "   - User type: External\n"
        "   - Fill in app name, support email, etc.\n"
        "   - Leave publishing status as 'Testing' (no Google review needed)\n"
        "   - Under Audience / Test users, add the Google account(s) that should\n"
        "     be able to use this — including your own. Only accounts added here\n"
        "     will ever be able to sign in."
    )
    input("   Press Enter once the consent screen is configured with at least one test user...")

    print("\nd) Create an OAuth client:")
    open_url("https://console.cloud.google.com/auth/clients")
    print(
        "   - Create Credentials -> OAuth client ID\n"
        "   - Application type: Web application\n"
        "   - Authorized redirect URIs: leave a placeholder for now (e.g.\n"
        "     https://placeholder.onrender.com/oauth/callback) — this wizard will\n"
        "     tell you the real one to set once the service is deployed."
    )
    input("   Press Enter once the OAuth client is created...")

    print("\nCopy the Client ID and Client Secret from that OAuth client.")
    client_id = ask("Client ID")
    client_secret = ask("Client Secret")
    while not client_id or not client_secret:
        print("Both are required.")
        client_id = ask("Client ID")
        client_secret = ask("Client Secret")
    return client_id, client_secret


# ---------------------------------------------------------------------------
# Step 3: local .env (for local testing, optional)
# ---------------------------------------------------------------------------


def write_local_env(client_id: str, client_secret: str) -> None:
    header("3. Local .env (optional, for testing on your machine)")
    env_path = REPO_ROOT / ".env"
    if env_path.exists() and not ask_yes_no(f"{env_path} already exists. Overwrite it?", default=False):
        print("Skipping local .env.")
        return
    env_path.write_text(
        f"GOOGLE_CLIENT_ID={client_id}\n"
        f"GOOGLE_CLIENT_SECRET={client_secret}\n"
        "TOKEN_STORE_PATH=./token_store.json\n"
        "PORT=8000\n"
    )
    print(f"Wrote {env_path}")


# ---------------------------------------------------------------------------
# Step 4: GitHub
# ---------------------------------------------------------------------------


def ensure_github_repo() -> str:
    header("4. GitHub repository")
    remote = run(["git", "remote", "get-url", "origin"])
    if remote.returncode == 0 and remote.stdout.strip():
        url = remote.stdout.strip()
        print(f"Found existing 'origin' remote: {url}")
        if ask_yes_no("Push the current branch now?", default=True):
            branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "main"
            push = run(["git", "push", "-u", "origin", branch])
            print(push.stdout + push.stderr)
        return url

    print("No 'origin' remote configured.")
    if not which("gh"):
        print("Install the GitHub CLI (https://cli.github.com) to auto-create a repo, or add")
        print("a remote yourself with: git remote add origin <url>")
        return ask("Paste the GitHub repo URL you'll deploy from (e.g. https://github.com/you/tasks-mcp)")

    if ask_yes_no("Create a new private GitHub repo and push with `gh`?", default=True):
        name = ask("Repo name", default="tasks-mcp")
        visibility = "--private" if ask_yes_no("Private repo?", default=True) else "--public"
        result = run(["gh", "repo", "create", name, visibility, "--source=.", "--remote=origin", "--push"])
        print(result.stdout + result.stderr)
        if result.returncode != 0:
            return ask("Paste the GitHub repo URL you'll deploy from")
        remote = run(["git", "remote", "get-url", "origin"])
        return remote.stdout.strip()

    return ask("Paste the GitHub repo URL you'll deploy from")


# ---------------------------------------------------------------------------
# Step 5: Render
# ---------------------------------------------------------------------------


def render_api_request(method: str, path: str, api_key: str, **kwargs):
    import httpx

    resp = httpx.request(
        method,
        f"{RENDER_API}{path}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=30,
        **kwargs,
    )
    return resp


def deploy_with_render_api(api_key: str, repo_url: str, client_id: str, client_secret: str) -> str | None:
    import httpx

    owners_resp = render_api_request("GET", "/owners", api_key)
    if owners_resp.status_code != 200:
        print(f"Couldn't list Render owners ({owners_resp.status_code}): {owners_resp.text[:300]}")
        return None
    owners = json.loads(owners_resp.text, strict=False)
    if not owners:
        print("No Render owners found for this API key.")
        return None
    owner = owners[0]["owner"]
    if len(owners) > 1:
        for i, o in enumerate(owners):
            print(f"  [{i}] {o['owner']['name']} ({o['owner']['id']})")
        idx = int(ask("Pick an owner index", default="0") or 0)
        owner = owners[idx]["owner"]
    print(f"Using Render owner: {owner['name']}")

    name = ask("Render service name", default="tasks-mcp")
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "main"

    payload = {
        "type": "web_service",
        "name": name,
        "ownerId": owner["id"],
        "repo": repo_url,
        "autoDeploy": "yes",
        "branch": branch,
        "serviceDetails": {
            "env": "python",
            "plan": "free",
            "region": "oregon",
            "envSpecificDetails": {
                "buildCommand": "pip install uv && uv sync --frozen --no-dev",
                "startCommand": "uv run tasks-mcp",
            },
        },
        "envVars": [
            {"key": "GOOGLE_CLIENT_ID", "value": client_id},
            {"key": "GOOGLE_CLIENT_SECRET", "value": client_secret},
            {"key": "TOKEN_STORE_PATH", "value": "/opt/render/project/src/token_store.json"},
        ],
    }
    resp = render_api_request("POST", "/services", api_key, json=payload)
    if resp.status_code not in (200, 201):
        print(f"Render service creation failed ({resp.status_code}): {resp.text[:500]}")
        print(
            "If this mentions repo access, connect GitHub to Render once in the dashboard\n"
            "(Account Settings -> GitHub), then re-run this wizard."
        )
        return None
    data = json.loads(resp.text, strict=False)
    service_id = data["service"]["id"]
    deploy_id = data["deployId"]
    url = data["service"]["serviceDetails"]["url"]
    print(f"Service created: {url}")

    print("Waiting for the first deploy to finish (this can take a couple of minutes)...")
    for _ in range(40):
        time.sleep(15)
        d = render_api_request("GET", f"/services/{service_id}/deploys/{deploy_id}", api_key)
        status = json.loads(d.text, strict=False).get("status")
        print(f"  status: {status}")
        if status == "live":
            return url
        if status and ("failed" in status or status == "canceled"):
            print("Deploy failed — check the Render dashboard logs.")
            return url
    print("Still deploying — check the Render dashboard.")
    return url


def render_setup(repo_url: str, client_id: str, client_secret: str) -> str | None:
    header("5. Deploying to Render")
    print(
        "This needs a Render account. If you don't have an API key, get one at:\n"
        "  https://dashboard.render.com/u/settings#api-keys\n"
    )
    if ask_yes_no("Do you have a Render API key to automate this?", default=True):
        api_key = ask("Render API key")
        if api_key:
            url = deploy_with_render_api(api_key, repo_url, client_id, client_secret)
            if url:
                return url

    print("\nManual steps instead:")
    print("  1. https://dashboard.render.com -> New -> Web Service -> connect this repo")
    print("     (render.yaml in this repo sets the build/start commands automatically)")
    print("  2. Set env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET")
    print("  3. Deploy, and note the assigned https://<name>.onrender.com URL")
    return ask("Paste your Render service URL once it's deployed (or leave blank for now)") or None


# ---------------------------------------------------------------------------
# Step 6: wire it back together
# ---------------------------------------------------------------------------


def final_instructions(render_url: str | None) -> None:
    header("6. Finish up")
    if render_url:
        callback = f"{render_url.rstrip('/')}/oauth/callback"
        mcp_url = f"{render_url.rstrip('/')}/mcp"
        print(f"1. In Google Cloud Console, set your OAuth client's redirect URI to:\n   {callback}")
        open_url("https://console.cloud.google.com/auth/clients")
        print(f"\n2. Add this as a connector URL in Claude or ChatGPT:\n   {mcp_url}")
        print("   You'll be prompted to sign in with Google — only accounts added as Test")
        print("   users in step 2c can complete that.")
    else:
        print("Once your Render service is deployed:")
        print("  1. Set its OAuth redirect URI in Google Cloud Console to")
        print("     https://<your-service>.onrender.com/oauth/callback")
        print("  2. Add https://<your-service>.onrender.com/mcp as a connector in Claude/ChatGPT")
    print("\nTo let someone else use it: add their Google account as a Test user in")
    print("Google Cloud Console (step 2c). Nothing else needs to change.")


def main() -> None:
    header("tasks-mcp setup wizard")
    check_prerequisites()
    client_id, client_secret = google_cloud_console_setup()
    write_local_env(client_id, client_secret)
    repo_url = ensure_github_repo()
    render_url = render_setup(repo_url, client_id, client_secret) if repo_url else None
    final_instructions(render_url)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
