# Security policy

Dotiphant stores a copy of your Telegram chats and a login session that gives full access to your Telegram account.

## Reporting a vulnerability

Report privately through GitHub: open the repository's Security tab and choose "Report a vulnerability", or go to https://github.com/Zierold-Ventures/dotiphant/security/advisories/new.

Do not open a public issue, and do not include real message content, phone numbers, tokens or session files in your report. Made-up data from `dotiphant demo` is enough to show most problems.

We aim to reply within 7 days and to ship a fix for confirmed issues within 30 days. We credit reporters in the advisory unless you ask us not to.

## Supported versions

Only the latest release gets security fixes. To upgrade, run the installer again (`curl -fsSL https://dotiphant.com/install.sh | sh`).

## In scope

- Any way Dotiphant could send, edit, delete or mark messages as read in Telegram.
- Reading the archive, session or secrets without the viewer key, an MCP token or an approved OAuth app.
- Bypassing the viewer's page token, the setup page protections, or the HTTP MCP authentication, pairing codes and approval flow.
- Open redirects or token leaks in the OAuth flow.
- Message text, phone numbers or secrets written to logs, status files or error output.
- Data files or secret files created with permissions that let other user accounts read them.
- Message content escaping the untrusted-content markers in ways the docs say are prevented.
- Deleting or corrupting archived messages.

## Out of scope

- An AI model that follows instructions inside a message despite the untrusted-content markers. This is a known limit; see docs/security.md. Report it only if the markers themselves can be broken.
- Attacks that need control of your user account, your keychain or root on your computer.
- What AI providers do with content you send them.
- Cookies for 127.0.0.1 being sent to other local ports on a shared computer, as described in docs/security.md.
- Security problems in Telegram, Telethon or other dependencies. Report those upstream; tell us if Dotiphant needs to react.
- Running `dotiphant mcp --http --allow-remote` on an untrusted network.

Details on what Dotiphant stores and who can reach it: [docs/security.md](docs/security.md).
