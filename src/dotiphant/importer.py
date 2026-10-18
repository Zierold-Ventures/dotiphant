"""Import a Telegram Desktop export (machine-readable JSON) into the archive.

Telegram Desktop writes `result.json` from "Export chat history" (one chat) or
"Export Telegram data" (the whole account). This module maps that file onto the same
rows a live sync stores, so imported and synced messages look the same everywhere.

Rules:
- Rows are keyed by (chat_id, message_id), the same ids a live sync uses, and are only
  ever added (INSERT OR IGNORE). A message already in the archive, synced or imported, is
  kept as it is, except that messages sent by the known owner are marked as outgoing, so
  importing again with --me fixes an earlier import without it. Nothing is deleted.
- `sync_state` is not touched. A later live sync of an imported chat behaves like a first
  sync: it reads the latest page and backfills from there, skipping stored rows' inserts.
- Each chat is written in its own transaction.
- The whole file is read with json.load, so memory use is a few times the file size. That
  is fine for exports up to a few hundred MB; for larger accounts export chats one by one.

Telegram Desktop gives chat ids as bare ids. They are turned into marked ids by chat type:
users positive, basic groups -id, supergroups and channels -100<id>.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from . import config, db, status

MAX_RAW_JSON = 64_000
CHANNEL_SHIFT = 1_000_000_000_000

CHAT_KINDS = {
    "personal_chat": "private",
    "saved_messages": "private",
    "replies": "private",
    "verification_codes": "private",
    "bot_chat": "bot",
    "private_group": "group",
    "private_supergroup": "supergroup",
    "public_supergroup": "supergroup",
    "private_channel": "channel",
    "public_channel": "channel",
}
FALLBACK_TITLES = {"saved_messages": "Saved Messages", "replies": "Replies",
                   "verification_codes": "Verification Codes"}
DOC_TYPES = {
    "sticker": "sticker", "voice_message": "voice", "video_message": "video",
    "animation": "video", "video_file": "video", "audio_file": "audio",
}
OTHER_MEDIA_KEYS = ("game_title", "invoice_information", "giveaway_information", "giveaway_results",
                    "paid_media", "todo_list", "story")


class ExportFormatError(ValueError):
    """The path is not a Telegram Desktop JSON export."""


# --- reading the export ------------------------------------------------------------

def locate(path: Path | str) -> Path:
    """result.json itself, or the export folder that holds it."""
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "result.json"
    if not p.is_file():
        raise ExportFormatError(f"No result.json at {p}. Export from Telegram Desktop with format 'Machine-readable JSON'.")
    return p


def load(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportFormatError(f"{path} is not valid JSON ({exc}). Is it a Telegram Desktop JSON export?") from None
    except RecursionError:
        raise ExportFormatError(f"{path} is nested too deeply to be a Telegram Desktop export.") from None
    if not isinstance(data, dict):
        raise ExportFormatError(f"{path} is not a Telegram Desktop export.")
    return data


def iter_chats(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every chat in the export: a single-chat export is itself one chat."""
    if "messages" in data and "type" in data:
        yield data
        return
    found = False
    for section in ("chats", "left_chats"):
        block = data.get(section)
        for chat in (block or {}).get("list", []) if isinstance(block, dict) else []:
            found = True
            yield chat
    if not found and "chats" not in data:
        raise ExportFormatError("This export has no chats. Export chat history or account data with messages, as JSON.")


def owner_from(data: dict[str, Any]) -> tuple[int | None, str | None]:
    info = data.get("personal_information") or {}
    uid = info.get("user_id")
    if uid is None:
        return None, None
    name = " ".join(p for p in (info.get("first_name"), info.get("last_name")) if p) or None
    return int(uid), name


# --- mapping helpers -------------------------------------------------------------------

def marked_chat_id(chat_type: str, bare_id: int) -> int:
    kind = CHAT_KINDS.get(chat_type)
    if kind == "group":
        return -abs(bare_id)
    if kind in ("supergroup", "channel"):
        return -(CHANNEL_SHIFT + abs(bare_id))
    return int(bare_id)


def peer_id(ref: Any) -> int | None:
    """'user123' -> 123, 'chat123' -> -123, 'channel123' -> -100123."""
    if not isinstance(ref, str):
        return None
    for prefix, convert in (("user", lambda n: n), ("chat", lambda n: -n),
                            ("channel", lambda n: -(CHANNEL_SHIFT + n))):
        if ref.startswith(prefix) and ref[len(prefix):].isdigit():
            return convert(int(ref[len(prefix):]))
    return None


INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


def int64(value: Any) -> int | None:
    """value when it is a real integer (not a bool) that sqlite can store, else None."""
    if isinstance(value, int) and not isinstance(value, bool) and INT64_MIN <= value <= INT64_MAX:
        return value
    return None


def clean_str(value: Any) -> Any:
    """Replace lone UTF-16 surrogates (legal in JSON, refused by sqlite) with U+FFFD."""
    if not isinstance(value, str):
        return value
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        return value.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def plain_text(value: Any) -> str:
    """The export's text is a string or a list of strings and {type, text} entities."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(plain_text(part) for part in value)
    if isinstance(value, dict):
        return plain_text(value.get("text"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _utc(unixtime: Any, local: Any) -> str | None:
    if unixtime not in (None, ""):
        try:
            return dt.datetime.fromtimestamp(int(unixtime), dt.timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    if isinstance(local, str) and local:
        try:
            parsed = dt.datetime.fromisoformat(local)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            try:
                parsed = parsed.astimezone()  # the export writes the exporting computer's local time
            except (ValueError, OverflowError, OSError):
                parsed = parsed.replace(tzinfo=dt.timezone.utc)  # the calendar's edge: no local offset to apply
        try:
            return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
        except (ValueError, OverflowError):
            return parsed.replace(tzinfo=dt.timezone.utc, microsecond=0).isoformat()
    return None


def _usable_path(value: Any) -> str | None:
    """A relative file path, not one of the '(File not included...)' notes."""
    if isinstance(value, str) and value and not value.startswith("("):
        return value
    return None


def media_info(m: dict[str, Any]) -> tuple[str | None, str | None, int | None, str | None]:
    """(media_type, media_name, media_size, relative file path) in sync.py's vocabulary."""
    if "photo" in m:
        return "photo", None, m.get("photo_file_size"), _usable_path(m.get("photo"))
    if "file" in m or "media_type" in m or "mime_type" in m:
        kind = DOC_TYPES.get(m.get("media_type") or "", "document")
        name = m.get("file_name") or None
        if not name and kind == "audio" and (m.get("performer") or m.get("title")):
            name = " - ".join(p for p in (m.get("performer"), m.get("title")) if p)
        return kind, name, m.get("file_size"), _usable_path(m.get("file"))
    if isinstance(m.get("poll"), dict):
        return "poll", plain_text(m["poll"].get("question")) or None, None, None
    if "location_information" in m or "place_name" in m:
        return "location", m.get("place_name") or None, None, None
    if isinstance(m.get("contact_information"), dict):
        c = m["contact_information"]
        name = " ".join(p for p in (c.get("first_name"), c.get("last_name")) if p)
        return "contact", name or None, None, None
    if any(k in m for k in OTHER_MEDIA_KEYS):
        return "other", m.get("game_title"), None, None
    return None, None, None, None


def service_text(m: dict[str, Any]) -> str:
    """Same wording as sync.py's service_text for the actions both know."""
    who = m.get("actor") or "Someone"
    action = m.get("action") or ""
    title = m.get("title") or ""
    members = [x or "someone" for x in (m.get("members") or [])]
    if "information_text" in m and not action:
        return str(m["information_text"])
    if action == "create_group":
        return f"{who} created the group \"{title}\""
    if action == "create_channel":
        return f"{who} created \"{title}\""
    if action == "invite_members":
        if members == [m.get("actor")]:
            return f"{who} joined the group"
        return f"{who} added {', '.join(members)}"
    if action == "remove_members":
        target = members[0] if members else "someone"
        return f"{who} left the group" if target == m.get("actor") else f"{who} removed {target}"
    if action == "join_group_by_link":
        return f"{who} joined the group via invite link"
    if action == "join_group_by_request":
        return f"{who} joined the group (request approved)"
    if action == "edit_group_title":
        return f"{who} changed the group name to \"{title}\""
    if action == "edit_group_photo":
        return f"{who} changed the group photo"
    if action == "delete_group_photo":
        return f"{who} removed the group photo"
    if action == "pin_message":
        return f"{who} pinned a message"
    if action == "migrate_to_supergroup":
        return "The group was upgraded to a supergroup"
    if action == "migrate_from_group":
        return f"Upgraded from the basic group \"{title}\""
    if action == "topic_created":
        return f"{who} created the topic \"{title}\""
    if action == "topic_edit":
        return f"{who} renamed the topic to \"{m['new_title']}\"" if m.get("new_title") else f"{who} edited the topic"
    if action == "clear_history":
        return "History was cleared"
    if action in ("phone_call", "conference_call"):
        return f"{who} made a call"
    if action == "group_call":
        return f"{who} {'ended' if m.get('duration') else 'started'} a group call"
    if action == "take_screenshot":
        return f"{who} took a screenshot"
    if action == "joined_telegram":
        return f"{who} joined Telegram"
    if action == "set_messages_ttl":
        return f"{who} {'set' if m.get('period') else 'turned off'} auto-delete"
    return f"{who}: {action.replace('_', ' ') or 'service message'}"


def _raw_json(m: dict[str, Any]) -> str | None:
    clean = dict(m)
    if isinstance(clean.get("contact_information"), dict):
        clean["contact_information"] = {k: v for k, v in clean["contact_information"].items() if k != "phone_number"}
    clean.pop("contact_vcard", None)
    clean["source"] = "telegram_desktop_export"
    text = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= MAX_RAW_JSON else None


# --- the import ------------------------------------------------------------------------

class _Importer:
    def __init__(self, conn: sqlite3.Connection, export_dir: Path, owner_id: int | None,
                 owner_name: str | None, copy_media: bool, skip: frozenset[int] = frozenset()):
        self.conn = conn
        self.skip = skip  # excluded chats (see FORGOTTEN), left out of the import
        self.export_dir = export_dir.resolve()
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.copy_media = copy_media
        self.media_root = config.media_dir() if copy_media else None

    def add_sender(self, pid: int | None, name: str | None, is_bot: bool = False) -> None:
        """Add a sender the archive does not know yet; names from a live sync win."""
        if pid is None:
            return
        if name is not None and not isinstance(name, str):
            name = str(name)
        if pid == self.owner_id:
            name = self.owner_name or name or "Me"
        self.conn.execute("INSERT OR IGNORE INTO senders(peer_id, name, is_bot) VALUES(?,?,?)",
                          (pid, clean_str(name or "Deleted Account"), int(is_bot)))

    def upsert_chat(self, chat_id: int, kind: str, title: str, is_forum: bool) -> None:
        """Create the chat if new. An existing chat keeps its kind and title from the live sync."""
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        self.conn.execute(
            """INSERT INTO chats(chat_id, kind, title, is_forum, first_seen_at, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET is_forum = max(chats.is_forum, excluded.is_forum)""",
            (chat_id, kind, clean_str(title), int(is_forum), now, now),
        )

    def copy_file(self, chat_id: int, message_id: int, rel: str) -> str | None:
        try:
            src = (self.export_dir / rel).resolve()
            if not src.is_relative_to(self.export_dir) or not src.is_file():
                return None
        except (OSError, ValueError):  # a NUL byte or an over-long name in the path
            return None
        folder = self.media_root / str(chat_id)
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"{message_id}-{db.safe_filename(src.name)}"
        if not final.exists():
            part = final.with_name(final.name + ".part")
            shutil.copyfile(src, part)
            part.chmod(0o600)
            part.replace(final)
        return str(final.relative_to(self.media_root))

    def import_chat(self, chat: Any) -> dict[str, Any] | None:
        if not isinstance(chat, dict):
            return None
        ctype = chat.get("type") or ""
        ctype = ctype if isinstance(ctype, str) else str(ctype)
        kind = CHAT_KINDS.get(ctype)
        raw_messages = chat.get("messages")
        messages = [m for m in raw_messages if isinstance(m, dict)] if isinstance(raw_messages, list) else []
        name = chat.get("name")
        name = clean_str(str(name)) if name not in (None, "") else None
        if kind is None or chat.get("id") is None:
            return {"chat_id": None, "title": name or ctype or "unknown", "kind": ctype,
                    "skipped": f"unsupported chat type '{ctype}'"}
        if not messages:
            return None
        raw_id = chat["id"]
        if isinstance(raw_id, str) and re.fullmatch(r"-?[0-9]+", raw_id.strip()):
            raw_id = int(raw_id)
        bare = int64(raw_id)
        chat_id = marked_chat_id(ctype, bare) if bare is not None else None
        if chat_id is None or int64(chat_id) is None:
            return {"chat_id": None, "title": name or ctype, "kind": ctype,
                    "skipped": f"the chat id '{clean_str(str(raw_id))[:40]}' is not a valid Telegram id"}
        if chat_id in self.skip:
            return {"chat_id": chat_id, "title": name or ctype, "kind": kind, "skipped": FORGOTTEN}
        if ctype == "saved_messages" and self.owner_id is None:
            self.owner_id = chat_id
        title = name or FALLBACK_TITLES.get(ctype) or f"Chat {bare}"
        report = {"chat_id": chat_id, "title": title, "kind": kind, "imported": 0, "already_present": 0,
                  "media_copied": 0, "media_missing": 0, "skipped_pre_upgrade": 0, "skipped_invalid": 0}

        topics = {m["id"]: clean_str(plain_text(m.get("title")) or "Topic") for m in messages
                  if m.get("action") == "topic_created" and int64(m.get("id")) is not None}
        forum = bool(topics)
        topic_of: dict[int, int] = {}
        with self.conn:
            self.upsert_chat(chat_id, kind, title, forum)
            if kind in ("private", "bot") and ctype != "saved_messages":
                self.add_sender(chat_id, chat.get("name"), is_bot=kind == "bot")
            if forum:
                self.conn.execute("INSERT OR IGNORE INTO topics(chat_id, topic_id, title) VALUES(?,1,?)",
                                  (chat_id, "General"))
                for tid, ttitle in topics.items():
                    self.conn.execute("INSERT OR IGNORE INTO topics(chat_id, topic_id, title) VALUES(?,?,?)",
                                      (chat_id, tid, ttitle))
            for m in messages:
                mid = m.get("id")
                if isinstance(mid, int) and not isinstance(mid, bool) and int64(mid) is None:
                    report["skipped_invalid"] += 1
                    continue
                if int64(mid) is None:
                    continue
                if mid <= 0:
                    # A supergroup export includes the basic group it was upgraded from, with ids
                    # shifted below zero. The old group's id is not in the export, so these are
                    # left out rather than stored under ids a live sync would duplicate.
                    report["skipped_pre_upgrade"] += 1
                    continue
                try:
                    row, media_rel = self.message_row(chat_id, kind, m, topics, topic_of, forum)
                    cols = ", ".join(row)
                    cur = self.conn.execute(f"INSERT OR IGNORE INTO messages({cols}) VALUES({', '.join('?' * len(row))})",
                                            [clean_str(v) for v in row.values()])
                    report["imported" if cur.rowcount else "already_present"] += 1
                    if self.copy_media and row["media_type"] in db.DOWNLOADABLE:
                        self.attach_media(chat_id, mid, media_rel, report)
                except (ValueError, TypeError, AttributeError, KeyError, OverflowError, OSError,
                        sqlite3.InterfaceError, sqlite3.ProgrammingError, sqlite3.IntegrityError):
                    # One malformed message must not abort the import of everything else.
                    report["skipped_invalid"] += 1
            if self.owner_id is not None:
                # An earlier import without --me left the owner's messages unmarked.
                self.conn.execute("UPDATE messages SET out=1 WHERE chat_id=? AND sender_id=? AND out=0",
                                  (chat_id, self.owner_id))
            newest =self.conn.execute("SELECT max(date) FROM messages WHERE chat_id=?", (chat_id,)).fetchone()[0]
            if newest:
                self.conn.execute(
                    "UPDATE chats SET last_message_at=? WHERE chat_id=? AND (last_message_at IS NULL OR last_message_at < ?)",
                    (newest, chat_id, newest))
        return report

    def attach_media(self, chat_id: int, mid: int, rel: str | None, report: dict) -> None:
        current = self.conn.execute("SELECT media_path FROM messages WHERE chat_id=? AND message_id=?",
                                    (chat_id, mid)).fetchone()
        if current is None or (current[0] and (self.media_root / current[0]).exists()):
            return
        stored = self.copy_file(chat_id, mid, rel) if rel else None
        if stored is None:
            report["media_missing"] += 1
            return
        self.conn.execute("UPDATE messages SET media_path=? WHERE chat_id=? AND message_id=?", (stored, chat_id, mid))
        report["media_copied"] += 1

    def message_row(self, chat_id: int, kind: str, m: dict[str, Any], topics: dict[int, str],
                    topic_of: dict[int, int], forum: bool) -> tuple[dict[str, Any], str | None]:
        mid = m["id"]
        is_service = m.get("type") == "service"
        from_ref = m.get("actor_id") if is_service else m.get("from_id")
        from_name = m.get("actor") if is_service else m.get("from")
        sender_id = peer_id(from_ref)
        if sender_id is None:
            sender_id = chat_id if kind in ("channel", "private", "bot") else None
        else:
            self.add_sender(sender_id, from_name)
        out = int(self.owner_id is not None and sender_id == self.owner_id)

        reply_to = int64(m.get("reply_to_message_id"))
        topic_id = None
        if mid in topics:
            topic_id = mid
        elif forum:
            if reply_to in topics:
                topic_id, reply_to = reply_to, None  # a plain post in a topic points at the topic root
            else:
                topic_id = topic_of.get(reply_to, 1) if reply_to is not None else 1
            topic_of[mid] = topic_id

        if is_service:
            kind_, text, service = "service", "", service_text(m)
            media_type = media_name = media_size = media_rel = None
        else:
            text, service = plain_text(m.get("text")), None
            media_type, media_name, media_size, media_rel = media_info(m)
            kind_ = "media" if media_type else "text"
        forwarded = m.get("forwarded_from") or ("Unknown" if "forwarded_from" in m else None)
        row = {
            "chat_id": chat_id, "message_id": mid, "topic_id": topic_id, "sender_id": sender_id, "out": out,
            "date": _utc(m.get("date_unixtime"), m.get("date")) or "1970-01-01T00:00:00+00:00",
            "edit_date": _utc(m.get("edited_unixtime"), m.get("edited")) if ("edited" in m or "edited_unixtime" in m) else None,
            "reply_to_id": reply_to, "forwarded_from": forwarded, "kind": kind_, "text": text,
            "media_type": media_type, "media_name": media_name,
            "media_size": int64(media_size),
            "service_text": service, "raw_json": _raw_json(m),
        }
        return row, media_rel


FORGOTTEN = ("excluded from the archive, for example by `dotiphant forget`. "
             "Import it anyway with --include-forgotten")
BUSY = "A sync, download or forget is running. Try the import again when it finishes."


def import_export(path: Path | str, *, copy_media: bool = False, me: int | None = None,
                  db_path: Path | None = None, include_forgotten: bool = False) -> dict[str, Any]:
    """Import result.json (or its folder). Returns {"owner_id", "chats": [per-chat counts]}.

    Holds the sync lock (raises status.LockBusy when another process has it), so a forget
    cannot run halfway through. Chats in excluded_chats are skipped unless include_forgotten.
    """
    file = locate(path)
    data = load(file)
    chats = list(iter_chats(data))
    owner_id, owner_name = owner_from(data)
    with status.holding_sync_lock(BUSY):
        skip = frozenset() if include_forgotten else frozenset(config.Settings.load().excluded_chats)
        return _import(file, chats, owner_id, owner_name, me, copy_media, db_path, skip)


def _import(file: Path, chats: list, owner_id: int | None, owner_name: str | None, me: int | None,
            copy_media: bool, db_path: Path | None, skip: frozenset[int]) -> dict[str, Any]:
    conn = db.open_rw(db_path)
    try:
        # Private chat ids and message ids are per account: never mix two accounts in one archive.
        # The owner the export declares, --me and the stored owner must agree where present.
        stored = db.get_meta(conn, "owner_id")
        known = {int(v) for v in (owner_id, me, stored) if v is not None}
        if len(known) > 1:
            raise db.OtherAccount(db.OTHER_ACCOUNT_IMPORT)
        owner_id = next(iter(known), None)
        imp = _Importer(conn, file.parent, owner_id, owner_name, copy_media, skip)
        if owner_id is not None and owner_name:
            with conn:
                imp.add_sender(owner_id, owner_name)
        reports = []
        for chat in chats:
            report = imp.import_chat(chat)
            if report is not None:
                reports.append(report)
        if stored is None and imp.owner_id is not None:
            # The export named its account, --me did, or the export holds its Saved Messages:
            # the archive is now that account's.
            with conn:
                db.set_meta(conn, "owner_id", str(imp.owner_id))
        return {"owner_id": imp.owner_id, "chats": reports}
    finally:
        conn.close()
