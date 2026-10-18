# Security and privacy

Dotiphant keeps a copy of your Telegram chats on your computer and lets AI apps read it. This page covers what is stored, who can reach it, and how to remove it.

## What is stored where

| What | Where |
| --- | --- |
| Messages, chat names, sender names | `dotiphant.db`, a SQLite file in the data folder |
| Downloaded photos and files | `media/` in the data folder, only for chats where you asked for media |
| Your choices, including which chats AI apps may read | `settings.json` in the data folder |
| Approved remote apps, hashed tokens, open approval requests, pairing code | `oauth.json` in the data folder, readable only by you |
| Sync progress | `status.json` and `logs/` in the data folder. These never contain message text, phone numbers or secrets |
| Telegram API id and hash, login session, HTTP MCP token, viewer key | The system keychain, under "Dotiphant (unofficial Telegram archive)" |

The data folder is `~/Library/Application Support/Dotiphant` on macOS, `~/.local/share/dotiphant` on Linux and `%APPDATA%\Dotiphant` on Windows, or `DOTIPHANT_HOME` if set. Only your user account can read it. `dotiphant status` prints the path.

Without a keychain (for example on a headless Linux server), secrets go to `secrets/` in the data folder, readable only by your user. `DOTIPHANT_SECRETS=file` always uses these files, for example in a container. `dotiphant status` shows which store is in use.

The archive is not encrypted beyond your disk encryption. Turn on FileVault (macOS), BitLocker (Windows) or full-disk encryption (Linux).

## The session is your account

The stored Telegram session gives full access to your account, like a logged-in Telegram Desktop. Anyone who can read your keychain or your user account can use it. Protect your computer like your phone: a login password, screen lock and disk encryption.

You can end the session at any time in the Telegram app under Settings > Devices, where it shows as "Dotiphant".

A login belongs to the API id and hash that made it. When you replace them (`dotiphant setup --new-credentials`, other values in `dotiphant setup`, or new values on `/setup`), Dotiphant first tries to end the old session, so old entries do not pile up under Settings > Devices. If Telegram cannot be reached, it says so; end the old entry there by hand.

## Verify a download

Dotiphant holds your Telegram session, so check that what you install is what the project released. Each [GitHub release](https://github.com/Zierold-Ventures/dotiphant/releases) has four files:

- `dotiphant-X.Y.Z-py3-none-any.whl`, the wheel that gets installed.
- `dotiphant-X.Y.Z.tar.gz`, the source archive.
- `constraints.txt`, the exact dependency versions the release was tested with.
- `SHA256SUMS`, the SHA-256 checksums of the other three.

The installer (`install.sh`) downloads the wheel, `constraints.txt` and `SHA256SUMS` from the same release, compares them and prints the hash. On a mismatch or a missing file it stops and installs nothing. If the GitHub CLI (`gh`) is installed and logged in, it also runs the attestation check below and reports the result.

### Check the checksum by hand

Download the wheel and `SHA256SUMS` from the release page into one folder. In that folder, run:

```sh
shasum -a 256 -c --ignore-missing SHA256SUMS   # macOS
sha256sum -c --ignore-missing SHA256SUMS       # Linux
```

Each file you downloaded should show `OK`.

A checksum from the same release page catches a broken or swapped download. It cannot prove who built the file: anyone who can change the release can change both.

### Check the build attestation

Each release also has a build attestation (SLSA build provenance) for the wheel, the source archive, `constraints.txt` and `SHA256SUMS`. GitHub Actions creates it during the build. It records which repository, workflow and commit produced each file, and is signed with [Sigstore](https://www.sigstore.dev/). Check it with the GitHub CLI:

```sh
gh auth login   # once
gh attestation verify dotiphant-X.Y.Z-py3-none-any.whl --repo Zierold-Ventures/dotiphant
```

A passing check means the file was built by the release workflow of `Zierold-Ventures/dotiphant` and has not changed since.

## Read-only toward Telegram

Dotiphant only reads. It never sends, edits, deletes, forwards or reacts to messages, never joins or leaves chats and never marks messages as read. Requests are paced and Dotiphant waits whenever Telegram asks it to slow down.

Syncing never deletes messages from the archive. When a sync sees that a message was edited, Dotiphant keeps the earlier text too. A sync looks for edits in a chat's latest 100 messages, when the chat has new messages or when you sync it on its own (`dotiphant sync CHAT`, or Refresh chat in the viewer). Edits to older messages are not fetched. To remove a chat on purpose, for example when someone asks you to delete their messages, run `dotiphant forget CHAT`. It deletes the chat, its messages and its media from the archive and stops syncing it. It also removes the names of people who wrote only in that chat, and overwrites the deleted text in the database file and its write-ahead log, so no copy stays on disk.

## Who can reach the archive

- The viewer (`dotiphant open`) listens on 127.0.0.1, so only programs on your computer can reach it.
- The viewer also needs a key, so other programs and other user accounts on the same computer cannot read the archive. `dotiphant open` opens a link with the key once; the viewer swaps it for a browser cookie and removes it from the address bar. The cookie holds a random session value, not the key. It works only as a cookie and stops working when the viewer stops; the next `dotiphant open` starts a new session. `dotiphant open --no-browser` prints the link instead. Scripts can send the key as `Authorization: Bearer KEY`. The key is created on first use and kept in the keychain as `viewer_token`.
- The viewer accepts changes (refresh, favorites) from a browser only from its own pages: every `/api/` request and every POST must carry the page token of a viewer page, in addition to the cookie. The page token protects against other web pages open in your browser. It does not protect against programs: a program that has the cookie can load a viewer page and read the page token from it.
- The browser setup at `/setup` follows the same rules. It also never sends the API hash back to the page, keeps the two-step password and login code in memory only until Telegram has checked them, limits login attempts, and is off in the demo.
- Every viewer response carries `X-Dotiphant-Port-Owner: dotiphant`, `X-Dotiphant-Build` (a short hash of the installed version and files) and `X-Dotiphant-Folder` (an HMAC of the data folder path, keyed with the viewer key). `dotiphant open` and `dotiphant stop` use these headers to tell a Dotiphant viewer from another program, and send the viewer key only to a viewer whose folder header matches. Neither command stops a process by its pid.
- `POST /api/shutdown` stops the viewer. It needs `Authorization: Bearer KEY` with the viewer key and a matching Origin; the cookie and page token are not enough.
- Anyone who captures the viewer cookie can read the archive and make changes until the viewer stops. Browsers send cookies for 127.0.0.1 to every port on 127.0.0.1, so on a computer shared with other people, another user's program on another port could receive your viewer cookie. The cookie never reveals the viewer key, and it cannot stop the viewer. Use the viewer on a computer only you use, and stop it (Ctrl+C or `dotiphant stop`) when you are done.
- The MCP server normally runs over stdio: your AI app starts it as a child process and nothing listens on the network.
- `dotiphant mcp --http` listens on 127.0.0.1 and requires a token on every request. Anyone with a valid token can read your whole archive.
  - Apps that support OAuth (ChatGPT, claude.ai) get their own token after you approve them. Approved apps and hashed tokens are kept in `oauth.json`; `dotiphant mcp --clients` lists them and `dotiphant mcp --revoke` removes them.
  - Apps that can set headers can use the static bearer token instead. It is created on first use and kept in the keychain; `dotiphant mcp --show-token` prints it.
  - `--public-url` is for an HTTPS tunnel you run; see [remote-access.md](remote-access.md).
  - `--host ADDRESS --allow-remote` makes it listen on another address, so other machines can connect directly. Use it only on a network you control.

## Approving remote apps

Anyone who finds your tunnel URL can register an app and open an approval page. Only you can approve it, in one of two ways:

- In a terminal (safer): the page shows a command like `dotiphant mcp --approve K7QM-2XPA`. It prints the app name and the exact address your browser returns to, and asks y/N. It needs an interactive terminal. Then choose Continue on the page.
- With the pairing code that only your terminal shows (`dotiphant mcp --pair`). It has 8 digits, works once and expires after 30 minutes.

The page shows the full return address of this request, with its host highlighted. The authorization goes to that address, so approve only if the host belongs to the app you are connecting right now. The app name is chosen by the app and proves nothing.

Limits that keep strangers out without locking you out:

- Return addresses must use `https://`, or `http://` on 127.0.0.1, localhost or [::1]. Addresses with a user name (`user@host`) or a fragment (`#...`) and other schemes such as `javascript:` or `data:` are refused. An app may register at most 5 addresses of up to 500 characters, and a name of up to 100 characters. Other metadata such as logos is not stored or shown. Oversized requests are refused.
- Five wrong pairing codes close that one approval request. After 20 wrong codes within 10 minutes, code entry pauses for 10 minutes for everyone. Terminal approval keeps working during the pause. At most about 40 guesses fit into one code's lifetime, out of 100 million possible codes.
- At most 50 unapproved apps and 50 open approval requests are kept. A flood of new ones pushes out the oldest, so it cannot block a real app for long. It can push out a request you just started; start connecting again if the page says the link expired.
- Every refresh replaces the app's tokens. If an old refresh token is ever used again, Dotiphant assumes it was copied and revokes every token of that approval. The app then has to be approved again.
- `oauth.json` is changed under a file lock, so `dotiphant mcp --revoke` and a running server never overwrite each other.

Remaining risks: if you approve a request without checking the return address, the app at that address gets read access. A stranger can also keep pausing code entry; use terminal approval then.

## What an AI app receives

When you ask an AI app a question, it calls Dotiphant's tools and receives the matching messages: text, sender names, chat names and dates. The app then sends that content to its provider (Anthropic, OpenAI, Cursor and so on) to answer you, under that provider's privacy terms. Dotiphant cannot control what happens after that. Only connect apps whose provider you trust with your chats.

You choose which chats AI apps may read (`settings.json`, keys `ai_access`, `ai_hidden_chats` and `ai_allowed_chats`):

- `dotiphant ai mode all` (the default): every chat except the hidden ones.
- `dotiphant ai mode selected`: only the chats you allow.
- `dotiphant ai hide CHAT` and `dotiphant ai show CHAT`, or the "Visible to AI apps" switch on a chat's page in the viewer, change one chat.

Every MCP tool applies the same filter. Hidden chats are left out of chat lists, search results, snippets, "did you mean" suggestions, recent activity and the "waiting for your reply" list. Reading a hidden chat, asking for its details or refreshing it gets the same answer as for a chat that does not exist, whether the AI app uses the chat id, the @username, the full title or part of it. So an AI app cannot learn that a hidden chat exists or what it is called. A group and the basic group it was upgraded from are hidden or allowed together. The MCP server reads `settings.json` on every request, so a change applies without a restart. If `settings.json` exists but cannot be read, AI apps see no chat at all.

The filter limits what AI apps receive. It does not limit the viewer or the `dotiphant` command, which are your own tools. A hidden chat stays in the archive; `dotiphant forget CHAT` deletes it.

All tools are read-only, except `refresh`, which runs a sync to fetch new messages (still only reading from Telegram). `refresh` starts at most one sync per 60 seconds; otherwise it reports the last result and when the next refresh is allowed. This keeps a looping or tricked AI app from hammering Telegram.

## Prompt injection

Anyone who can message you can put text like "ignore previous instructions" into your archive. Dotiphant marks every message it hands to an AI as untrusted content and tells the AI not to follow instructions inside it. The marker lines use runs of three `<` or `>`; any such run inside a message is spaced out, so a message cannot fake the end of the untrusted block. This lowers the risk but does not remove it: the AI model makes the final decision. Dotiphant's tools cannot send messages or change anything, so a tricked AI still cannot act in Telegram through Dotiphant. Be careful when the same AI app also has tools that can send email, run commands or move money.

## Wiping everything

```sh
dotiphant schedule remove              # stop background refresh
dotiphant logout --forget-credentials  # end the session on Telegram, remove every stored secret
```

`--forget-credentials` removes the session, the API id and hash, the viewer key, the MCP token and the approved remote apps (`oauth.json`). If Telegram cannot be reached, the command says so: end the session in the Telegram app under Settings > Devices. Running viewers and a running `dotiphant mcp --http` check the stored keys every few seconds. Within a few seconds of the command, they stop accepting the old keys and tokens, on any port, and a viewer shuts down at its next request. The one exception is a token passed with `--token`: it works until you stop that server.

Then delete the data folder (the path is shown by `dotiphant status`). If you tried `dotiphant demo`, also delete the demo folder next to it ("Dotiphant Demo", or "dotiphant-demo" on Linux). To remove Dotiphant from your AI apps, delete the `dotiphant` entry from their MCP settings, or for Claude Code run `claude mcp remove dotiphant`. Finally, run `uv tool uninstall dotiphant` (or `pipx uninstall dotiphant`).

## Telegram terms

Dotiphant uses the public Telegram API with API credentials that you create yourself, so you are the developer of record under the [Telegram API Terms of Service](https://core.telegram.org/api/terms). In practice:

- Use it for your own account and your own chats.
- Dotiphant does not train AI models. Using Telegram data with AI apps is subject to the [Telegram API Terms of Service](https://core.telegram.org/api/terms) and the [Terms for Content Licensing and AI Scraping](https://telegram.org/tos/content-licensing). Check them before you connect an AI app; you are responsible for how you use your archive.
- Do not present Dotiphant or anything built on it as an official Telegram app.

Dotiphant is not affiliated with Telegram.
