# gmail-mcp

Local stdio MCP server for managing multiple Gmail and Outlook (Microsoft 365) accounts from Claude Code, Claude Desktop, Cursor, VS Code, Codex, Gemini CLI, or any other MCP client.

## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). Nothing else has to be installed or cloned — `uvx` fetches and caches the package on first run:

```bash
uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp
```

That command is what your MCP client runs (see [step 4](#4-connect-your-mcp-client)). The package provides two commands:

| Command | What it does |
|---------|--------------|
| `gmail-mcp` | The MCP server (stdio). Your MCP client starts it. |
| `gmail-mcp-auth` | One-time setup: `init`, `add <alias>`, `list`, `remove <alias>`. |

Tip: to skip the long `--from` prefix, install the commands once with `uv tool install git+https://github.com/aprilkang666/gmails_connector`, then use `gmail-mcp` / `gmail-mcp-auth` directly.

## Setup

### 1. Configure your accounts

```bash
uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp-auth init
```

This writes an example config to `~/.gmail-mcp/config.json` (it won't overwrite an existing one). Edit it to list your accounts. Each alias is whatever name you'd like the assistant to use:

```json
{
  "accounts": {
    "personal": "you@gmail.com",
    "work": { "email": "you@company.com", "provider": "outlook" }
  }
}
```

A bare email string is treated as Gmail. For Outlook / Microsoft 365 accounts, use the object form with `"provider": "outlook"`.

Config lookup order:

1. `$GMAIL_MCP_CONFIG` — full path to a config file, if set.
2. `~/.gmail-mcp/config.json`.
3. `config.json` in the repo root (legacy location for clone-based installs; still read, with a reminder to move it).

### 2. Create your provider OAuth client(s)

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

   (You'll also put them in your MCP client config — see step 4.)

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

### 3. Authorize each account

Run once per alias in your config, in the shell where you exported the client IDs from step 2:

```bash
uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp-auth add personal
uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp-auth add work
```

A browser opens. Sign in with the account that matches the alias and approve. Gmail aliases go through Google's OAuth flow and Outlook aliases through Microsoft's, automatically. Tokens are stored in `~/.gmail-mcp/tokens.db`.

Verify:

```bash
uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp-auth list
```

### 4. Connect your MCP client

Every client needs the same thing: run `uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp` over stdio, with the OAuth client env vars for the providers you use (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID`, `MICROSOFT_TENANT_ID`). Optionally add `GMAIL_MCP_CONFIG` if your config isn't at `~/.gmail-mcp/config.json`. See [Use with other clients](#use-with-other-clients) for each client's format.

For Claude Code:

```bash
claude mcp add --transport stdio \
  --env GOOGLE_CLIENT_ID="...apps.googleusercontent.com" \
  --env GOOGLE_CLIENT_SECRET="GOCSPX-..." \
  --env MICROSOFT_CLIENT_ID="..." \
  --env MICROSOFT_TENANT_ID="..." \
  --scope user gmail \
  -- uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp
```

Leave out the `--env` lines for providers you don't use. Restart Claude Code, then run `/mcp` to confirm the `gmail` server shows up with its tools.

## Use with other clients

All examples below use the server name `gmail`; drop the env vars for providers you don't use. GUI apps often don't inherit your shell `PATH`, so if a client can't find `uvx`, replace `"uvx"` with its absolute path (`which uvx` on macOS/Linux, `where uvx` on Windows).

### Claude Code

See [step 4](#4-connect-your-mcp-client). `--scope user` makes the server available in all projects; use `--scope project` to write it to a shared `.mcp.json` instead (don't commit real secrets). Note that `claude mcp add` needs another option between the last `--env` and the server name, which is why `--scope` appears there.

### Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gmail": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/aprilkang666/gmails_connector", "gmail-mcp"],
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

Claude Desktop needs absolute paths, so use the full path to `uvx` here. Fully quit and restart Claude Desktop afterwards. Logs: `~/Library/Logs/Claude/mcp*.log` (macOS), `%APPDATA%\Claude\logs` (Windows).

For a one-click install, see the Desktop Extension section.

### Cursor

Create `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "gmail": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/aprilkang666/gmails_connector", "gmail-mcp"],
      "env": {
        "GOOGLE_CLIENT_ID": "${env:GOOGLE_CLIENT_ID}",
        "GOOGLE_CLIENT_SECRET": "${env:GOOGLE_CLIENT_SECRET}",
        "MICROSOFT_CLIENT_ID": "${env:MICROSOFT_CLIENT_ID}",
        "MICROSOFT_TENANT_ID": "${env:MICROSOFT_TENANT_ID}"
      }
    }
  }
}
```

`${env:NAME}` reads the value from Cursor's environment; you can also paste literal values, or point `envFile` at a `.env` file.

### VS Code (GitHub Copilot)

Add to `~/.copilot/mcp-config.json` (all workspaces; `$COPILOT_HOME/mcp-config.json` if that's set) or `.mcp.json` at the workspace root. You can also run **MCP: Add Server** from the Command Palette.

```json
{
  "mcpServers": {
    "gmail": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/aprilkang666/gmails_connector", "gmail-mcp"],
      "env": {
        "GOOGLE_CLIENT_ID": "...apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "GOCSPX-...",
        "MICROSOFT_CLIENT_ID": "...",
        "MICROSOFT_TENANT_ID": "..."
      },
      "tools": ["*"]
    }
  }
}
```

VS Code's docs mark the older `.vscode/mcp.json` / `"servers"` format as deprecated for new Copilot configurations. Don't put real credentials in a workspace `.mcp.json` you commit.

### OpenAI Codex CLI

```bash
codex mcp add gmail \
  --env GOOGLE_CLIENT_ID="...apps.googleusercontent.com" \
  --env GOOGLE_CLIENT_SECRET="GOCSPX-..." \
  -- uvx --from git+https://github.com/aprilkang666/gmails_connector gmail-mcp
```

Or edit `~/.codex/config.toml` directly:

```toml
[mcp_servers.gmail]
command = "uvx"
args = ["--from", "git+https://github.com/aprilkang666/gmails_connector", "gmail-mcp"]

[mcp_servers.gmail.env]
GOOGLE_CLIENT_ID = "...apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-..."
MICROSOFT_CLIENT_ID = "..."
MICROSOFT_TENANT_ID = "..."
```

### Gemini CLI

Add to `~/.gemini/settings.json` (user) or `.gemini/settings.json` (project):

```json
{
  "mcpServers": {
    "gmail": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/aprilkang666/gmails_connector", "gmail-mcp"],
      "env": {
        "GOOGLE_CLIENT_ID": "$GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET": "$GOOGLE_CLIENT_SECRET",
        "MICROSOFT_CLIENT_ID": "$MICROSOFT_CLIENT_ID",
        "MICROSOFT_TENANT_ID": "$MICROSOFT_TENANT_ID"
      }
    }
  }
}
```

Gemini CLI expands `$VAR` / `${VAR}` in `env` from your shell environment (undefined variables become empty strings), so you can keep the secrets out of the file. You can also use `gemini mcp add -s user -e KEY=value ...`.

## Development

Clone the repo and run from source with `uv run`:

```bash
git clone https://github.com/aprilkang666/gmails_connector
cd gmails_connector
uv sync
uv run gmail-mcp-auth list          # or: uv run python -m gmail_mcp.setup_auth list
uv run gmail-mcp                    # or: uv run python -m gmail_mcp.server
```

To point an MCP client at your checkout, use `"command": "uv"` with `"args": ["run", "--directory", "/ABSOLUTE/PATH/TO/gmails_connector", "gmail-mcp"]`.

## Usage

Ask your assistant things like:

- "What accounts do I have configured?" (→ `list_accounts`)
- "Find unread emails from my boss in my work account" (→ `search_emails`)
- "Across all my accounts, any unread Stripe receipts this month?" (→ `search_all_accounts`)
- "Draft a reply to that email saying I'll review by Friday" (→ `create_draft`)
- "Send it." (→ `send_draft`)
- "Archive messages older than 30 days labeled 'Newsletter' in personal" (→ `search_emails` + `modify_labels`)
- "List attachments on that message" / "Pull the PDF and summarize it" (→ `list_attachments`, `read_attachment`)

## Safety design

- **Draft-then-send.** The assistant never sends directly. It creates a draft, and you explicitly tell it to send.
- **Explicit account scoping.** Every tool requires an `account` argument. No implicit "default" account.
- **Local tokens.** Credentials live in `~/.gmail-mcp/tokens.db`. They never leave your machine.
- **Narrow scopes.** Read, compose, send, modify. No admin-level access.

## Troubleshooting

- **"Unverified app" warning** on first Google sign-in: expected if your OAuth project is in Testing mode or published but unverified. Click "Advanced → Go to [app] (unsafe)" — it's your own app.
- **Google refresh tokens expire after 7 days**: you're in Testing mode. Either re-run `gmail-mcp-auth add` weekly, or publish the app (unverified is fine) under the Google Auth Platform → Audience tab.
- **`GOOGLE_CLIENT_ID ... must be set`** or **`MICROSOFT_CLIENT_ID ... must be set`**: the env vars aren't reaching the server. Check the `env` block in your MCP client config.
- **Outlook sign-in fails with a tenant / `AADSTS` error**: your `MICROSOFT_TENANT_ID` doesn't match the account type. Try `common` for any Microsoft account or `consumers` for personal outlook.com / hotmail.com addresses.
- **`No config.json found`**: run `gmail-mcp-auth init`, or set `GMAIL_MCP_CONFIG` to your config file's full path.
- **Client says `uvx` not found**: use the absolute path to `uvx` as the `command`.
