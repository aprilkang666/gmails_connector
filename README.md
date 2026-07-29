# gmail-mcp

Local MCP server for managing multiple email accounts from Claude Code. Search, read, draft, send, label, and pull attachments across several mailboxes at once.

Two ways to connect an account:

| | App password (`imap`) | OAuth (`gmail` / `outlook`) |
|---|---|---|
| Setup time | ~2 minutes | ~30 minutes |
| Cloud project / app registration | not needed | required |
| Works with | Gmail, iCloud, Fastmail, Yahoo, Zoho, QQ, 163, Alibaba, self-hosted | Gmail, Outlook / Microsoft 365 |
| Access granted | whole mailbox | narrow scopes |
| Credential at rest | password in local SQLite | OAuth refresh token |

**Start with the app-password path.** Use OAuth if you need Outlook / Microsoft 365 (Microsoft has disabled password-based IMAP), or if you'd rather grant narrow scopes than a full-mailbox app password.

---

## Quick start (app password)

### 1. Install

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/). Python 3.11+ is required, and `uv` fetches it for you if your system Python is older.

```bash
git clone https://github.com/aprilkang666/gmails_connector.git
cd gmails_connector
uv sync
```

### 2. Configure your accounts

Create `~/.gmail-mcp/config.json`. Each alias is whatever name you'd like Claude to use:

```json
{
  "accounts": {
    "personal": { "email": "you@gmail.com", "provider": "imap" },
    "side": { "email": "you@fastmail.com", "provider": "imap" }
  }
}
```

Server hostnames are inferred from the email domain — nothing else to fill in for
`gmail.com`, `googlemail.com`, `icloud.com`, `me.com`, `mac.com`, `fastmail.com`,
`yahoo.com`, `ymail.com`, `zoho.com`, `qq.com`, `163.com`, `aliyun.com`.

For a **custom domain**, name the preset that hosts it:

```json
{ "email": "you@your-company.com", "provider": "imap", "preset": "alibaba" }
```

Presets: `gmail`, `icloud`, `fastmail`, `yahoo`, `zoho`, `qq`, `163`, `alibaba`.

For anything else, set the hosts directly (ports default to 993 / 465; use `"smtp_port": 587` for STARTTLS):

```json
{
  "email": "you@example.com", "provider": "imap",
  "imap_host": "mail.example.com", "smtp_host": "mail.example.com"
}
```

### 3. Generate an app password

- **Gmail** — 2-Step Verification must be on, then create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Paste it with or without spaces.
- **iCloud** — [account.apple.com](https://account.apple.com) → Sign-In and Security → App-Specific Passwords.
- **Fastmail / Yahoo / Zoho / QQ / 163** — same idea, in each provider's security settings. QQ and 163 call it an "authorization code" and may require enabling IMAP first.

### 4. Save the credential

Run once per alias:

```bash
uv run python -m gmail_mcp.setup_auth add personal
uv run python -m gmail_mcp.setup_auth list
```

### 5. Wire into Claude Code

```bash
claude mcp add gmail -- uv run --directory /ABSOLUTE/PATH/TO/gmails_connector python -m gmail_mcp.server
```

Restart Claude Code and run `/mcp` to confirm the `gmail` server is listed. No environment variables are needed on this path.

---

## OAuth setup

Needed for Outlook / Microsoft 365, and optional for Gmail if you prefer scoped access. You create your own OAuth client — the server never ships one.

### Gmail — Google OAuth client

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project (or pick an existing one).
2. **APIs & Services → Library** → enable **Gmail API**.
3. **APIs & Services → OAuth consent screen** → choose **External**, fill in the required fields (app name, your email). While the app stays in **Testing** mode, add your own Gmail addresses under **Test users**.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** → application type **Desktop app**. Save the **Client ID** and **Client secret**.
5. Export them in the shell where you'll run the authorize step:

   ```bash
   export GOOGLE_CLIENT_ID="...apps.googleusercontent.com"
   export GOOGLE_CLIENT_SECRET="GOCSPX-..."
   ```

In `config.json`, a bare email string means Gmail-over-OAuth:

```json
{ "accounts": { "personal": "you@gmail.com" } }
```

### Outlook / Microsoft 365 — Azure app registration

1. Open the [Azure portal](https://portal.azure.com/) → **Microsoft Entra ID → App registrations → New registration**.
2. Pick a name. For **Supported account types**, choose whichever fits your mailbox (single tenant for company accounts, "Personal Microsoft accounts" for outlook.com, or both).
3. Under **Authentication**:
   - Add a **Mobile and desktop applications** platform with redirect URI `http://localhost`.
   - Set **Allow public client flows** to **Yes** (enables the PKCE flow used by MSAL — no client secret needed).
4. Under **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add `Mail.ReadWrite`, `Mail.Send`, `MailboxSettings.ReadWrite`, `User.Read`. Grant admin consent if your tenant requires it.
5. Copy the **Application (client) ID** and **Directory (tenant) ID** from the app's Overview page. Use `common` to allow any work or personal Microsoft account, or `consumers` for personal outlook.com / hotmail.com only.
6. Export them:

   ```bash
   export MICROSOFT_CLIENT_ID="..."
   export MICROSOFT_TENANT_ID="..."   # or "common" / "consumers"
   ```

### Authorize, then register with the credentials

```bash
uv run python -m gmail_mcp.setup_auth add personal
```

A browser opens. Sign in with the account matching the alias and approve. Gmail aliases route to Google's flow and Outlook aliases to Microsoft's automatically.

Then add the same env vars to your Claude Code MCP entry in `~/.claude.json`:

```json
{
  "mcpServers": {
    "gmail": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/gmails_connector",
               "python", "-m", "gmail_mcp.server"],
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

---

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
- **Local credentials.** Everything lives in `~/.gmail-mcp/tokens.db` (created `0600`, in a `0700` directory). Nothing leaves your machine except traffic to your mail provider.
- **TLS everywhere.** IMAP over implicit TLS; SMTP over implicit TLS, or STARTTLS with certificate verification on port 587.
- **Scope depends on the auth method.** OAuth accounts are limited to read / compose / send / modify. **An app password is not scoped** — it grants full access to that mailbox, including folders this server never touches. It is stored in plaintext in the local SQLite file, so anyone who can read your home directory can read it. Prefer an app password you can revoke independently, never your account password, and revoke it in your provider's settings when you're done.

## Where files live

| Path | What |
|---|---|
| `~/.gmail-mcp/config.json` | your accounts (preferred location) |
| `./config.json` | repo-local accounts, used if the above is absent |
| `$GMAIL_MCP_CONFIG` | overrides both |
| `~/.gmail-mcp/tokens.db` | tokens and app passwords |
| `~/Downloads/gmail-mcp/` | binary attachments saved by `read_attachment` |

## Troubleshooting

- **`config.json not found`**: create `~/.gmail-mcp/config.json` (see `config.json.example`) or point `GMAIL_MCP_CONFIG` at it.
- **`Cannot determine mail servers for '<alias>'`**: the email domain has no preset. Add `"preset"` or set `imap_host` / `smtp_host`.
- **IMAP login fails with valid-looking credentials**: you used your account password instead of an app password, or 2-Step Verification isn't enabled (Gmail requires it before app passwords appear). QQ / 163 need IMAP switched on in their web settings.
- **"Unverified app" warning** on first Google sign-in: expected if your OAuth project is in Testing mode, or published but unverified. Click "Advanced → Go to [app] (unsafe)" — it's your own app.
- **Google refresh tokens expire after 7 days**: you're in Testing mode. Either re-run `setup_auth add` weekly, publish the app (unverified is fine) under Google Auth Platform → Audience, or switch that account to the app-password path.
- **`GOOGLE_CLIENT_ID ... must be set`** or **`MICROSOFT_CLIENT_ID ... must be set`**: the env vars aren't reaching the server. Check the `env` block in your Claude Code config.
- **Outlook sign-in fails with a tenant / `AADSTS` error**: `MICROSOFT_TENANT_ID` doesn't match the account type. Try `common` for any Microsoft account, or `consumers` for personal outlook.com / hotmail.com.
- **Outlook over IMAP fails**: not supported. Microsoft disabled password-based IMAP for Microsoft 365 and personal accounts; use the OAuth path.
