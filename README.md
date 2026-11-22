# gmail-mcp

Local MCP server for managing multiple Gmail and Outlook (Microsoft 365) accounts from Claude Code.

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
- **Local tokens.** Credentials live in `~/.gmail-mcp/tokens.db`. They never leave your machine.
- **Narrow scopes.** Read, compose, send, modify. No admin-level access.

## Troubleshooting

- **"Unverified app" warning** on first Google sign-in: expected if your OAuth project is in Testing mode or published but unverified. Click "Advanced → Go to [app] (unsafe)" — it's your own app.
- **Google refresh tokens expire after 7 days**: you're in Testing mode. Either re-run `setup_auth add` weekly, or publish the app (unverified is fine) under the Google Auth Platform → Audience tab.
- **`GOOGLE_CLIENT_ID ... must be set`** or **`MICROSOFT_CLIENT_ID ... must be set`**: the env vars aren't reaching the server. Check the `env` block in your Claude Code config.
- **Outlook sign-in fails with a tenant / `AADSTS` error**: your `MICROSOFT_TENANT_ID` doesn't match the account type. Try `common` for any Microsoft account or `consumers` for personal outlook.com / hotmail.com addresses.

## Claude Desktop Extension

The server can also be installed in Claude Desktop as a one-click extension (`.mcpb` bundle), no terminal needed for day-to-day use.

### Install

1. Download `gmail-mcp-<version>.mcpb` from the [Releases](https://github.com/aprilkang666/gmails_connector/releases) page.
2. Double-click it (or drag it onto Claude Desktop → **Settings → Extensions**) and click **Install**.
3. In the install dialog, fill in the OAuth credentials for the providers you use (all fields are optional; leave a provider's fields blank if you don't use it):
   - **Google OAuth Client ID / Secret** — from step 3 above (Gmail).
   - **Microsoft Application (client) ID / Directory (tenant) ID** — from step 3 above (Outlook).
   - **Accounts config file** — optional; defaults to `~/.gmail-mcp/config.json`.
4. Create `~/.gmail-mcp/config.json` listing your accounts (same format as step 2 above).

The extension uses Claude Desktop's built-in `uv` runtime, which installs Python and the dependencies on first launch.

### Authorize accounts from chat

Instead of running `setup_auth` in a terminal, ask Claude:

- "Run `setup_status`" — shows which config file is in use, which accounts are authorized, and which settings are missing.
- "Authorize my `work` account" — calls `authorize_account`, which opens the provider's sign-in page in your browser. Sign in with the matching account; the call returns once you approve.

Tokens are stored in `~/.gmail-mcp/tokens.db`, shared with the CLI setup, so accounts already authorized via `setup_auth` work in the extension too.

### Build the bundle yourself

Requires Node.js (for `npx`) and Python 3.11+:

```bash
scripts/build_mcpb.sh          # → dist/gmail-mcp-<version>.mcpb
```

The script stages `extension/manifest.json` with `pyproject.toml`, `uv.lock` and `src/`, runs `mcpb validate`, then `mcpb pack`. Pushing a `v*` tag runs the same script in GitHub Actions and attaches the bundle to the release.
