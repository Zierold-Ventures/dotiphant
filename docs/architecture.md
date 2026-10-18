# Architecture

Dotiphant keeps a local copy of your Telegram chats and makes it readable by you (the viewer) and by AI assistants (the MCP server). It logs in as your own account over MTProto, the protocol the official apps use, so it sees the full history of every chat you are in. It never sends, edits or deletes anything in Telegram.

## Data flow

```
Telegram (your account)
   |  read-only MTProto, via Telethon
   v
sync.py  --writes-->  dotiphant.db (SQLite, WAL, FTS5)  <--reads--  query.py
                        ^                                 |-- web/ (viewer, 127.0.0.1)
importer*.py --writes---+                                 |-- mcp_server.py (stdio or HTTP)
(Telegram, WhatsApp exports)                              |-- cli.py (search, export)
```

`sync.py`, `importer.py` and `importer_whatsapp.py` add to the archive. `forget.py` is the only code that deletes messages, and only on `dotiphant forget`. `demo.py` writes only its own demo folder, never the real archive. Everything else opens the archive read-only, so the viewer and MCP server keep working while a sync runs. A read-only open of an archive that does not exist yet creates nothing and reads as empty.

## Modules

| Module | Role |
| --- | --- |
| `config.py` | Data directory, `Settings` (settings.json, including AI access), secrets in the OS keychain with a 0600 file fallback, API credential checks shared by both setups |
| `db.py` | Schema, connections, the list of downloadable media types. A trigger blocks deleting messages; only `forget.py` lifts it, for one chat |
| `status.py` | Reads status.json for every caller (with crashed-run detection), the sync lock, and starting background `dotiphant` processes. No Telethon import |
| `query.py` | All reads: chats, messages, search, recent activity, Markdown transcripts. `ai_scope` decides which chats AI apps may read |
| `telegram.py` | Telethon client factory and login (QR code or phone code). The session is a StringSession kept in the keychain |
| `sync.py` | Inventory of chats, incremental fetch of new messages, backfill of history, forum topics, group upgrades, on-demand media download. Sync also keeps each chat's profile photo (small version, fetched only when Telegram reports a new one, after the new messages). Writes status.json and holds the lock while it runs |
| `importer.py` | Imports Telegram Desktop JSON exports. Adds rows only, never overwrites. Holds the sync lock and skips chats in `excluded_chats` unless `--include-forgotten` |
| `importer_whatsapp.py` | Imports one chat from WhatsApp's "Export chat" (.txt or .zip). Chat and sender ids from a name hash at 9e18 and above, which the Telegram sync skips. Adds rows only. Holds the sync lock and skips excluded chats like `importer.py` |
| `forget.py` | `dotiphant forget CHAT`: adds one chat to `excluded_chats`, then deletes it, its messages, media and profile photo under the sync lock |
| `demo.py` | The made-up archive for `dotiphant demo`, in its own folder next to the data directory |
| `mcp_server.py` | Read-only MCP tools over `query.py`, plus a `refresh` tool that runs a sync. Every tool applies the AI access filter |
| `oauth.py` | OAuth 2.1 provider for `dotiphant mcp --http`: client registration with limits, approval page (terminal approval or pairing code), PKCE tokens, rotating refresh with reuse detection, revocation. State in `oauth.json`, changed under `oauth.lock` |
| `connect.py` | Writes the MCP entry into Claude Desktop, Claude Code, Codex and Cursor configs |
| `web/` | Starlette viewer: chat list, chat view, search, media, copy as Markdown, refresh button. `web/onboarding.py` is the browser setup at `/setup` (credentials, QR or phone login in a background task, archive choices, first sync, connect, schedule) |
| `cli.py` | The `dotiphant` command, including the `setup` wizard and background schedule |
| `viewer.py` | The viewer's build id and identity headers, and the port checks behind `dotiphant open`, `dotiphant demo` and `dotiphant stop`: reuse a current viewer, stop an outdated one, skip ports held by other programs |

## Data directory

`DOTIPHANT_HOME`, or by default `~/Library/Application Support/Dotiphant` (macOS), `~/.local/share/dotiphant` (Linux), `%APPDATA%\Dotiphant` (Windows). Created with 0700 permissions.

| Path | Content |
| --- | --- |
| `dotiphant.db` | The archive |
| `settings.json` | `Settings` |
| `status.json` | Current or last sync state |
| `oauth.json` | Remote apps approved for `dotiphant mcp --http`, hashed tokens, open approval requests, pairing code (0600) |
| `sync.lock` | Held while a sync, a media download, an import or a forget runs |
| `settings.lock` | Held while one process changes `settings.json` |
| `media/<chat_id>/<message_id>-<name>` | Downloaded attachments |
| `media/avatars/<chat_id>-<photo_id>.jpg` | Chat profile photos, shown in the viewer only (never to AI apps) |
| `secrets/` | Only used when no OS keychain is available |
| `logs/` | Background sync logs |

## Settings

`settings.json` holds one `Settings` object. Unknown keys are ignored, and missing keys take their defaults.

Every change goes through `config.update_settings(change)`: it takes `settings.lock`, rereads the file, applies the change and writes it through a temporary file of its own. So the viewer and a CLI command never undo each other's changes. A file that exists but cannot be read is never overwritten: the change fails with `SettingsUnreadable`.

| Key | Meaning |
| --- | --- |
| `include_private`, `include_groups`, `include_channels`, `include_bots`, `include_archived` | Which kinds of chats sync archives |
| `history_days` | How far back the first sync goes; `null` means everything |
| `excluded_chats` | Chats sync skips |
| `favorites` | Chats starred in the viewer |
| `sync_interval_minutes` | Background refresh interval |
| `ai_access` | `"all"` (default): AI apps read every chat except `ai_hidden_chats`. `"selected"`: only `ai_allowed_chats`. Any other value reads as `"selected"` |
| `ai_hidden_chats` | Chats AI apps never read, in both modes |
| `ai_allowed_chats` | Chats AI apps may read in `"selected"` mode |

## AI access filter

AI access applies to the MCP server only. The viewer and the CLI are the owner's own tools and show every chat.

`query.ai_scope(conn, settings)` returns an `AiScope`: `allows(chat_id)` and `sql(column)`, an SQL condition with the chat ids written in as integers. A chat and the group it was upgraded from or to (`query.linked_chat_ids`) share one setting. Hidden wins over allowed.

Every MCP tool builds the scope from a fresh `Settings.load()` on each call, so changes apply without a restart. If `settings.json` exists but cannot be read, the scope allows nothing. The scope is passed to `list_chats`, `search`, `recent_activity`, `similar_chats` and `resolve_chat`. With a scope, `resolve_chat` skips chats the scope does not allow, so a reference to a hidden chat raises `ChatNotFound` exactly like a reference to a chat that does not exist. AI apps cannot use exact or partial references to learn hidden chats' names. Hidden chats never appear as candidates or suggestions.

Who changes it: `dotiphant ai` (CLI), `POST /api/ai-access` and `POST /api/ai-mode` (viewer, with the same origin, cookie and page token checks as other POSTs), and the setup in both the terminal and the browser.

## Secrets

Keychain entries under service `Dotiphant (unofficial Telegram archive)`: `api_id`, `api_hash`, `session`, `viewer_token` (the viewer key), `mcp_token` (the static bearer token for `dotiphant mcp --http`). `dotiphant logout --forget-credentials` deletes all of them, and `oauth.json`. `DOTIPHANT_API_ID` and `DOTIPHANT_API_HASH` environment variables override the stored credentials. `DOTIPHANT_SECRETS=file` stores secrets in files under `secrets/` in the data folder instead (used by tests, CI and the Docker image). The default, `keyring`, uses the system keyring and falls back to files when none is available.

## Interfaces between modules

```python
# telegram.py
def make_client(receive_updates: bool = False) -> TelegramClient  # StringSession from the keychain
async def is_authorized(client) -> bool
# login_qr needs a client from make_client(receive_updates=True): QR login completes through an update
async def login_qr(client, show_url: Callable[[str], None], get_password: Callable[[], str]) -> str  # returns display name
async def login_phone(client, phone: str, get_code: Callable[[], str], get_password: Callable[[], str]) -> str
def save_session(client) -> None
async def logout() -> bool        # deletes the stored session; True when Telegram confirmed the logout

# sync.py
class SyncBusy(RuntimeError)
async def run_sync(chat_ids: list[int] | None = None, *, client=None, settings=None,
                   progress: Callable[[dict], None] | None = None, max_seconds: float | None = None) -> dict
async def run_inventory(*, client=None, settings=None) -> int   # chat list only; takes the lock; returns the chat count
async def run_avatars(chat_ids: list[int] | None = None, *, refresh: bool = False, client=None, settings=None,
                      progress=None, max_seconds=None) -> dict
# profile photos only; takes the lock; returns counts: chats, downloaded, removed, failed, missing
async def download_media(chat_id: int, message_ids: list[int] | None = None, *, client=None, hold_lock=True) -> dict
# takes the lock (SyncBusy when held); returns counts: downloaded, already_present, skipped_large, unavailable, failed

# status.py
def read_status() -> dict          # every key present; a crashed run reads as idle with stale=True
def is_running(status: dict) -> bool
def pid_alive(pid) -> bool
def lock_held() -> bool
def start_background(args: list[str], log_name: str = "sync") -> Popen   # python -m dotiphant ARGS, logs/<log_name>.log
# status.json: {"state": "idle|running|waiting|error", "stage": str, "chat": str|None, "chats_done": int,
#               "chats_total": int, "new_messages": int, "started_at": iso, "finished_at": iso|None,
#               "message": str|None, "pid": int|None, "errors": int,
#               "failed_chats": [{"chat_id": int, "error": str}]}
# A run where some chats failed ends "idle" with errors > 0 and a message; "error" means the run itself failed.

# mcp_server.py
def build_server(**auth) -> MCPServer
def run_stdio() -> None
def run_http(host: str = "127.0.0.1", port: int = 8766, token: str | None = None,
             allow_remote: bool = False, public_url: str | None = None) -> None
def http_app(token: str | None, host: str = "127.0.0.1", port: int = 8766, public_url: str | None = None,
             log: Callable[[str], None] | None = None) -> Starlette    # /mcp plus OAuth routes

# connect.py
CLIENTS: dict[str, ...]          # keys: claude-desktop, claude-code, codex, cursor
def detect() -> list[str]                              # clients that look installed
def is_connected(name: str) -> bool                    # its config already has a `dotiphant` server
def connect(name: str, dry_run: bool = False) -> str   # human-readable result
def snippet(name: str) -> str                          # config text to paste by hand

# web/app.py
def viewer_token() -> str                              # per-install viewer key, created on first use
def create_app(token: str | None = None) -> Starlette
def serve(port: int = 8765, open_browser: bool = True, path: str = "/") -> None  # opens PATH?key=TOKEN, or prints it
```

Background work started from the viewer or the MCP server runs as a separate process, `python -m dotiphant sync [...]` or `python -m dotiphant media CHAT_ID [...]`, so it survives closed pages and never blocks a request.

## Safety rules

- Read-only toward Telegram: no send, edit, delete, join or mark-as-read calls.
- Message text shown to an AI is wrapped as untrusted content, and the AI is told not to follow instructions found in messages.
- AI apps only see chats the AI access settings allow (see above).
- The viewer binds to 127.0.0.1. The HTTP MCP server binds to 127.0.0.1 unless started with `--host ADDRESS --allow-remote`. It requires the static bearer token or an OAuth token approved in the terminal (`dotiphant mcp --approve`) or with a one-time pairing code; `--public-url` allows a tunnel's host name while still binding to 127.0.0.1. The viewer requires its per-install key (`viewer_token`) on every request, either as a bearer token or through a one-time `?key=` link that sets an HttpOnly SameSite=Strict session cookie. The cookie holds a random per-run value, never the key, and is not accepted as a bearer token. The viewer accepts POSTs only from its own origin. `/api/` requests and POSTs also need the per-run page token (`X-Dotiphant-Token`), because browsers share 127.0.0.1 cookies across ports.
- Logs and status files never contain message text, phone numbers or secrets.
