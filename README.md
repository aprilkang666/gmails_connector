# gmail-mcp

Local MCP server for managing multiple Gmail accounts from Claude Code.

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

Edit `config.json` and replace the example addresses with your real Gmail accounts:

```json
{
  "accounts": {
    "personal": "your.personal@gmail.com",
    "work": "your.work@company.com"
  }
}
```

The left side (`personal`, `work`) is the alias Claude will use. You can name these anything.

### 3. Export your Google OAuth credentials

From the OAuth client you created in Google Cloud Console:

```bash
export GOOGLE_CLIENT_ID="...apps.googleusercontent.com"
export GOOGLE_CLIENT_SECRET="GOCSPX-..."
```

(These also go into the Claude Code config — see step 5.)

### 4. Authorize each account

Run once per account:

```bash
uv run python -m gmail_mcp.setup_auth add personal
uv run python -m gmail_mcp.setup_auth add work
```

A browser opens for each. Sign in with the matching Gmail account and approve.

Verify:

```bash
uv run python -m gmail_mcp.setup_auth list
```

### 5. Wire into Claude Code

Add to `~/.claude.json` (or run `claude mcp add` if that CLI command exists in your version):

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
        "GOOGLE_CLIENT_SECRET": "GOCSPX-..."
      }
    }
  }
}
```

Restart Claude Code, then run `/mcp` to confirm the `gmail` server shows up with its tools.

## Usage

Ask Claude Code things like:

- "What accounts do I have configured?" (→ `list_accounts`)
- "Find unread emails from my boss in my work account" (→ `search_emails`)
- "Across all my accounts, any unread Stripe receipts this month?" (→ `search_all_accounts`)
- "Draft a reply to that email saying I'll review by Friday" (→ `create_draft`)
- "Send it." (→ `send_draft`)
- "Archive messages older than 30 days labeled 'Newsletter' in personal" (→ `search_emails` + `modify_labels`)

## Safety design

- **Draft-then-send.** Claude never sends directly. It creates a draft, and you explicitly tell it to send.
- **Explicit account scoping.** Every tool requires an `account` argument. No implicit "default" account.
- **Local tokens.** Credentials live in `~/.gmail-mcp/tokens.db`. They never leave your machine.
- **Narrow scopes.** Read, compose, send, modify. No admin-level access.

## Troubleshooting

- **"Unverified app" warning** on first sign-in: expected if your OAuth project is in Testing mode or published but unverified. Click "Advanced → Go to [app] (unsafe)" — it's your own app.
- **Refresh tokens expire after 7 days**: you're in Testing mode. Either re-run `setup_auth add` weekly, or publish the app (unverified is fine) in the Google Auth Platform → Audience tab.
- **`GOOGLE_CLIENT_ID ... must be set`**: the env vars aren't reaching the server. Check the `env` block in your Claude Code config.
