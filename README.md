# tasks-mcp

A Google Tasks MCP server: lets Claude (or ChatGPT, via its custom connectors)
create, list, complete, update and delete your Google Tasks by chatting with it.

## Tools exposed

- `echo` — sanity-check tool.
- `auth_status` — whether Google is connected; gives the `/oauth/login` URL if not.
- `list_task_lists` — your Google Tasks lists (id, title).
- `list_tasks(tasklist_id, show_completed, due_min, due_max)` — returns id/title/notes/due/status/tasklist_id for each task, so the model can resolve a natural-language reference ("the dry cleaning one") itself.
- `create_task(title, tasklist_id, notes, due)`
- `update_task(task_id, tasklist_id, title, notes, due)`
- `complete_task(task_id, tasklist_id)`
- `delete_task(task_id, tasklist_id)`

There are two separate OAuth hops here, easy to conflate: Google's (this server
gets permission to touch *your* Google Tasks — `/oauth/login`) and the
connector's (an MCP client like Claude gets permission to talk to *this
server* — `/login`, gated by `MCP_AUTH_TOKEN`). Claude's and ChatGPT's custom
connector UIs require the second one; they won't connect to a fully open
MCP server.

## Local development

```bash
uv sync
cp .env.example .env   # fill in GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / MCP_AUTH_TOKEN
uv run tasks-mcp
```

Server listens on `http://localhost:8000`, MCP endpoint at `/mcp`.

To test locally end-to-end you'd need to add `http://localhost:8000/oauth/callback`
as an authorized redirect URI in Google Cloud Console, in addition to the Render one.
Not required — the deployed version is what you'll actually connect Claude/ChatGPT to.

## Deploying to Render (free tier)

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
   - `MCP_AUTH_TOKEN` — the password required at the connector's own `/login` page
     (see Security below). Pick a long random string.
4. Deploy. Note the assigned URL, e.g. `https://tasks-mcp-xxxx.onrender.com`.
   `GOOGLE_REDIRECT_URI` / `PUBLIC_BASE_URL` don't need to be set — they're derived
   from Render's own `RENDER_EXTERNAL_URL` env var.
5. In Google Cloud Console → your OAuth client → Authorized redirect URIs, replace
   the placeholder with `https://<your-render-url>/oauth/callback`.
6. Visit `https://<your-render-url>/oauth/login` in a browser, sign in with the
   Google account you added as a Test user, and grant access.
7. Add the connector in Claude (claude.ai → Settings → Connectors → Add custom
   connector) or ChatGPT (Settings → Connectors → Create, Developer mode) using
   `https://<your-render-url>/mcp` as the server URL. The client will walk you
   through its own OAuth popup: that's this server's `/login` page, asking for
   `MCP_AUTH_TOKEN` — not your Google password.

## Free-tier caveats

- **Cold starts**: Render free web services spin down after ~15 min idle; the first
  request after that takes a few seconds to wake up.
- **Token persistence**: tokens are stored in a JSON file on the instance's local
  disk, which Render's free tier does not persist across redeploys (it does persist
  across restarts/idle-spindown). A redeploy means re-running step 6 above. Good
  enough for a single personal user; move to Render's free Postgres only if this
  becomes annoying in practice.

## Security note

`/mcp` is protected by a real (if minimal) OAuth 2.1 authorization server this
project implements on itself (dynamic client registration + PKCE authorization
code), which is what lets Claude/ChatGPT treat it as a proper connector rather
than refusing it as unauthenticated. The actual gate is `MCP_AUTH_TOKEN`: it's
the password required at `/login` before a client is issued a token. Anyone can
*register* as a client (that's normal for DCR) and reach the login form, but
without the password they get no token and can't call any tool. Access tokens
last 30 days or until the service redeploys (in-memory, same tradeoff as the
Google token store) and aren't refreshable — reconnect the client when one expires.
