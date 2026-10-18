# FAQ

## Can my account get banned?

Dotiphant logs in the same way Telegram Desktop does, only reads, and slows down whenever Telegram asks. Read-only, paced use of your own account is what the API is meant for. No third-party client can promise zero risk, though, and brand new accounts or accounts with a history of spam reports are watched more closely. Use your own API id and hash, and don't run several archivers on one account at the same time.

## Are secret chats included?

No. Secret chats are end-to-end encrypted between two devices and never reach Telegram's servers in readable form, so no other app can read them.

## Why are channels off by default?

Channels are often large and are already public or easy to reread in Telegram. Turn them on with `dotiphant setup --reconfigure`.

## What about WhatsApp, Signal or Slack?

Dotiphant syncs Telegram only. You can import WhatsApp chats from WhatsApp's own export files; see below. Signal and Slack are not supported.

## How do I keep a chat out of the archive?

Add its id to `excluded_chats` in `settings.json` in the data folder. Find the id with `dotiphant chats`. Messages already archived stay in the archive; Dotiphant never deletes stored messages.

## How do I keep a chat away from AI apps?

Run `dotiphant ai hide CHAT`, or turn off "Visible to AI apps" on the chat's page in the viewer. AI apps then cannot list, search or read that chat. To let AI apps read only the chats you pick, run `dotiphant ai mode selected` and allow each chat with `dotiphant ai show CHAT`. `dotiphant ai` shows what is hidden. You still see every chat in the viewer and the CLI. See [privacy.md](privacy.md).

## Where does my data live?

In one folder on your computer: `~/Library/Application Support/Dotiphant` on macOS, `~/.local/share/dotiphant` on Linux, `%APPDATA%\Dotiphant` on Windows. `dotiphant status` shows the path. Secrets are in the system keychain. See [security.md](security.md).

## Can I archive two Telegram accounts?

Yes, each in its own data folder. An archive belongs to the account that first synced it, or whose full Telegram export was imported first. Dotiphant refuses to sync, download files, log in or import an export for another account. Chat and message ids repeat across accounts, so a shared archive would mix them up. For a second account, set `DOTIPHANT_HOME` to another folder before you run `dotiphant setup`.

## How do I get older history?

The first sync loads the last 365 days by default. Run `dotiphant sync --all-history` once for everything, or change the default with `dotiphant setup --reconfigure`. Large accounts can take a while; you can stop with Ctrl-C and the next sync continues where it stopped.

## Can I import a Telegram Desktop export?

Import is experimental: it is tested with sample files, not yet with many real exports. If a file does not import, [open an issue](https://github.com/Zierold-Ventures/dotiphant/issues).

Yes. You can try Dotiphant on your real chats without API credentials or a login, or merge exports you already have. In Telegram Desktop, open a chat and choose "Export chat history", or use Settings > Advanced > "Export Telegram data". Pick the format "Machine-readable JSON". Then run:

```
dotiphant import ~/Downloads/Telegram\ Desktop/ChatExport_2024-01-31
```

The path can be the export folder or its `result.json`. Add `--copy-media` to copy exported photos and files into the archive. Importing again is safe: messages already in the archive, imported or synced, are kept as they are, and nothing is deleted. A later `dotiphant sync` uses the same chat and message ids, so it adds new messages without duplicates.

A full account export knows who you are, so your own messages are marked as yours. For a single-chat export, pass `--me YOUR_USER_ID`, or run one live sync first. If you forgot, import the same export again with `--me`. Messages from before a group was upgraded to a supergroup are left out, because the export does not say which old group they came from; a live sync fetches them. The whole file is read into memory, so very large account exports (above a few hundred MB) work better exported chat by chat.

## Import WhatsApp chats

Import is experimental: it is tested with sample files, not yet with many real exports. If a file does not import, [open an issue](https://github.com/Zierold-Ventures/dotiphant/issues).

Dotiphant reads the files WhatsApp's own "Export chat" makes. Nothing connects to WhatsApp, and Dotiphant never logs in to it.

1. Export the chat. On iPhone: open the chat, tap the contact or group name, then Export Chat. On Android: open the chat, tap the three dots, then More > Export chat. Choose "Attach media" if you want photos and files too.
2. Save the .zip (or the .txt) on your computer.
3. Run:

```
dotiphant import "WhatsApp Chat - Clara.zip" --me "Your Name"
```

The path can be the .zip, the chat .txt, or the unpacked folder. Dotiphant detects WhatsApp exports; `--source whatsapp` forces it. `--me` is your name exactly as the export shows it, so your own messages are marked as yours. `--copy-media` copies attached files from the .zip or folder into the archive.

Supported: iPhone and Android exports, 12 and 24 hour clocks, day-first and month-first dates, messages over several lines, system lines like "X added Y", media placeholders, deleted messages and the "edited" marker. A chat with more than two people, or with group events, is stored as a group.

Times. The export has local times without a time zone. Dotiphant reads them in your computer's time zone. If the export came from a phone in another zone, add `--tz Europe/Berlin` (any IANA zone name). Use the same `--tz` for every export of a chat.

Dates. Dotiphant tells day-first from month-first by looking at the whole file. If the file cannot tell (for example only dates like 01/02), it says so and uses day-first. Import again with `--date-order mdy` if dates look wrong.

Importing again. The chat name, taken from the file name or from `--title`, decides which chat the messages go to. Importing the same export again adds nothing. A newer export of the same chat adds only the messages that are not stored yet: a message counts as stored when its time, sender and text match one in the archive. A message edited between two exports is added again in its new form. Nothing is ever deleted.

WhatsApp chats are never synced: `dotiphant sync` leaves them alone, and only `--copy-media` brings their files.

## Photos and files are missing

Media is downloaded on demand to save space. Use the button in the viewer, or `dotiphant media CHAT`.

## Does it work with ChatGPT?

Yes, through remote access. ChatGPT and claude.ai on the web and phone only talk to MCP servers on the internet over HTTPS, so they need a tunnel you run. Run `dotiphant mcp --http --public-url URL` behind ngrok, Tailscale Funnel or Cloudflare Tunnel, add the URL as a connector, and approve it with `dotiphant mcp --approve` in your terminal or with the pairing code from `dotiphant mcp --pair`. Step by step: [remote-access.md](remote-access.md). Claude Desktop, Claude Code, Codex and Cursor work locally with `dotiphant connect`.

## How do I open a message an AI app cites?

Every message has a short ref like `dp-3k9xq`, unique across your whole archive. AI apps cite messages by it and link them to your viewer: `http://127.0.0.1:8765/m/dp-3k9xq`. You can also paste the ref into the viewer's search. In the viewer, hover a message's time to see its ref.

Telegram's own message numbers, like #40120, repeat across chats. Searching `#40120` lists every chat that has a message with that number.

## The AI app doesn't see Dotiphant

Quit the app completely and start it again after `dotiphant connect`. Then run `dotiphant doctor`. If the app is not supported by `dotiphant connect`, `dotiphant connect NAME --print` shows the configuration to add by hand.

## dotiphant open says the address is in use

Another program is listening on the viewer's port (8765, or 8764 for the demo). Often it is a viewer you started earlier and left running, maybe in another terminal.

- A viewer for the same data folder and the same version: `dotiphant open` reuses it and opens its link.
- An older viewer for the same data folder, for example one still running from before an update: `dotiphant open` asks it to stop and starts the updated one. `dotiphant stop` stops it without starting a new one.
- A viewer for another data folder, or another program: `dotiphant open` leaves it alone and uses the next free port, like 8766. With `--port`, it uses only that port and stops with a message if the port is busy.

If a viewer does not stop, press Ctrl+C in the terminal where it runs. To find the program that holds a port, run `lsof -i :8765` (macOS and Linux). Or start the viewer on another port: `dotiphant open --port 8770`.

## How do I remove everything?

See "Wiping everything" in [security.md](security.md).
