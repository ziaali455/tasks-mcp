# tasks-mcp

A Google Tasks MCP server: lets Claude (or ChatGPT, via its custom connectors)
create, list, complete, update and delete Google Tasks by chatting with it.
Multi-user: each person who connects signs in with their own Google account,
and every tool call acts on that account's own Tasks.

## Tools exposed

- `echo` — sanity-check tool.
- `auth_status` — whether this connection's Google account still has a usable token.
- `list_task_lists` — the signed-in user's Google Tasks lists (id, title).
- `list_tasks(tasklist_id, show_completed, due_min, due_max)` — returns id/title/notes/due/status/tasklist_id for each task, so the model can resolve a natural-language reference ("the dry cleaning one") itself.
- `create_task(title, tasklist_id, notes, due)`
- `update_task(task_id, tasklist_id, title, notes, due)`
- `complete_task(task_id, tasklist_id)`
- `delete_task(task_id, tasklist_id)`

## How sign-in works

There's one Google Cloud OAuth client behind this server, but each person who
connects a client (Claude, ChatGPT) does their own Google sign-in, and the two
are chained into a single hop from the user's point of view:

1. Claude/ChatGPT hits `/authorize` (standard OAuth 2.1 + PKCE, with dynamic
   client registration at `/register` — that's what lets Claude's connector UI
   treat this as a real OAuth-protected server instead of refusing it).
2. `/authorize` immediately redirects to Google's own consent screen.
3. Google redirects back to `/oauth/callback`, which looks up the signed-in
   account's email via the OpenID userinfo endpoint, stores that account's
   Google Tasks token keyed by its Google user id, and completes the original
   `/authorize` request — issuing the connector an access token tied to that
   Google account (`AccessToken.subject`).
4. Every tool call reads that `subject` off the current request and uses that
   account's stored Google token. Nobody's task data crosses into anybody
   else's account.

While this app's Google OAuth consent screen is in **Testing** status (the
default, no Google review needed), only Google accounts explicitly added as
Test users in Google Cloud Console can get past step 2 at all — that's the
actual access control, not anything this server invents. To let someone else
use it, add their Google account as a Test user in Cloud Console; nothing
else needs to change here.

## Local development

```bash
uv sync
cp .env.example .env   # fill in GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
uv run tasks-mcp
```

Server listens on `http://localhost:8000`, MCP endpoint at `/mcp`.

To test locally end-to-end you'd need to add `http://localhost:8000/oauth/callback`
as an authorized redirect URI in Google Cloud Console, in addition to the Render one.
Not required — the deployed version is what you'll actually connect Claude/ChatGPT to.

## Deploying to Render (free tier)

### Quickest path: the setup wizard

```bash
uv run python scripts/setup.py
```

Walks you through all of the steps below interactively: the Google Cloud
Console setup (that part's manual — Google doesn't expose an API for creating
an OAuth client, so the wizard opens each page and tells you what to click),
then pushing to GitHub and deploying to Render, which it automates for you if
you give it a Render API key (from
[dashboard.render.com/u/settings#api-keys](https://dashboard.render.com/u/settings#api-keys)).
Without a key it just prints the manual steps instead. Safe to re-run.

### Manual steps

1. Push this repo to GitHub.
2. In the Render dashboard: New → Web Service → connect the repo. Render should pick
   up `render.yaml` automatically (Blueprint); if not, set manually:
   - Runtime: Python
   - Build command: `pip install uv && uv sync --frozen --no-dev`
   - Start command: `uv run tasks-mcp`
   - Plan: Free
3. Set environment variables on the service (Render dashboard → Environment):
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
4. Deploy. Note the assigned URL, e.g. `https://tasks-mcp-xxxx.onrender.com`.
   `GOOGLE_REDIRECT_URI` / `PUBLIC_BASE_URL` don't need to be set — they're derived
   from Render's own `RENDER_EXTERNAL_URL` env var.
5. In Google Cloud Console → your OAuth client → Authorized redirect URIs, replace
   the placeholder with `https://<your-render-url>/oauth/callback`.
6. In Google Cloud Console → OAuth consent screen, add each Google account that
   should be able to use this as a Test user.
7. Each person adds the connector in Claude (claude.ai → Settings → Connectors →
   Add custom connector) or ChatGPT (Settings → Connectors → Create, Developer
   mode) using `https://<your-render-url>/mcp` as the server URL, then signs in
   with their own Google account when prompted.

## Free-tier caveats

- **Cold starts**: Render free web services spin down after ~15 min idle; the first
  request after that takes a few seconds to wake up.
- **Token persistence**: Google tokens are stored in a JSON file on the instance's
  local disk, and connector tokens are in-memory — neither survives a redeploy
  (both do survive restarts/idle-spindown). A redeploy means everyone reconnects:
  acceptable for a small group of personal Test users; move to Render's free
  Postgres only if this becomes annoying in practice.

## Security note

`/mcp` is protected by a real (if minimal) OAuth 2.1 authorization server this
project implements on itself (dynamic client registration + PKCE authorization
code), which is what lets Claude/ChatGPT treat it as a proper connector rather
than refusing it as unauthenticated. The actual access control isn't anything
this server invents, though — it's Google's own Testing-mode restriction:
`/authorize` always routes through a real Google sign-in, so only accounts
added as Test users in Google Cloud Console can ever complete it and get a
token. Anyone can *register* a client (normal for DCR) and start the flow, but
without a Test-user Google account they're stopped by Google itself, before a
token is ever issued. Connector access tokens last 30 days or until the
service redeploys and aren't refreshable — reconnect the client when one expires.
