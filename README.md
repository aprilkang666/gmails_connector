# gmail-mcp

MCP server for managing multiple Gmail and Outlook (Microsoft 365) accounts. Run it locally for Claude Code, or remotely as a **claude.ai custom connector** so the same tools work in claude.ai, the desktop app and mobile (see [Use as a claude.ai custom connector](#use-as-a-claudeai-custom-connector)).

## Setup

### 1. Install dependencies

```bash
cd gmail-mcp
uv sync
```

### 2. Configure your accounts

```bash
cp config.json.example config.json
```

Edit `config.json` to list your accounts. Each alias is whatever name you'd like Claude to use:

```json
{
  "accounts": {
    "personal": "you@gmail.com",
    "work": { "email": "you@company.com", "provider": "outlook" }
  }
}
```

A bare email string is treated as Gmail. For Outlook / Microsoft 365 accounts, use the object form with `"provider": "outlook"`.

### 3. Create your provider OAuth client(s)

Do the step(s) for the providers you actually use.

#### Gmail — Google OAuth client

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a new project (or pick an existing one).
2. **APIs & Services → Library** → enable **Gmail API**.
3. **APIs & Services → OAuth consent screen** → choose **External**, fill in the required fields (app name, your email). While the app stays in **Testing** mode, add your own Gmail addresses under **Test users**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → application type **Desktop app**. Save the resulting **Client ID** and **Client secret**.
5. Export them in the shell where you'll run the next step:

   ```bash
   export GOOGLE_CLIENT_ID="...apps.googleusercontent.com"
   export GOOGLE_CLIENT_SECRET="GOCSPX-..."
   ```

   (You'll also put them in the Claude Code config — see step 5.)

#### Outlook / Microsoft 365 — Azure app registration

1. Open the [Azure portal](https://portal.azure.com/) → **Microsoft Entra ID → App registrations → New registration**.
2. Pick a name. For **Supported account types**, choose whichever fits your mailbox (a single tenant for company accounts, "Personal Microsoft accounts" for outlook.com, or both).
3. Under **Authentication**:
   - Add a **Mobile and desktop applications** platform with redirect URI `http://localhost`.
   - Set **Allow public client flows** to **Yes** (this enables the PKCE flow used by MSAL — no client secret needed).
4. Under **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add:
   - `Mail.ReadWrite`
   - `Mail.Send`
   - `MailboxSettings.ReadWrite`
   - `User.Read`

   Grant admin consent if your tenant requires it.
5. Copy the **Application (client) ID** and the **Directory (tenant) ID** from the app's Overview page. Use `common` if you want to allow any work or personal Microsoft account, or `consumers` for personal outlook.com / hotmail.com addresses only.
6. Export them:

   ```bash
   export MICROSOFT_CLIENT_ID="..."
   export MICROSOFT_TENANT_ID="..."   # or "common" / "consumers"
   ```

### 4. Authorize each account

Run once per alias in `config.json`:

```bash
uv run python -m gmail_mcp.setup_auth add personal
uv run python -m gmail_mcp.setup_auth add work
```

A browser opens. Sign in with the account that matches the alias and approve. The tool routes Gmail aliases to Google's OAuth flow and Outlook aliases to Microsoft's automatically.

Verify:

```bash
uv run python -m gmail_mcp.setup_auth list
```

### 5. Wire into Claude Code

Add to `~/.claude.json` (or run `claude mcp add` if your CLI has it). Include only the env vars for the providers you use:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ABSOLUTE/PATH/TO/gmail-mcp",
        "python",
        "-m",
        "gmail_mcp.server"
      ],
      "env": {
        "GOOGLE_CLIENT_ID": "...apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "GOCSPX-...",
        "MICROSOFT_CLIENT_ID": "...",
        "MICROSOFT_TENANT_ID": "..."
      }
    }
  }
}
```

Restart Claude Code, then run `/mcp` to confirm the `gmail` server shows up with its tools.

## Use as a claude.ai custom connector

The same tools can be served over HTTPS so you can add them in claude.ai under **Settings → Connectors → Add custom connector**. They then work in claude.ai on the web, in the desktop app and on mobile.

In this mode the server:

- speaks MCP over Streamable HTTP at `https://<your-host>/mcp`;
- runs its own small OAuth server, which claude.ai uses automatically (dynamic client registration + PKCE). When you click **Connect**, a page asks for your **connector password**; nothing gets a token without it;
- accepts OAuth callbacks only to `claude.ai` / `claude.com`, locks the login page for 15 minutes after 5 wrong passwords, and stores its access/refresh tokens as hashes in `connector.db`;
- returns image attachments inline instead of saving them to the server's disk.

### 1. Authorize your mail accounts locally

Do steps 1–4 above on your own computer. The Google and Microsoft sign-ins need a local browser, so they always happen there. You end up with `config.json` and `~/.gmail-mcp/tokens.db`.

### 2. Pick a password and a public HTTPS address

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"   # your CONNECTOR_PASSWORD
```

claude.ai connects from Anthropic's cloud, so the server must be reachable from the internet over HTTPS. Choose one:

**Option A: run on your own computer behind a tunnel (recommended).** Your mail tokens never leave your machine. The connector only works while the computer is on.

```bash
# Terminal 1: a stable HTTPS hostname via Cloudflare Tunnel (or ngrok / Tailscale Funnel)
cloudflared tunnel --url http://localhost:8000          # quick test: prints https://<random>.trycloudflare.com
# For a permanent hostname, create a named tunnel: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/

# Terminal 2: the connector
export CONNECTOR_BASE_URL="https://mail.example.com"   # the tunnel's public origin, no path
export CONNECTOR_PASSWORD="..."                        # from above
export GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=...   # same as for Claude Code
export MICROSOFT_CLIENT_ID=... MICROSOFT_TENANT_ID=...
uv run python -m gmail_mcp.remote                      # listens on 127.0.0.1:8000
```

A quick `trycloudflare.com` URL changes every time the tunnel restarts, and the connector in claude.ai would then need to be re-added. Use a named tunnel for anything long-lived.

**Option B: a server or container host** (a VPS, Fly.io, Render, Railway…). It's always on, but your mail tokens live on that host.

```bash
mkdir data && cp config.json ~/.gmail-mcp/tokens.db data/
docker build -t mail-connector .
docker run -d --name mail-connector -p 8000:8000 -v "$PWD/data:/data" \
  -e CONNECTOR_BASE_URL=https://mail.example.com -e CONNECTOR_PASSWORD=... \
  -e GOOGLE_CLIENT_ID=... -e GOOGLE_CLIENT_SECRET=... \
  -e MICROSOFT_CLIENT_ID=... -e MICROSOFT_TENANT_ID=... \
  mail-connector
```

The host must terminate TLS for `CONNECTOR_BASE_URL` and forward to port 8000. Keep `/data` on a persistent volume: refreshed mail tokens and the connector's own tokens are written there.

Check it's up: `curl https://mail.example.com/healthz` → `{"ok": true}`.

### 3. Add it in claude.ai

1. **Settings → Connectors → Add custom connector.** On Team / Enterprise plans an owner adds it under organization settings first.
2. Name: anything (e.g. "My mail"). URL: `https://mail.example.com/mcp`. Leave the advanced OAuth fields empty.
3. Click **Connect**. A "Connect your mailboxes" page opens: enter your connector password and click **Allow**.
4. In a chat, enable the connector from the tools menu and ask: "What mail accounts do I have?"

To disconnect every client at once, stop the server and delete `connector.db` (in `~/.gmail-mcp/` or `/data/`); claude.ai will ask you to connect again.

### Remote settings

| Variable | Default | Meaning |
|---|---|---|
| `CONNECTOR_BASE_URL` | required | Public HTTPS origin, no path |
| `CONNECTOR_PASSWORD` | required | Owner password, ≥ 12 characters |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address (the Docker image uses `0.0.0.0`) |
| `CONNECTOR_REDIRECT_HOSTS` | `claude.ai,claude.com` | OAuth callback hosts to accept |
| `GMAIL_MCP_HOME` | `~/.gmail-mcp` | Directory with `tokens.db` and `connector.db` |
| `GMAIL_MCP_CONFIG` | `./config.json` | Path to the account list |

Tests: `uv run pytest`.

## Usage

Ask Claude Code things like:

- "What accounts do I have configured?" (→ `list_accounts`)
- "Find unread emails from my boss in my work account" (→ `search_emails`)
- "Across all my accounts, any unread Stripe receipts this month?" (→ `search_all_accounts`)
- "Draft a reply to that email saying I'll review by Friday" (→ `create_draft`)
- "Send it." (→ `send_draft`)
- "Archive messages older than 30 days labeled 'Newsletter' in personal" (→ `search_emails` + `modify_labels`)
- "List attachments on that message" / "Pull the PDF and summarize it" (→ `list_attachments`, `read_attachment`)

## Safety design

- **Draft-then-send.** Claude never sends directly. It creates a draft, and you explicitly tell it to send.
- **Explicit account scoping.** Every tool requires an `account` argument. No implicit "default" account.
- **Local tokens.** Credentials live in `~/.gmail-mcp/tokens.db`. In local mode they never leave your machine; as a remote connector they live wherever the server runs (your computer with option A).
- **Password-gated remote access.** The custom connector issues tokens only after you type the connector password, and only to claude.ai / claude.com callbacks.
- **Narrow scopes.** Read, compose, send, modify. No admin-level access.

## Troubleshooting

- **"Unverified app" warning** on first Google sign-in: expected if your OAuth project is in Testing mode or published but unverified. Click "Advanced → Go to [app] (unsafe)" — it's your own app.
- **Google refresh tokens expire after 7 days**: you're in Testing mode. Either re-run `setup_auth add` weekly, or publish the app (unverified is fine) under the Google Auth Platform → Audience tab.
- **`GOOGLE_CLIENT_ID ... must be set`** or **`MICROSOFT_CLIENT_ID ... must be set`**: the env vars aren't reaching the server. Check the `env` block in your Claude Code config.
- **Outlook sign-in fails with a tenant / `AADSTS` error**: your `MICROSOFT_TENANT_ID` doesn't match the account type. Try `common` for any Microsoft account or `consumers` for personal outlook.com / hotmail.com addresses.

## Development

Tests are fully offline: Gmail / Graph API calls are mocked, token storage uses a temporary SQLite file, and attachments are saved under a temp dir — no real accounts, network, or `~/Downloads` writes. This includes the remote connector tests (`tests/test_connector.py`).

```bash
uv sync              # installs runtime + dev deps (pytest, ruff)
uv run pytest        # run the test suite
uv run ruff check .  # lint
```

CI (`.github/workflows/ci.yml`) runs `ruff check` and `pytest` on Python 3.11 and 3.12 for every PR and push to `main`. `ruff format` is not enforced yet.
