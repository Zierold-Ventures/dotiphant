"""SQLite storage: one file, WAL mode so readers never block the sync, FTS5 for search.

Chat ids are Telegram "marked" peer ids (users positive, basic groups negative,
supergroups and channels -100...). Dates are ISO 8601 strings in UTC.
Stored messages are never deleted: a DELETE trigger aborts. An edit the sync sees (on
a chat's latest page) updates the text and keeps the earlier version in message_versions.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 3

# Media types that are files Telegram can send us (the rest, like polls or locations, are not).
DOWNLOADABLE = ("photo", "video", "document", "voice", "audio", "sticker")


def safe_filename(name: str) -> str:
    """A file name that is safe inside media/<chat_id>/, for synced and imported files alike."""
    name = re.sub(r"[^\w.\- ]+", "_", name).strip().lstrip(".")
    return name[:120] or "file"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS chats(
  chat_id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,              -- private | bot | group | supergroup | channel
  title TEXT NOT NULL,
  username TEXT,
  is_forum INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  member_count INTEGER,
  migrated_to INTEGER,             -- basic group upgraded into this supergroup id
  last_message_at TEXT,
  unread_count INTEGER,
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  raw_json TEXT,
  avatar_path TEXT,                -- profile photo, relative to the media dir (avatars/<chat_id>-<photo_id>.jpg)
  photo_id INTEGER                 -- Telegram's id of that photo, to notice a new one
);

CREATE TABLE IF NOT EXISTS topics(
  chat_id INTEGER NOT NULL,
  topic_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  PRIMARY KEY(chat_id, topic_id)
);

CREATE TABLE IF NOT EXISTS senders(
  peer_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  username TEXT,
  is_bot INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages(
  row_id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  topic_id INTEGER,
  sender_id INTEGER,
  out INTEGER NOT NULL DEFAULT 0,  -- 1 when the account owner sent it
  date TEXT NOT NULL,
  edit_date TEXT,
  reply_to_id INTEGER,
  forwarded_from TEXT,
  kind TEXT NOT NULL DEFAULT 'text', -- text | media | service
  text TEXT NOT NULL DEFAULT '',
  media_type TEXT,                 -- photo | video | voice | audio | document | sticker | poll | location | contact | webpage | other
  media_name TEXT,
  media_size INTEGER,
  media_path TEXT,                 -- relative to the media dir once downloaded
  service_text TEXT,               -- readable line for service messages
  raw_json TEXT,
  UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS messages_chat_date ON messages(chat_id, date);
CREATE INDEX IF NOT EXISTS messages_date ON messages(date);
CREATE INDEX IF NOT EXISTS messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS chats_migrated_to ON chats(migrated_to);

CREATE TABLE IF NOT EXISTS message_versions(
  chat_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  replaced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state(
  chat_id INTEGER PRIMARY KEY,
  newest_id INTEGER NOT NULL DEFAULT 0,  -- highest message id stored
  oldest_id INTEGER,                     -- lowest message id stored
  backfill_done INTEGER NOT NULL DEFAULT 0,
  last_sync_at TEXT,
  error TEXT
);

CREATE TRIGGER IF NOT EXISTS messages_no_delete BEFORE DELETE ON messages
BEGIN SELECT RAISE(ABORT, 'Dotiphant never deletes stored messages'); END;

-- Per-chat counters kept by triggers, so chat lists and chat cards need no count over messages.
-- chat_senders counts messages that are not service messages; sender_key is sender_id, or 0
-- when the sender is unknown.
CREATE TABLE IF NOT EXISTS chat_stats(
  chat_id INTEGER PRIMARY KEY,
  messages INTEGER NOT NULL DEFAULT 0,
  media INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_senders(
  chat_id INTEGER NOT NULL,
  sender_key INTEGER NOT NULL,
  messages INTEGER NOT NULL DEFAULT 0,
  last_message_at TEXT,
  PRIMARY KEY(chat_id, sender_key)
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS messages_stats_insert AFTER INSERT ON messages BEGIN
  INSERT INTO chat_stats(chat_id, messages, media) VALUES (new.chat_id, 1, new.media_type IS NOT NULL)
    ON CONFLICT(chat_id) DO UPDATE SET messages = messages + 1, media = media + (new.media_type IS NOT NULL);
  INSERT INTO chat_senders(chat_id, sender_key, messages, last_message_at)
    SELECT new.chat_id, coalesce(new.sender_id, 0), 1, new.date WHERE new.kind != 'service'
    ON CONFLICT(chat_id, sender_key) DO UPDATE SET messages = messages + 1,
      last_message_at = max(coalesce(last_message_at, ''), excluded.last_message_at);
END;
CREATE TRIGGER IF NOT EXISTS messages_stats_delete AFTER DELETE ON messages BEGIN
  UPDATE chat_stats SET messages = messages - 1, media = media - (old.media_type IS NOT NULL)
    WHERE chat_id = old.chat_id;
  UPDATE chat_senders SET messages = messages - 1
    WHERE chat_id = old.chat_id AND sender_key = coalesce(old.sender_id, 0) AND old.kind != 'service';
  DELETE FROM chat_senders WHERE chat_id = old.chat_id AND sender_key = coalesce(old.sender_id, 0) AND messages <= 0;
END;
CREATE TRIGGER IF NOT EXISTS messages_stats_update AFTER UPDATE OF chat_id, sender_id, kind, media_type ON messages BEGIN
  UPDATE chat_stats SET messages = messages - 1, media = media - (old.media_type IS NOT NULL)
    WHERE chat_id = old.chat_id;
  UPDATE chat_senders SET messages = messages - 1
    WHERE chat_id = old.chat_id AND sender_key = coalesce(old.sender_id, 0) AND old.kind != 'service';
  INSERT INTO chat_stats(chat_id, messages, media) VALUES (new.chat_id, 1, new.media_type IS NOT NULL)
    ON CONFLICT(chat_id) DO UPDATE SET messages = messages + 1, media = media + (new.media_type IS NOT NULL);
  INSERT INTO chat_senders(chat_id, sender_key, messages, last_message_at)
    SELECT new.chat_id, coalesce(new.sender_id, 0), 1, new.date WHERE new.kind != 'service'
    ON CONFLICT(chat_id, sender_key) DO UPDATE SET messages = messages + 1,
      last_message_at = max(coalesce(last_message_at, ''), excluded.last_message_at);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  text, content='messages', content_rowid='row_id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.row_id, new.text || ' ' || coalesce(new.media_name, ''));
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE OF text ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.row_id, old.text || ' ' || coalesce(old.media_name, ''));
  INSERT INTO messages_fts(rowid, text) VALUES (new.row_id, new.text || ' ' || coalesce(new.media_name, ''));
END;
"""


def readonly_uri(path: Path) -> str:
    """An SQLite URI that opens `path` read-only. The path is percent-encoded, so a '#', '?' or
    '%' in a folder name cannot cut the path short or drop mode=ro."""
    return Path(path).absolute().as_uri() + "?mode=ro"


def connect(path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open the database. Read-only connections are used by the viewer, MCP server and CLI.

    A read-only open of an archive that does not exist yet creates nothing: it returns an
    empty in-memory archive, so readers show empty states.
    """
    path = path or (config.data_dir_path() / "dotiphant.db" if readonly else config.db_path())
    if readonly:
        if path.exists():
            conn = sqlite3.connect(readonly_uri(path), uri=True, timeout=30, check_same_thread=False)
        else:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA query_only=ON")
    else:
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Fills the version 2 counters from the messages already stored. Runs in the same
# transaction that creates their triggers, so no message is counted twice or missed.
BACKFILL_V2 = """
DELETE FROM chat_stats;
DELETE FROM chat_senders;
INSERT INTO chat_stats(chat_id, messages, media)
  SELECT chat_id, count(*), sum(media_type IS NOT NULL) FROM messages NOT INDEXED GROUP BY chat_id;
INSERT INTO chat_senders(chat_id, sender_key, messages, last_message_at)
  SELECT chat_id, coalesce(sender_id, 0), count(*), max(date) FROM messages NOT INDEXED
  WHERE kind != 'service' GROUP BY chat_id, coalesce(sender_id, 0);
"""


def init(path: Path | None = None) -> None:
    """Create the schema, or upgrade an older archive to SCHEMA_VERSION. Upgrades only add."""
    path = path or config.db_path()
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        try:
            has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() if has_meta else None
            version = int(row[0]) if row else None
            for statement in _statements(SCHEMA):
                conn.execute(statement)
            if version is None or version < 2:
                conn.execute("PRAGMA cache_size=-65536")  # 64 MB while counting existing messages
                for statement in _statements(BACKFILL_V2):
                    conn.execute(statement)
            # Version 3: chat profile photos. CREATE TABLE IF NOT EXISTS leaves an older chats table
            # as it is, so the columns are added here when missing.
            columns = {r[1] for r in conn.execute("PRAGMA table_info(chats)")}
            for column, kind in (("avatar_path", "TEXT"), ("photo_id", "INTEGER")):
                if column not in columns:
                    conn.execute(f"ALTER TABLE chats ADD COLUMN {column} {kind}")
            if version is None or version < SCHEMA_VERSION:
                set_meta(conn, "schema_version", str(SCHEMA_VERSION))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _statements(script: str) -> list[str]:
    """Split a script into statements (trigger bodies included) for use inside one transaction."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        if line.lstrip().startswith("--") and not buf.strip():
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def has_stats(conn: sqlite3.Connection) -> bool:
    """True when the per-chat counters exist (schema version 2 or later)."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_stats'").fetchone() is not None


def has_avatars(conn: sqlite3.Connection) -> bool:
    """True when chats has the profile photo columns (schema version 3 or later)."""
    return any(r[1] == "avatar_path" for r in conn.execute("PRAGMA table_info(chats)"))


def open_rw(path: Path | None = None) -> sqlite3.Connection:
    init(path)
    return connect(path)


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


OTHER_ACCOUNT = ("This archive belongs to another Telegram account. Log in with that account, "
                 "or use a separate data folder (DOTIPHANT_HOME).")


OTHER_ACCOUNT_IMPORT = ("This export belongs to another Telegram account than this archive. "
                        "Import it into a separate data folder (DOTIPHANT_HOME).")


class OtherAccount(RuntimeError):
    """The Telegram account at hand is not the one this archive belongs to."""

    def __init__(self, message: str = OTHER_ACCOUNT):
        super().__init__(message)


def other_account(account_id: int | None, conn: sqlite3.Connection | None = None) -> bool:
    """True when the archive was synced by a different Telegram account than `account_id`.

    Private chat ids and basic group message ids are per account, so two accounts must
    never share one archive. An archive without an owner (import only) belongs to anyone.
    Without `conn`, the archive is opened read-only for the check.
    """
    if account_id is None:
        return False
    if conn is None:
        conn = connect(readonly=True)
        try:
            return other_account(account_id, conn)
        finally:
            conn.close()
    stored = get_meta(conn, "owner_id")
    return stored is not None and stored != str(account_id)
