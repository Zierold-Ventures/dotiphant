# Directory listing copy

Ready-to-paste text for MCP directories and awesome lists. The steps to publish are in [releasing.md](releasing.md), section "MCP directories".

Facts every listing must keep true:

- Dotiphant is a local (stdio) server. Users install it and log in to Telegram with their own API id. There is no hosted endpoint.
- The MCP tools only read the local archive. The one tool that talks to Telegram, `refresh`, fetches new messages and never sends, edits, deletes or marks anything as read.
- Dotiphant is unofficial and not affiliated with Telegram.

Names used below:

- Server name in the MCP registry: `io.github.Zierold-Ventures/dotiphant` (case matters, it must match the GitHub org login)
- Package: `dotiphant` (GitHub releases; not on PyPI). It installs the `dotiphant` command, plus the short alias `phant`
- Repo: `https://github.com/Zierold-Ventures/dotiphant`
- Command: `dotiphant mcp` (stdio). `dotiphant mcp --http --public-url URL` adds an HTTP mode with OAuth for remote clients.

## One-line description

Under 100 characters. The registry `description` field uses the first one (89 characters).

```text
Search and read your Telegram chats from a local, read-only archive on your own computer.
```

Shorter variant (67 characters):

```text
Local, read-only Telegram archive that your AI assistant can search
```

## Long description

```text
Dotiphant copies your Telegram chats into a single SQLite file on your own computer and lets your AI assistant search and read it over MCP. It logs in as you with your own Telegram API id, so it sees your full history, not only messages sent after a bot joined. It can also import Telegram Desktop exports and WhatsApp "Export chat" files; it never connects to WhatsApp.

Tools:
- list_chats: archived chats with kind, last activity and message count
- search_messages: full-text keyword search across all chats and file names
- get_messages: read one chat in order, the latest messages, a time range, or the context around a search hit
- recent_activity: what is new across chats, and which chats wait for your reply
- chat_info: members, forum topics, most active participants and archive coverage
- refresh: fetch new messages from Telegram into the archive (at most once a minute)

Read-only toward Telegram: Dotiphant never sends, edits, deletes or marks messages as read. No cloud service and no telemetry. The Telegram session and API keys stay in the system keychain. Message text is passed to the model as untrusted content, which lowers the risk of prompt injection but cannot rule it out.

Works with Claude Desktop, Claude Code, Codex, Cursor and other stdio MCP clients. ChatGPT, claude.ai and other apps that support remote MCP servers connect through an optional HTTP mode with OAuth and a one-time pairing code, behind a tunnel you run.

Setup: install with `curl -fsSL https://dotiphant.com/install.sh | sh` (macOS and Linux), run `dotiphant open` for a browser setup (about 5 minutes), then `dotiphant connect claude-desktop` or another app. Without a Telegram login the server still starts and lists its tools; it reports an empty archive.

macOS and Linux. Windows is experimental. Version 0.1: new, with limited real-world testing against live Telegram.

Dotiphant is an independent, unofficial app. It is not made, endorsed or supported by Telegram. Open source under the MIT license.
```

## Categories and tags

- Categories: Communication; Knowledge and Memory; Search
- Tags: telegram, whatsapp, chat, messaging, archive, search, full-text-search, local-first, privacy, read-only, sqlite, python
- Language: Python 3.12+
- Platforms: macOS, Linux. Windows is experimental
- Transport: stdio (also streamable HTTP with OAuth, self-hosted)
- Auth needed: the user's own Telegram API id and hash, and a Telegram login. No API key for Dotiphant itself.
- License: MIT

## Official MCP registry

Filled from `server.json` at the repo root. Title `Dotiphant`, description as the one-liner above. Nothing to paste by hand.

## Glama

Glama reads the repo README, `glama.json` and the registry entry. After claiming the listing, paste the build settings from the repo `Dockerfile` if Glama asks for them. Its check only needs the server to start and answer introspection; the image does that with an empty archive.

Badge for the README and the awesome list (check the exact owner/repo path on the Glama page once it exists):

```markdown
[![Zierold-Ventures/dotiphant MCP server](https://glama.ai/mcp/servers/Zierold-Ventures/dotiphant/badges/score.svg)](https://glama.ai/mcp/servers/Zierold-Ventures/dotiphant)
```

## Smithery

Smithery lists local servers as MCPB bundles. Use the one-liner as the description and the long description as the README text. Add this note at the top so nobody expects a hosted server:

```text
Local server: needs a local install and your own Telegram login (dotiphant open). Not a hosted service.
```

## mcp.so, PulseMCP and other directories

- PulseMCP ingests the official registry; no separate submission. If the listing is missing or wrong a few days after the registry entry is live, use their contact form:

  ```text
  Dotiphant (io.github.Zierold-Ventures/dotiphant) is in the official MCP registry. It is a local, read-only Telegram archive that AI assistants can search over MCP. Repo: https://github.com/Zierold-Ventures/dotiphant. Could you check the listing? Thanks.
  ```
- mcp.so: submit the repo URL with the one-liner, the long description and the tags above. Add the "Local server" note from the Smithery section.

## awesome-mcp-servers (punkpeye)

Section: Communication. One line, in this exact format. The repo link must use the full `owner/repo` name, the Glama score badge follows it, then the legend emojis (Python, local service, macOS, Linux), then ` - ` and the description. The list's own format requires these emojis.

```markdown
- [Zierold-Ventures/dotiphant](https://github.com/Zierold-Ventures/dotiphant) [![Zierold-Ventures/dotiphant MCP server](https://glama.ai/mcp/servers/Zierold-Ventures/dotiphant/badges/score.svg)](https://glama.ai/mcp/servers/Zierold-Ventures/dotiphant) 🐍 🏠 🍎 🐧 - Local, read-only archive of your own Telegram chats (and imported WhatsApp chats) with full-text search and background sync. Unofficial, not affiliated with Telegram.
```

PR title: `Add Zierold-Ventures/dotiphant`. A bot checks the PR for the Glama badge, a permitted emoji and the `owner/repo` name, and that the server is not already listed. The server must be listed on Glama and pass its checks before the PR is merged. Open the PR by hand and fill in the list's checklist; the list's opt-in tag for automated agents does not apply.

## Other awesome lists

Check each list's CONTRIBUTING file before opening a PR; formats differ. A neutral line without emoji:

```markdown
- [Dotiphant](https://github.com/Zierold-Ventures/dotiphant) - Local, read-only archive of your own Telegram chats with full-text search, exposed over MCP. Python. Unofficial, not affiliated with Telegram.
```
