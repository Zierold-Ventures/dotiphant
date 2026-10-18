"""Fetch Telegram chats into the local archive.

A run has three steps:

1. Inventory: walk the dialog list (archived folder included), keep the chats the user's
   settings allow and upsert them into `chats`.
2. Recent messages, most recently active chat first. The first sync of a chat (no
   `sync_state` row, or `newest_id` still 0) stores its latest page so recent context
   shows up quickly. Later syncs read the latest page when the dialog's top message
   changed. When that page does not reach the stored messages, the unread range between
   them is recorded as a gap (meta key `gap:<chat_id>`, a list of ranges). Every chat
   gets its latest page before any chat spends more; then gaps are filled round-robin,
   a few pages per chat per pass, newest gap first. No message is ever skipped: every id
   between `oldest_id` and `newest_id` is stored, not served by Telegram, or inside a
   recorded gap.
3. History backfill below `sync_state.oldest_id`, round-robin: a few pages per chat per
   pass, most recently active chat first, until Telegram has no older messages or the
   `history_days` cutoff is reached.

Between steps 2 and 3, each chat's profile photo is kept up to date (small version, in
media/avatars/). The dialog list already carries each photo's id, so only a new or missing
photo costs a request; this runs after the new messages, so it never delays them, and
before the backfill, which can take hours on a first sync.

The `history_days` cutoff is reached when the lowest-id message on a page is older than
the cutoff (dates are not strictly monotonic, so one old message mid-page does not end
anything). Inside a gap, the part older than the cutoff is kept as a deferred gap
(marked "old"): it is not fetched while the cutoff applies, and becomes a normal gap
again when `history_days` is widened.

Paging uses only offset_id, never min_id or max_id: Telegram can answer those with an
empty page. A short page does not mean the end of history either (Telegram drops
messages it may not show); the end is an empty page, message id 1, or a complete
(non-slice) answer.

Chats that Telegram refuses (left, banned, private, deleted) are marked in
`sync_state.error` with the prefix "inaccessible:" and retried at most once a day,
or whenever they are requested by id. They do not count as a failed run.

Every page is committed together with its cursor, so a run that stops for any reason
(time limit, long flood wait, crash, Ctrl-C) resumes exactly where it stopped.
Only read requests are sent to Telegram. Logs and status.json never contain message text.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from telethon import errors, functions, types, utils

from . import config, db
from .importer_whatsapp import is_whatsapp_id
from .status import LockBusy, try_lock, unlock

log = logging.getLogger(__name__)

PAGE_SIZE = 100
PAGE_DELAY = 0.3  # seconds between new-message requests, to stay friendly with Telegram
BACKFILL_DELAY = 1.0  # seconds between history backfill requests
AVATAR_DELAY = 0.3  # seconds between profile photo downloads
AVATARS = "avatars"  # folder inside the media dir
BACKFILL_PAGES_PER_TURN = 5  # pages per chat before the backfill moves on to the next chat
GAP_PAGES_PER_TURN = 3  # gap pages per chat before the gap filling moves on to the next chat
RECHECK_INACCESSIBLE = 24 * 3600  # seconds before an inaccessible chat is tried again
RECHECK_PREDECESSOR = 30 * 24 * 3600  # the same for an upgraded basic group (often never joined)
INACCESSIBLE = "inaccessible"
MAX_FLOOD_WAIT = 600  # longer waits stop the run; the next run resumes
FAILED_CHATS_SHOWN = 50  # failed chats listed in status.json; `errors` keeps the full count
MAX_TOPIC_PAGES = 20
MAX_RAW_JSON = 64_000
LARGE_FILE = 50 * 1024 * 1024
DEFAULT_NAMES = {
    "photo": "photo.jpg", "video": "video.mp4", "voice": "voice.ogg",
    "audio": "audio.mp3", "sticker": "sticker.webp", "document": "file",
}
_STRIP_KEYS = {"phone", "phone_number", "vcard", "access_hash", "file_reference"}
# Errors that mean the account cannot read this chat at all (left, banned, private, deleted).
_PERMANENT = (
    errors.ChannelPrivateError, errors.ChannelInvalidError, errors.ChatIdInvalidError,
    errors.PeerIdInvalidError, errors.ChatForbiddenError, errors.ChannelPublicGroupNaError,
    errors.UserBannedInChannelError,
)


class SyncBusy(LockBusy):
    """Another sync, download, import or forget holds the lock."""


class NotAuthorized(RuntimeError):
    pass


class OtherAccount(NotAuthorized, db.OtherAccount):
    """The logged-in Telegram account is not the one this archive was synced with."""


class _Stop(Exception):
    """Stop the run cleanly; everything committed so far stays and the next run resumes."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# --- small helpers -----------------------------------------------------------------

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _iso(value: Any) -> str | None:
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: Any) -> Any:
    """to_dict() output made JSON-safe, without phone numbers or access hashes.

    Keys whose value is None are left out: they are most of a message's to_dict() and
    say nothing, so dropping them makes raw_json about four times smaller for text.
    """
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in _STRIP_KEYS and v is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _raw_json(obj: Any) -> str | None:
    try:
        text = json.dumps(_clean(obj.to_dict()), ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return None
    return text if len(text) <= MAX_RAW_JSON else None


def _person_name(user: Any) -> str:
    if getattr(user, "deleted", False):
        return "Deleted Account"
    name = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p)
    if name:
        return name
    if getattr(user, "username", None):
        return "@" + user.username
    return f"User {getattr(user, 'id', '')}".strip()


def _entity_name(entity: Any) -> str:
    title = getattr(entity, "title", None)
    if title:
        return title
    if isinstance(entity, (types.User, types.UserEmpty)):
        return _person_name(entity)
    return "Untitled chat"


def _chat_kind(entity: Any) -> str | None:
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "private"
    if isinstance(entity, (types.Chat, types.ChatForbidden, types.ChatEmpty)):
        return "group"
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
            return "supergroup"
        return "channel"
    return None


def _photo_id(entity: Any) -> int | None:
    """Telegram's id of a chat's or person's current profile photo; None when there is none."""
    photo = getattr(entity, "photo", None)
    if isinstance(photo, (types.ChatPhoto, types.UserProfilePhoto)) and photo.photo_id:
        return photo.photo_id
    return None


def avatars_dir() -> Path:
    path = config.media_dir() / AVATARS
    path.mkdir(exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def remove_avatar_file(rel: str | None) -> None:
    """Delete a stored profile photo. Only files inside media/avatars/ are ever touched."""
    if not rel:
        return
    media = config.media_dir()
    root = (media / AVATARS).resolve()
    try:
        path = (media / rel).resolve()
        if path.parent == root and path.is_file():
            path.unlink()
    except (OSError, ValueError):
        pass


def _humanize(name: str) -> str:
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", name.removeprefix("MessageAction")).lower()
    return words or "service message"


# --- media mapping -----------------------------------------------------------------

def _photo_size(photo: Any) -> int | None:
    best = None
    for s in getattr(photo, "sizes", None) or []:
        if isinstance(s, types.PhotoSizeProgressive) and s.sizes:
            best = max(best or 0, max(s.sizes))
        elif isinstance(s, types.PhotoSize):
            best = max(best or 0, s.size)
    return best


def media_info(media: Any) -> tuple[str | None, str | None, int | None]:
    """(media_type, media_name, media_size) for a message's media."""
    if media is None or isinstance(media, types.MessageMediaEmpty):
        return None, None, None
    if isinstance(media, types.MessageMediaPhoto):
        return "photo", None, _photo_size(media.photo)
    if isinstance(media, types.MessageMediaDocument):
        doc = media.document
        if not isinstance(doc, types.Document):
            return "document", None, None
        kind, name = "document", None
        for attr in doc.attributes:
            if isinstance(attr, types.DocumentAttributeFilename):
                name = attr.file_name
            elif isinstance(attr, types.DocumentAttributeSticker):
                kind = "sticker"
            elif isinstance(attr, types.DocumentAttributeAudio) and kind == "document":
                kind = "voice" if attr.voice else "audio"
                if not name and not attr.voice and (attr.title or attr.performer):
                    name = " - ".join(p for p in (attr.performer, attr.title) if p)
            elif isinstance(attr, (types.DocumentAttributeVideo, types.DocumentAttributeAnimated)) and kind == "document":
                kind = "video"
        if getattr(media, "voice", False):
            kind = "voice"
        return kind, name, doc.size
    if isinstance(media, types.MessageMediaPoll):
        q = media.poll.question
        return "poll", getattr(q, "text", q) if q else None, None
    if isinstance(media, types.MessageMediaVenue):
        return "location", media.title or None, None
    if isinstance(media, (types.MessageMediaGeo, types.MessageMediaGeoLive)):
        return "location", None, None
    if isinstance(media, types.MessageMediaContact):
        name = " ".join(p for p in (media.first_name, media.last_name) if p)
        return "contact", name or None, None
    if isinstance(media, types.MessageMediaWebPage):
        page = media.webpage
        return "webpage", getattr(page, "title", None) or getattr(page, "url", None), None
    return "other", None, None


# --- the run -----------------------------------------------------------------------

class _Lock:
    def __init__(self):
        self.file = None

    def acquire(self) -> None:
        self.file = open(config.lock_path(), "a+")
        if not try_lock(self.file):
            self.file.close()
            self.file = None
            raise SyncBusy("A sync is already running.")

    def release(self) -> None:
        if self.file:
            try:
                unlock(self.file)
            finally:
                self.file.close()
                self.file = None


class _Runner:
    def __init__(self, client, conn: sqlite3.Connection, settings: config.Settings,
                 progress: Callable[[dict], None] | None, max_seconds: float | None,
                 write_status: bool = True):
        self.client = client
        self.write_status = write_status
        self.explicit: set[int] = set()  # chat ids the caller asked for by id
        self.predecessors: dict[int, dict] = {}
        self.failed: set[int] = set()  # chats that raised an error in this run
        self.inaccessible: list[int] = []
        self.forbidden: set[int] = set()  # ChatForbidden / ChannelForbidden dialogs
        self.topics_stale: set[int] = set()  # forums with a message in a topic not in `topics`
        self.topics_done: set[int] = set()  # forums whose topic list was read in this run
        self.known_topics: dict[int, set[int]] = {}
        self.conn = conn
        self.settings = settings
        self.progress = progress
        self.deadline = time.monotonic() + max_seconds if max_seconds else None
        self.last_request = 0.0
        self.owner_id: int | None = None
        self.owner_name = "Me"
        self.new_messages = 0
        self.edited = 0
        self.errors: list[dict] = []
        self.avatar_counts = {"downloaded": 0, "removed": 0, "failed": 0}
        self.status = {
            "state": "running", "stage": "starting", "chat": None, "chats_done": 0, "chats_total": 0,
            "new_messages": 0, "started_at": _now(), "finished_at": None, "message": None, "pid": os.getpid(),
            "errors": 0, "failed_chats": [],
        }
        cutoff_days = settings.history_days
        self.cutoff = (
            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cutoff_days)).replace(microsecond=0).isoformat()
            if cutoff_days else None
        )

    # status ------------------------------------------------------------------------

    def set_status(self, **changes) -> None:
        self.status.update(changes)
        self.status["new_messages"] = self.new_messages
        if self.write_status:
            try:
                config.write_private(config.status_path(), json.dumps(self.status, indent=1))
            except OSError as exc:
                log.warning("Could not write status.json: %s", type(exc).__name__)
        if self.progress:
            try:
                self.progress(dict(self.status))
            except Exception:
                log.debug("progress callback failed", exc_info=True)

    # requests ----------------------------------------------------------------------

    def check_time(self) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise _Stop("time_limit", "Stopped at the time limit. The next sync continues where this one stopped.")

    async def flood_wait(self, seconds: int) -> None:
        if seconds > MAX_FLOOD_WAIT:
            raise _Stop("flood_wait", f"Telegram asked to wait {seconds} seconds. Stopped for now; "
                                      "the next sync continues where this one stopped.")
        if self.deadline is not None and time.monotonic() + seconds >= self.deadline:
            raise _Stop("time_limit", "Stopped at the time limit (Telegram asked to wait). "
                                      "The next sync continues where this one stopped.")
        previous = self.status["state"]
        self.set_status(state="waiting", message=f"Telegram asked to wait {seconds} seconds.")
        await asyncio.sleep(seconds + 1)
        self.set_status(state=previous if previous != "waiting" else "running", message=None)

    async def call(self, request, delay: float | None = None):
        spacing = PAGE_DELAY if delay is None else delay
        while True:
            self.check_time()
            delay = spacing - (time.monotonic() - self.last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_request = time.monotonic()
            try:
                return await self.client(request)
            except errors.FloodWaitError as exc:
                await self.flood_wait(exc.seconds)

    # senders -----------------------------------------------------------------------

    def upsert_sender(self, peer_id: int, name: str, username: str | None = None, is_bot: bool = False) -> None:
        self.conn.execute(
            """INSERT INTO senders(peer_id, name, username, is_bot) VALUES(?,?,?,?)
               ON CONFLICT(peer_id) DO UPDATE SET
                 name = excluded.name,
                 username = coalesce(excluded.username, senders.username),
                 is_bot = excluded.is_bot""",
            (peer_id, name or "Unknown", username, int(bool(is_bot))),
        )

    def save_entities(self, response) -> dict[int, str]:
        """Upsert the users and chats a response carries; return a peer id -> name map."""
        names: dict[int, str] = {}
        for ent in list(getattr(response, "users", None) or []) + list(getattr(response, "chats", None) or []):
            try:
                pid = utils.get_peer_id(ent)
            except Exception:
                continue
            if isinstance(ent, types.UserEmpty):
                continue
            name = _entity_name(ent)
            if getattr(ent, "is_self", False) or pid == self.owner_id:
                name = self.owner_name
            names[pid] = name
            self.upsert_sender(pid, name, getattr(ent, "username", None), bool(getattr(ent, "bot", False)))
        return names

    def name_of(self, peer_id: int | None, names: dict[int, str]) -> str | None:
        if peer_id is None:
            return None
        if peer_id in names:
            return names[peer_id]
        row = self.conn.execute("SELECT name FROM senders WHERE peer_id=?", (peer_id,)).fetchone()
        return row[0] if row else None

    # inventory ---------------------------------------------------------------------

    def wanted(self, kind: str, archived: bool, chat_id: int) -> bool:
        s = self.settings
        if chat_id in set(s.excluded_chats or []):
            return False
        if archived and not s.include_archived:
            return False
        return {
            "private": s.include_private, "bot": s.include_bots, "group": s.include_groups,
            "supergroup": s.include_groups, "channel": s.include_channels,
        }.get(kind, False)

    def upsert_chat(self, chat_id: int, kind: str, title: str, *, username=None, is_forum=False,
                    archived=False, member_count=None, migrated_to=None, last_message_at=None,
                    unread_count=None, raw_json=None) -> None:
        now = _now()
        self.conn.execute(
            """INSERT INTO chats(chat_id, kind, title, username, is_forum, archived, member_count, migrated_to,
                                 last_message_at, unread_count, first_seen_at, updated_at, raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chat_id) DO UPDATE SET
                 kind=excluded.kind, title=excluded.title, username=excluded.username,
                 is_forum=excluded.is_forum, archived=excluded.archived,
                 member_count=coalesce(excluded.member_count, chats.member_count),
                 migrated_to=coalesce(excluded.migrated_to, chats.migrated_to),
                 last_message_at=CASE WHEN chats.last_message_at IS NULL
                                        OR excluded.last_message_at > chats.last_message_at
                                      THEN excluded.last_message_at ELSE chats.last_message_at END,
                 unread_count=coalesce(excluded.unread_count, chats.unread_count),
                 updated_at=excluded.updated_at,
                 raw_json=coalesce(excluded.raw_json, chats.raw_json)""",
            (chat_id, kind, title, username, int(bool(is_forum)), int(bool(archived)), member_count, migrated_to,
             last_message_at, unread_count, now, now, raw_json),
        )

    async def inventory(self, chat_ids: list[int] | None) -> list[dict]:
        self.set_status(stage="inventory", message=None)
        wanted_ids = set(chat_ids) if chat_ids else None
        self.explicit = set(wanted_ids or ())
        items: dict[int, dict] = {}
        self.forbidden: set[int] = set()
        if wanted_ids:
            # Chats imported from WhatsApp exports have nothing to fetch from Telegram.
            for cid in sorted(c for c in wanted_ids if is_whatsapp_id(c)):
                self.errors.append({"chat_id": cid, "error": "imported from a WhatsApp export; nothing to sync"})
                wanted_ids.discard(cid)
            if not wanted_ids:
                return []

        # Basic groups need no access hash, so explicitly requested ones resolve from the archive.
        if wanted_ids:
            for row in self.conn.execute(
                f"SELECT * FROM chats WHERE kind='group' AND chat_id IN ({','.join('?' * len(wanted_ids))})",
                list(wanted_ids),
            ):
                items[row["chat_id"]] = self.item_from_row(row)

        # Telethon sleeps through flood waits up to flood_sleep_threshold (60 s) by itself.
        # On a longer wait the walk stops and this run works on the chats listed so far;
        # the next run walks the whole list again. Only when nothing was listed yet does
        # it wait (or stop the run) and start over.
        complete = False
        while True:
            try:
                async for dialog in self.client.iter_dialogs(ignore_migrated=False):
                    self.check_time()
                    self.take_dialog(dialog, wanted_ids, items)
                    if wanted_ids and wanted_ids <= set(items) | self.forbidden:
                        break
                complete = True
                break
            except errors.FloodWaitError as exc:
                self.conn.commit()
                if items or self.forbidden:
                    log.info("Chat list stopped by a %s s flood wait; syncing the %d chats listed so far.",
                             exc.seconds, len(items))
                    break
                await self.flood_wait(exc.seconds)
        self.conn.commit()

        if wanted_ids:
            reason = "not found in your Telegram chats" if complete else \
                "not reached: Telegram asked to wait while listing chats; try again later"
            for missing in wanted_ids - set(items) - self.forbidden:
                self.errors.append({"chat_id": missing, "error": reason})
        return sorted(items.values(), key=lambda i: i["date"] or "", reverse=True)

    def take_dialog(self, dialog, wanted_ids: set[int] | None, items: dict[int, dict]) -> None:
        entity = dialog.entity
        kind = _chat_kind(entity)
        if kind is None:
            return
        chat_id = utils.get_peer_id(entity)
        archived = bool(getattr(dialog, "archived", False))
        if wanted_ids is not None:
            if chat_id not in wanted_ids:
                return
        elif not self.wanted(kind, archived, chat_id):
            return
        if isinstance(entity, (types.ChatForbidden, types.ChannelForbidden)):
            # Left, kicked or banned: keep what is stored, but there is nothing to fetch.
            title = getattr(entity, "title", None) or "Untitled chat"
            self.upsert_chat(chat_id, kind, title, archived=archived, raw_json=_raw_json(entity))
            self.mark_inaccessible(chat_id, type(entity).__name__)
            self.forbidden.add(chat_id)
            self.inaccessible.append(chat_id)
            if chat_id in self.explicit:
                self.errors.append({"chat_id": chat_id, "title": title,
                                    "error": "inaccessible: you are no longer a member of this chat"})
            return
        if kind in ("private", "bot"):
            title = "Saved Messages" if getattr(entity, "is_self", False) else _person_name(entity)
            if not getattr(entity, "is_self", False):
                self.upsert_sender(chat_id, _person_name(entity), getattr(entity, "username", None), kind == "bot")
        else:
            title = getattr(entity, "title", None) or "Untitled chat"
            self.upsert_sender(chat_id, title, getattr(entity, "username", None))
        migrated_to = None
        mt = getattr(entity, "migrated_to", None)
        if isinstance(mt, (types.InputChannel, types.InputChannelFromMessage)):
            migrated_to = utils.get_peer_id(types.PeerChannel(mt.channel_id))
        top = getattr(dialog, "message", None)
        date = _iso(getattr(dialog, "date", None))
        self.upsert_chat(
            chat_id, kind, title,
            username=getattr(entity, "username", None),
            is_forum=bool(getattr(entity, "forum", False)),
            archived=archived,
            member_count=getattr(entity, "participants_count", None),
            migrated_to=migrated_to,
            last_message_at=date,
            unread_count=getattr(dialog, "unread_count", None),
            raw_json=_raw_json(entity),
        )
        items[chat_id] = {
            "chat_id": chat_id, "kind": kind, "title": title, "peer": utils.get_input_peer(entity),
            "entity": entity, "forum": bool(getattr(entity, "forum", False)),
            "top_id": getattr(top, "id", None), "date": date, "archived": archived,
        }

    def item_from_row(self, row) -> dict:
        kind = row["kind"]
        peer = types.InputPeerChat(-row["chat_id"]) if kind == "group" else None
        return {"chat_id": row["chat_id"], "kind": kind, "title": row["title"], "peer": peer, "entity": None,
                "forum": bool(row["is_forum"]), "top_id": None, "date": row["last_message_at"],
                "archived": bool(row["archived"])}

    # sync_state --------------------------------------------------------------------

    def state(self, chat_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM sync_state WHERE chat_id=?", (chat_id,)).fetchone()

    def save_state(self, chat_id: int, **fields) -> None:
        self.conn.execute("INSERT OR IGNORE INTO sync_state(chat_id) VALUES(?)", (chat_id,))
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE sync_state SET {sets} WHERE chat_id=?", (*fields.values(), chat_id))

    def mark_inaccessible(self, chat_id: int, reason: str) -> None:
        # A new row keeps newest_id 0, which the next attempt treats as a first sync.
        self.save_state(chat_id, error=f"{INACCESSIBLE}: {reason} (checked {_now()})")

    def recently_inaccessible(self, chat_id: int) -> bool:
        """Marked inaccessible less than RECHECK_INACCESSIBLE ago (explicit requests always retry)."""
        if chat_id in self.explicit:
            return False
        st = self.state(chat_id)
        error = (st["error"] if st else None) or ""
        if not error.startswith(INACCESSIBLE):
            return False
        found = re.search(r"\(checked ([^)]+)\)", error)
        try:
            when = dt.datetime.fromisoformat(found.group(1))
        except (AttributeError, ValueError):
            return False
        row = self.conn.execute("SELECT migrated_to FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        window = RECHECK_PREDECESSOR if row is not None and row["migrated_to"] else RECHECK_INACCESSIBLE
        return (dt.datetime.now(dt.timezone.utc) - when).total_seconds() < window

    # gaps: unfetched ids strictly between low and high ---------------------------------

    def get_gaps(self, chat_id: int) -> list[tuple[int, int, bool]]:
        """(low, high, deferred) ranges, newest first. Stored as "low,high[,old];...".

        A deferred range lies below the history_days cutoff and waits for a wider setting.
        """
        value = db.get_meta(self.conn, f"gap:{chat_id}")
        gaps = []
        for part in (value or "").split(";"):
            bits = part.split(",")
            if len(bits) >= 2:
                gaps.append((int(bits[0]), int(bits[1]), len(bits) > 2 and bits[2] == "old"))
        return sorted(gaps, key=lambda g: -g[1])

    def set_gaps(self, chat_id: int, gaps: list[tuple[int, int, bool]]) -> None:
        if not gaps:
            self.conn.execute("DELETE FROM meta WHERE key=?", (f"gap:{chat_id}",))
            return
        value = ";".join(f"{lo},{hi}" + (",old" if old else "") for lo, hi, old in sorted(gaps, key=lambda g: -g[1]))
        db.set_meta(self.conn, f"gap:{chat_id}", value)

    def active_gaps(self, chat_id: int) -> list[tuple[int, int, bool]]:
        """Gaps to fill now: all of them without a cutoff, else the ones not deferred."""
        return [g for g in self.get_gaps(chat_id) if not g[2] or self.cutoff is None]

    def seen_top(self, chat_id: int) -> int | None:
        value = db.get_meta(self.conn, f"top:{chat_id}")
        return int(value) if value else None

    def remember_top(self, item: dict) -> None:
        """The dialog's top message id when the latest page was read.

        Telegram sometimes lists a top message that getHistory never returns; without
        this, such a chat would cost a request on every run.
        """
        if item["top_id"] is not None:
            db.set_meta(self.conn, f"top:{item['chat_id']}", str(item["top_id"]))

    # messages ----------------------------------------------------------------------

    def topic_of(self, m, forum: bool) -> int | None:
        if isinstance(getattr(m, "action", None), types.MessageActionTopicCreate):
            return m.id
        rh = getattr(m, "reply_to", None)
        if isinstance(rh, types.MessageReplyHeader) and rh.forum_topic:
            return rh.reply_to_top_id or rh.reply_to_msg_id
        return 1 if forum else None

    def service_text(self, action, actor: str | None, names: dict[int, str]) -> str:
        who = actor or "Someone"

        def users(ids):
            return ", ".join(self.name_of(i, names) or "someone" for i in ids or [])

        a = action
        if isinstance(a, types.MessageActionChatCreate):
            return f"{who} created the group \"{a.title}\""
        if isinstance(a, types.MessageActionChannelCreate):
            return f"{who} created \"{a.title}\""
        if isinstance(a, types.MessageActionChatAddUser):
            if a.users and len(a.users) == 1 and self.name_of(a.users[0], names) == actor:
                return f"{who} joined the group"
            return f"{who} added {users(a.users)}"
        if isinstance(a, types.MessageActionChatDeleteUser):
            target = self.name_of(a.user_id, names)
            return f"{who} left the group" if target == actor else f"{who} removed {target or 'someone'}"
        if isinstance(a, types.MessageActionChatJoinedByLink):
            return f"{who} joined the group via invite link"
        if isinstance(a, types.MessageActionChatJoinedByRequest):
            return f"{who} joined the group (request approved)"
        if isinstance(a, types.MessageActionChatEditTitle):
            return f"{who} changed the group name to \"{a.title}\""
        if isinstance(a, types.MessageActionChatEditPhoto):
            return f"{who} changed the group photo"
        if isinstance(a, types.MessageActionChatDeletePhoto):
            return f"{who} removed the group photo"
        if isinstance(a, types.MessageActionPinMessage):
            return f"{who} pinned a message"
        if isinstance(a, types.MessageActionChatMigrateTo):
            return "The group was upgraded to a supergroup"
        if isinstance(a, types.MessageActionChannelMigrateFrom):
            return f"Upgraded from the basic group \"{a.title}\""
        if isinstance(a, types.MessageActionTopicCreate):
            return f"{who} created the topic \"{a.title}\""
        if isinstance(a, types.MessageActionTopicEdit):
            if a.title:
                return f"{who} renamed the topic to \"{a.title}\""
            if a.closed is not None:
                return f"{who} {'closed' if a.closed else 'reopened'} the topic"
            return f"{who} edited the topic"
        if isinstance(a, types.MessageActionHistoryClear):
            return "History was cleared"
        if isinstance(a, types.MessageActionPhoneCall):
            return f"{who} made a {'video ' if a.video else ''}call"
        if isinstance(a, types.MessageActionGroupCall):
            return f"{who} {'ended' if a.duration else 'started'} a group call"
        if isinstance(a, types.MessageActionScreenshotTaken):
            return f"{who} took a screenshot"
        if isinstance(a, types.MessageActionContactSignUp):
            return f"{who} joined Telegram"
        if isinstance(a, types.MessageActionSetMessagesTTL):
            return f"{who} {'set' if a.period else 'turned off'} auto-delete"
        if isinstance(a, types.MessageActionCustomAction):
            return a.message
        return f"{who}: {_humanize(type(a).__name__)}"

    def message_row(self, item: dict, m, names: dict[int, str]) -> dict:
        chat_id = item["chat_id"]
        out = bool(getattr(m, "out", False))
        from_id = getattr(m, "from_id", None)
        if getattr(m, "post", False) and getattr(m, "peer_id", None) is not None:
            sender_id = utils.get_peer_id(m.peer_id)  # a channel post speaks for the channel, even when you sent it
        elif isinstance(from_id, types.PeerChannel):
            sender_id = utils.get_peer_id(from_id)  # sent as a channel (send_as) or as an anonymous admin
        elif out and self.owner_id is not None:
            sender_id = self.owner_id
        elif from_id is not None:
            sender_id = utils.get_peer_id(from_id)
        else:
            sender_id = utils.get_peer_id(m.peer_id) if getattr(m, "peer_id", None) else chat_id
        rh = getattr(m, "reply_to", None)
        reply_to_id = rh.reply_to_msg_id if isinstance(rh, types.MessageReplyHeader) else None
        if isinstance(rh, types.MessageReplyHeader) and rh.forum_topic and not rh.reply_to_top_id:
            reply_to_id = None  # a plain post in a forum topic: reply_to_msg_id is the topic, not a reply
        forwarded = None
        fwd = getattr(m, "fwd_from", None)
        if fwd is not None:
            forwarded = fwd.from_name
            if not forwarded and fwd.from_id is not None:
                forwarded = self.name_of(utils.get_peer_id(fwd.from_id), names)
            forwarded = forwarded or fwd.post_author or "Unknown"
        if isinstance(m, types.MessageService):
            kind, text = "service", ""
            media_type = media_name = media_size = None
            service = self.service_text(m.action, self.name_of(sender_id, names), names)
        else:
            text = m.message or ""
            media_type, media_name, media_size = media_info(getattr(m, "media", None))
            kind = "media" if media_type not in (None, "webpage") else "text"
            service = None
        return {
            "chat_id": chat_id, "message_id": m.id, "topic_id": self.topic_of(m, item["forum"]),
            "sender_id": sender_id, "out": int(out), "date": _iso(m.date) or _now(),
            "edit_date": _iso(getattr(m, "edit_date", None)), "reply_to_id": reply_to_id,
            "forwarded_from": forwarded, "kind": kind, "text": text, "media_type": media_type,
            "media_name": media_name, "media_size": media_size, "service_text": service,
            "raw_json": _raw_json(m),
        }

    def store(self, item: dict, m, names: dict[int, str]) -> None:
        row = self.message_row(item, m, names)
        existing = self.conn.execute(
            "SELECT text, edit_date FROM messages WHERE chat_id=? AND message_id=?",
            (row["chat_id"], row["message_id"]),
        ).fetchone()
        if existing is None:
            cols = ", ".join(row)
            self.conn.execute(f"INSERT INTO messages({cols}) VALUES({', '.join('?' * len(row))})", list(row.values()))
            self.new_messages += 1
        elif existing["text"] != row["text"] and row["kind"] != "service":
            self.conn.execute(
                "INSERT INTO message_versions(chat_id, message_id, text, replaced_at) VALUES(?,?,?,?)",
                (row["chat_id"], row["message_id"], existing["text"], _now()),
            )
            self.conn.execute(
                "UPDATE messages SET text=?, edit_date=?, raw_json=coalesce(?, raw_json) WHERE chat_id=? AND message_id=?",
                (row["text"], row["edit_date"], row["raw_json"], row["chat_id"], row["message_id"]),
            )
            self.edited += 1
        elif row["edit_date"] and existing["edit_date"] != row["edit_date"]:
            self.conn.execute("UPDATE messages SET edit_date=? WHERE chat_id=? AND message_id=?",
                              (row["edit_date"], row["chat_id"], row["message_id"]))
        action = getattr(m, "action", None)
        if isinstance(action, types.MessageActionTopicEdit) and action.title and row["topic_id"]:
            self.conn.execute("UPDATE topics SET title=? WHERE chat_id=? AND topic_id=?",
                              (action.title, row["chat_id"], row["topic_id"]))
        if isinstance(action, types.MessageActionTopicCreate):
            self.conn.execute("INSERT OR IGNORE INTO topics(chat_id, topic_id, title) VALUES(?,?,?)",
                              (row["chat_id"], m.id, action.title))
        if isinstance(action, types.MessageActionChannelMigrateFrom) and item["kind"] == "supergroup":
            self.register_predecessor(item, action.chat_id)
        if item["forum"] and row["topic_id"] is not None and not self.topic_known(row["chat_id"], row["topic_id"]):
            self.topics_stale.add(row["chat_id"])

    def topic_known(self, chat_id: int, topic_id: int) -> bool:
        known = self.known_topics.get(chat_id)
        if known is None or topic_id not in known:
            known = self.known_topics[chat_id] = {
                r[0] for r in self.conn.execute("SELECT topic_id FROM topics WHERE chat_id=?", (chat_id,))}
        return topic_id in known

    def store_page(self, item: dict, response, *, cutoff: bool = True, above: int = 0,
                   below: int | None = None) -> tuple[list[int], list[int], bool]:
        """Store the messages of a history page with above < id < below.

        Returns (their ids, stored ids, reached_cutoff). The ids include MessageEmpty
        entries (deleted messages): they are positions in the history, so cursors move
        over them, but nothing is stored for them.

        With cutoff, the cutoff is reached when the lowest-id message is older than
        history_days. Then the run of old messages at the bottom of the page is left out
        and everything above it is stored, so the stored range has no holes even when
        dates are out of order. Cursors only move over stored ids in that case, so
        widening history_days later continues seamlessly.
        """
        names = self.save_entities(response)
        page = [m for m in response.messages if m.id > above and (below is None or m.id < below)]
        ids = [m.id for m in page]
        real = sorted((m for m in page if not isinstance(m, types.MessageEmpty)), key=lambda m: m.id)
        skip = 0
        if cutoff and self.cutoff:
            while skip < len(real) and (_iso(real[skip].date) or "") < self.cutoff:
                skip += 1
        stored: list[int] = []
        for m in real[skip:]:
            self.store(item, m, names)
            stored.append(m.id)
        return ids, stored, skip > 0

    @staticmethod
    def history_end(response, ids: list[int]) -> bool:
        """Nothing older exists below this page.

        A short page is not the end: Telegram leaves out messages it may not show. The end
        is an empty page, message id 1, or a complete answer (messages.messages, not a slice).
        """
        return not ids or min(ids) <= 1 or type(response) is types.messages.Messages

    def history_request(self, item, *, offset_id=0):
        # Only offset_id: min_id and max_id can make Telegram answer with nothing.
        return functions.messages.GetHistoryRequest(
            peer=item["peer"], offset_id=offset_id, offset_date=None, add_offset=0,
            limit=PAGE_SIZE, max_id=0, min_id=0, hash=0,
        )

    # per-chat steps ----------------------------------------------------------------

    def register_predecessor(self, item: dict, old_chat_id: int) -> dict:
        """The basic group a supergroup was upgraded from.

        An existing row (the group is in the dialog list, or was seen before) keeps its own
        title and archive flag and only gains migrated_to. A new row is titled after the
        supergroup, as the basic group's own title is unknown.
        """
        pred_id = utils.get_peer_id(types.PeerChat(old_chat_id))
        row = self.conn.execute("SELECT * FROM chats WHERE chat_id=?", (pred_id,)).fetchone()
        if row is None:
            title = f"{item['title']} (before upgrade)"
            self.upsert_chat(pred_id, "group", title, migrated_to=item["chat_id"],
                             archived=item.get("archived", False))
            pred = {"chat_id": pred_id, "kind": "group", "title": title,
                    "peer": types.InputPeerChat(old_chat_id), "entity": None, "forum": False,
                    "top_id": None, "date": None, "archived": item.get("archived", False)}
        else:
            if row["migrated_to"] is None:
                self.conn.execute("UPDATE chats SET migrated_to=? WHERE chat_id=?", (item["chat_id"], pred_id))
            pred = self.item_from_row(row)
        self.predecessors.setdefault(pred_id, pred)
        return pred

    async def channel_details(self, item: dict) -> None:
        """Once per supergroup (its first sync): member count and the basic group it was upgraded from."""
        try:
            full = await self.call(functions.channels.GetFullChannelRequest(channel=item["peer"]))
        except _Stop:
            raise
        except Exception as exc:
            log.info("Full channel info unavailable for %s: %s", item["chat_id"], type(exc).__name__)
            return
        self.save_entities(full)
        fc = full.full_chat
        if getattr(fc, "participants_count", None):
            self.conn.execute("UPDATE chats SET member_count=? WHERE chat_id=?", (fc.participants_count, item["chat_id"]))
        if getattr(fc, "migrated_from_chat_id", None):
            self.register_predecessor(item, fc.migrated_from_chat_id)
        self.conn.commit()

    async def topics_if_needed(self, item: dict) -> None:
        """Refresh the topic list when a stored message names a topic not known yet.

        Topic titles otherwise follow the topic service messages as they are stored.
        """
        if item["chat_id"] in self.topics_stale and item["chat_id"] not in self.topics_done:
            await self.refresh_topics(item)

    async def refresh_topics(self, item: dict) -> None:
        """The forum's topic list: on the first sync, and at most once a run after that."""
        self.topics_done.add(item["chat_id"])
        self.topics_stale.discard(item["chat_id"])
        self.known_topics.pop(item["chat_id"], None)
        self.conn.execute("INSERT OR IGNORE INTO topics(chat_id, topic_id, title) VALUES(?,?,?)",
                          (item["chat_id"], 1, "General"))
        offset_date, offset_id, offset_topic = None, 0, 0
        seen = set()
        for _ in range(MAX_TOPIC_PAGES):
            try:
                result = await self.call(functions.messages.GetForumTopicsRequest(
                    peer=item["peer"], offset_date=offset_date, offset_id=offset_id,
                    offset_topic=offset_topic, limit=100))
            except _Stop:
                raise
            except Exception as exc:
                log.info("Topics unavailable for %s: %s", item["chat_id"], type(exc).__name__)
                break
            full = [t for t in result.topics if isinstance(t, types.ForumTopic)]
            for t in full:
                self.conn.execute(
                    "INSERT INTO topics(chat_id, topic_id, title) VALUES(?,?,?) "
                    "ON CONFLICT(chat_id, topic_id) DO UPDATE SET title=excluded.title",
                    (item["chat_id"], t.id, t.title))
            if len(result.topics) < 100 or not full:
                break
            last = full[-1]
            top = next((m for m in result.messages if m.id == last.top_message), None)
            date = last.date if getattr(result, "order_by_create_date", False) else getattr(top, "date", None)
            key = (last.top_message, last.id)
            if date is None or key in seen:
                break
            seen.add(key)
            offset_date, offset_id, offset_topic = date, last.top_message, last.id
        self.conn.commit()

    async def recent(self, item: dict) -> None:
        """The latest page of a chat, when the dialog list shows something new.

        A chat the caller asked for by id gets its latest page read anyway, so edits to
        its recent messages show up even when nothing new arrived.

        newest_id moves to the top only together with a recorded gap for whatever lies
        between the page and the stored messages; fill_gaps reads it later in the run.
        """
        chat_id = item["chat_id"]
        st = self.state(chat_id)
        newest = st["newest_id"] if st else 0
        first = newest == 0  # also after a first sync that failed or found an empty chat
        if first and st is not None and st["backfill_done"] and item["entity"] is not None \
                and item["top_id"] is None:
            return  # an empty chat, already checked
        if first:
            if item["kind"] == "supergroup":
                await self.channel_details(item)
            if item["forum"]:
                await self.refresh_topics(item)
            await self.first_page(item)
            return
        top = item["top_id"]
        if top is not None and (top <= newest or top == self.seen_top(chat_id)) and chat_id not in self.explicit:
            return  # nothing new according to the dialog list
        page = await self.call(self.history_request(item))
        ids, _, _ = self.store_page(item, page, cutoff=False)
        if ids and max(ids) > newest:
            low = min(ids)
            gaps = self.get_gaps(chat_id)
            if low > newest + 1 and not self.history_end(page, ids):
                gaps.append((newest, low, False))
            self.set_gaps(chat_id, gaps)
            self.save_state(chat_id, newest_id=max(ids), error=None)
        else:
            self.save_state(chat_id, error=None)
        self.remember_top(item)
        self.conn.commit()
        await self.topics_if_needed(item)

    async def first_page(self, item: dict) -> None:
        """First sync of a chat: store the latest page so recent context shows up quickly."""
        chat_id = item["chat_id"]
        page = await self.call(self.history_request(item))
        ids, stored, reached = self.store_page(item, page)
        done = reached or self.history_end(page, ids)
        if not ids:
            oldest = None
        elif reached:
            # The cursor sits just above everything not stored yet.
            oldest = min(stored) if stored else max(ids) + 1
        else:
            oldest = min(ids)
        self.set_gaps(chat_id, [])
        self.save_state(chat_id, newest_id=max(ids, default=0), oldest_id=oldest,
                        backfill_done=int(done), error=None)
        self.remember_top(item)
        self.conn.commit()

    async def fill_gaps(self, item: dict, pages: int | None = None) -> bool:
        """Read recorded gaps top down, newest gap first, at most `pages` pages.

        Progress is saved after every page. The part of a gap older than history_days
        becomes a deferred gap. Returns True when no gap is left to fill now.
        """
        chat_id = item["chat_id"]
        for _ in range(pages or GAP_PAGES_PER_TURN):
            active = self.active_gaps(chat_id)
            if not active:
                break
            gap = active[0]
            low, high, _ = gap
            page = await self.call(self.history_request(item, offset_id=high))
            ids, stored, reached = self.store_page(item, page, above=low, below=high)
            gaps = [g for g in self.get_gaps(chat_id) if g != gap]
            if reached:
                edge = min(stored) if stored else high
                if edge > low + 1:
                    gaps.append((low, edge, True))
            elif ids and min(ids) > low + 1 and not self.history_end(page, ids):
                gaps.append((low, min(ids), False))
            self.set_gaps(chat_id, gaps)
            self.conn.commit()
            self.set_status(chat=item["title"])
        await self.topics_if_needed(item)
        return not self.active_gaps(chat_id)

    async def backfill(self, item: dict, pages: int | None = None) -> bool:
        """Older history below oldest_id, at most `pages` pages. Returns True when nothing is left."""
        finished = await self.backfill_pages(item, pages or BACKFILL_PAGES_PER_TURN)
        await self.topics_if_needed(item)
        return finished

    async def backfill_pages(self, item: dict, pages: int) -> bool:
        chat_id = item["chat_id"]
        for _ in range(pages):
            st = self.state(chat_id)
            if st is None or st["backfill_done"]:
                return True
            oldest = st["oldest_id"]
            if oldest is None:
                return True  # no first sync yet (or an empty chat): nothing to page from
            page = await self.call(self.history_request(item, offset_id=oldest), delay=BACKFILL_DELAY)
            ids, stored, reached = self.store_page(item, page, below=oldest)
            done = reached or self.history_end(page, ids)
            if reached:
                cursor = min(stored) if stored else oldest
            else:
                cursor = min(ids) if ids else oldest  # MessageEmpty ids move the cursor too
            self.save_state(chat_id, oldest_id=cursor, backfill_done=int(done))
            self.conn.commit()
            self.set_status(chat=item["title"])
            if done:
                return True
        return False

    def needs_backfill(self, chat_id: int) -> bool:
        st = self.state(chat_id)
        return st is not None and not st["backfill_done"] and st["oldest_id"] is not None

    def finish_chat(self, item: dict) -> None:
        chat_id = item["chat_id"]
        newest = self.conn.execute("SELECT max(date) FROM messages WHERE chat_id=?", (chat_id,)).fetchone()[0]
        if newest:
            self.conn.execute("UPDATE chats SET last_message_at=? WHERE chat_id=?", (newest, chat_id))
        self.save_state(chat_id, last_sync_at=_now(), error=None)
        self.conn.commit()

    async def guarded(self, item: dict, step) -> Any:
        """Run one step for one chat. An error is recorded and ends this chat's work for the run."""
        chat_id = item["chat_id"]
        try:
            result = await step(item)
            self.finish_chat(item)
            return result
        except (_Stop, asyncio.CancelledError):
            self.conn.commit()
            raise
        except Exception as exc:
            self.conn.rollback()
            name = type(exc).__name__
            self.failed.add(chat_id)
            if isinstance(exc, _PERMANENT):
                # Expected for chats you left or were removed from: not a failed run.
                log.info("Chat %s is not accessible: %s", chat_id, name)
                self.mark_inaccessible(chat_id, name)
                self.inaccessible.append(chat_id)
                if chat_id in self.explicit:
                    self.errors.append({"chat_id": chat_id, "title": item["title"], "error": f"{INACCESSIBLE}: {name}"})
            else:
                log.warning("Chat %s could not be synced: %s", chat_id, name)
                self.errors.append({"chat_id": chat_id, "title": item["title"], "error": name})
                # Only an existing row: a new one would look like a finished first sync.
                self.conn.execute("UPDATE sync_state SET error=? WHERE chat_id=?", (name, chat_id))
            self.conn.commit()
            return None

    # profile photos ----------------------------------------------------------------

    def avatar_todo(self, items: list[dict], refresh: bool = False) -> list[tuple[dict, int]]:
        """Chats whose profile photo is new, changed or missing on disk. Clears removed photos.

        Needs no request: the dialog list's entities carry the photo id. Excluded chats are
        left alone, and so are chats known only from the archive (no entity).
        """
        excluded = set(self.settings.excluded_chats or [])
        media = config.media_dir()
        todo = []
        for item in items:
            entity = item.get("entity")
            if entity is None or item["chat_id"] in excluded:
                continue
            row = self.conn.execute("SELECT avatar_path, photo_id FROM chats WHERE chat_id=?",
                                    (item["chat_id"],)).fetchone()
            if row is None:
                continue
            photo_id = _photo_id(entity)
            if photo_id is None:
                if row["avatar_path"] or row["photo_id"]:
                    self.conn.execute("UPDATE chats SET avatar_path=NULL, photo_id=NULL WHERE chat_id=?",
                                      (item["chat_id"],))
                    self.conn.commit()
                    remove_avatar_file(row["avatar_path"])
                    self.avatar_counts["removed"] += 1
                continue
            present = row["avatar_path"] and (media / row["avatar_path"]).is_file()
            if refresh or row["photo_id"] != photo_id or not present:
                todo.append((item, photo_id))
        return todo

    async def avatars(self, items: list[dict], refresh: bool = False) -> None:
        todo = self.avatar_todo(items, refresh)
        if not todo:
            return
        self.set_status(stage="avatars", chats_total=len(todo), chats_done=0)
        for i, (item, photo_id) in enumerate(todo):
            self.set_status(chat=item["title"], chats_done=i)
            await self.fetch_avatar(item, photo_id)
        self.set_status(chats_done=len(todo))

    async def fetch_avatar(self, item: dict, photo_id: int) -> bool:
        """Download one small profile photo. A failure records nothing; the next run tries again."""
        chat_id = item["chat_id"]
        while True:
            self.check_time()
            wait = AVATAR_DELAY - (time.monotonic() - self.last_request)
            if wait > 0:
                await asyncio.sleep(wait)
                self.check_time()
            self.last_request = time.monotonic()
            try:
                data = await self.client.download_profile_photo(item["entity"], file=bytes, download_big=False)
                break
            except errors.FloodWaitError as exc:
                await self.flood_wait(exc.seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # PhotoInvalid, ChannelPrivate, a deleted account, network trouble
                log.info("Profile photo unavailable for %s: %s", chat_id, type(exc).__name__)
                self.avatar_counts["failed"] += 1
                return False
        if not isinstance(data, bytes) or not data:
            self.avatar_counts["failed"] += 1
            return False
        folder = avatars_dir()
        final = folder / f"{chat_id}-{photo_id}.jpg"
        part = folder / f"{chat_id}-{photo_id}.jpg.part"
        try:
            part.write_bytes(data)
            part.chmod(0o600)
            os.replace(part, final)
        except OSError as exc:
            log.warning("Could not save the profile photo of %s: %s", chat_id, type(exc).__name__)
            part.unlink(missing_ok=True)
            self.avatar_counts["failed"] += 1
            return False
        rel = f"{AVATARS}/{final.name}"
        old = self.conn.execute("SELECT avatar_path FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        self.conn.execute("UPDATE chats SET avatar_path=?, photo_id=? WHERE chat_id=?", (rel, photo_id, chat_id))
        self.conn.commit()
        if old is not None and old["avatar_path"] and old["avatar_path"] != rel:
            remove_avatar_file(old["avatar_path"])
        self.avatar_counts["downloaded"] += 1
        return True

    # main --------------------------------------------------------------------------

    def check_history_setting(self) -> None:
        """If the user now wants more history than before, reopen the backfill and deferred gaps."""
        days = self.settings.history_days
        before = db.get_meta(self.conn, "history_days")
        value = "all" if days is None else str(days)
        if before is not None and before != value:
            widened = value == "all" or (before != "all" and int(value) > int(before))
            if widened:
                self.conn.execute("UPDATE sync_state SET backfill_done=0")
                self.conn.execute("UPDATE meta SET value=replace(value, ',old', '') WHERE key LIKE 'gap:%'")
        db.set_meta(self.conn, "history_days", value)
        self.conn.commit()

    def queue_predecessors(self, work: list[dict]) -> list[dict]:
        """Move discovered predecessor groups into the work list. Returns the ones added."""
        added = []
        in_work = {w["chat_id"] for w in work}
        while self.predecessors:
            pid, pred = self.predecessors.popitem()
            if pid in in_work:
                continue
            if pid not in self.explicit:
                if self.explicit:
                    if pid in set(self.settings.excluded_chats or []):
                        continue
                elif not self.wanted("group", False, pid):
                    continue
                if self.recently_inaccessible(pid):
                    continue
                st = self.state(pid)
                if st is not None and st["newest_id"] and st["backfill_done"]:
                    continue  # an upgraded group gets no new messages; its history is complete
            work.append(pred)
            in_work.add(pid)
            added.append(pred)
        return added

    async def check_account(self):
        """The logged-in user. Raises OtherAccount before anything is written for another account."""
        me = await self.client.get_me()
        if me is not None and db.other_account(utils.get_peer_id(me), self.conn):
            raise OtherAccount()
        return me

    async def run(self, chat_ids: list[int] | None) -> dict:
        self.set_status(stage="connecting")
        me = await self.check_account()
        if me is not None:
            self.owner_id = utils.get_peer_id(me)
            self.owner_name = _person_name(me) if not getattr(me, "deleted", False) else "Me"
            self.upsert_sender(self.owner_id, self.owner_name, getattr(me, "username", None))
            db.set_meta(self.conn, "owner_id", str(self.owner_id))
        self.check_history_setting()

        work: list[dict] = []
        stopped = None
        try:
            found = await self.inventory(chat_ids)
            skipped = [w for w in found if self.recently_inaccessible(w["chat_id"])]
            self.inaccessible.extend(w["chat_id"] for w in skipped)
            work = [w for w in found if w not in skipped]
            # Known predecessors of the chats in this run (upgraded basic groups).
            for item in list(work):
                if item["kind"] == "supergroup":
                    for row in self.conn.execute("SELECT * FROM chats WHERE migrated_to=?", (item["chat_id"],)):
                        self.predecessors.setdefault(row["chat_id"], self.item_from_row(row))

            # Step 2: recent messages for every chat.
            self.set_status(stage="recent", chats_total=len(work), chats_done=0)
            i = 0
            while True:
                if self.queue_predecessors(work):
                    self.status["chats_total"] = len(work)
                if i >= len(work):
                    break
                item = work[i]
                self.set_status(chat=item["title"], chats_done=i)
                await self.guarded(item, self.recent)
                i += 1

            # Gaps behind the latest pages, round-robin, once every chat has its latest page.
            gapped = [w for w in work if w["chat_id"] not in self.failed and self.active_gaps(w["chat_id"])]
            while gapped:
                for item in list(gapped):
                    self.set_status(chat=item["title"])
                    finished = await self.guarded(item, self.fill_gaps)
                    if finished is not False:  # True, or None after an error
                        gapped.remove(item)
            self.set_status(chats_done=len(work))

            # Profile photos: after the new messages, before the (long) history backfill.
            await self.avatars(work)

            # Step 3: older history, round-robin so every chat makes progress.
            pending = [w for w in work if w["chat_id"] not in self.failed and self.needs_backfill(w["chat_id"])]
            total = len(pending)
            self.set_status(stage="history", chats_total=total, chats_done=0)
            while pending:
                for item in list(pending):
                    self.set_status(chat=item["title"], chats_done=total - len(pending))
                    finished = await self.guarded(item, self.backfill)
                    if finished is not False:  # True, or None after an error
                        pending.remove(item)
                # Predecessors found in old history (an "upgraded from" service message).
                for pred in self.queue_predecessors(work):
                    self.set_status(chat=pred["title"])
                    await self.guarded(pred, self.recent)
                    if pred["chat_id"] not in self.failed and self.needs_backfill(pred["chat_id"]):
                        pending.append(pred)
                        total += 1
                        self.status["chats_total"] = total
            self.set_status(chats_done=total)
        except _Stop as stop:
            stopped = stop
        ids = [w["chat_id"] for w in work]
        remaining = self.conn.execute(
            f"SELECT count(*) FROM sync_state WHERE backfill_done=0 AND oldest_id IS NOT NULL "
            f"AND chat_id IN ({','.join('?' * len(ids))})",
            ids).fetchone()[0] if ids else 0
        gaps = sum(1 for w in work if self.active_gaps(w["chat_id"]))
        return {
            "ok": stopped is None and not self.errors,
            "stopped": stopped.reason if stopped else None,
            "message": str(stopped) if stopped else None,
            "resumable": stopped is not None or remaining > 0 or gaps > 0,
            "chats": len(work),
            "new_messages": self.new_messages,
            "edited_messages": self.edited,
            "history_pending_chats": remaining,
            "inaccessible_chats": len(set(self.inaccessible)),
            "avatars": dict(self.avatar_counts),
            "errors": self.errors,
        }


async def _connected_client():
    from . import telegram

    client = telegram.make_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise NotAuthorized("Dotiphant is not logged in to Telegram. Run `dotiphant setup` first.")
    return client


async def _auto_download(runner, client, settings: config.Settings, chat_ids: list[int] | None,
                         started: float, max_seconds: float | None) -> int:
    """After the new messages: fetch the attachments of chats set to download automatically."""
    runner.conn.commit()  # download_media writes through its own connection
    downloaded = 0
    for chat_id in settings.auto_download_chats:
        if chat_ids is not None and chat_id not in chat_ids:
            continue
        if max_seconds and time.monotonic() - started > max_seconds:
            break
        row = runner.conn.execute("SELECT title FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        if row is None or is_whatsapp_id(chat_id):
            continue
        runner.set_status(stage="downloading_media", chat=row["title"])
        try:
            counts = await download_media(chat_id, client=client, hold_lock=False)  # run_sync holds it
        except Exception as exc:  # one chat's files must not fail the whole sync
            log.warning("Automatic download failed for %s: %s", chat_id, type(exc).__name__)
            continue
        downloaded += counts["downloaded"]
    return downloaded


async def run_sync(chat_ids: list[int] | None = None, *, client=None, settings: config.Settings | None = None,
                   progress: Callable[[dict], None] | None = None, max_seconds: float | None = None) -> dict:
    """Sync the archive with Telegram. Raises SyncBusy when another sync holds the lock."""
    lock = _Lock()
    lock.acquire()
    own_client = client is None
    conn = None
    runner = None
    started = time.monotonic()
    try:
        settings = settings or config.Settings.load()
        # Announce the run (fresh started_at and pid) as soon as the lock is held.
        runner = _Runner(None, None, settings, progress, max_seconds)
        runner.set_status(stage="starting")
        conn = runner.conn = db.open_rw()
        if own_client:
            client = await _connected_client()
        runner.client = client
        summary = await runner.run(chat_ids)
        if not summary["stopped"]:
            summary["media_downloaded"] = await _auto_download(runner, client, settings, chat_ids, started, max_seconds)
        summary["seconds"] = round(time.monotonic() - started, 1)
        if summary["stopped"]:
            message = summary["message"]
        elif summary["errors"]:
            message = f"{len(summary['errors'])} chat(s) could not be read; they are retried next time."
        else:
            message = None
        # Errors in single chats leave the run "idle" (it did its work), with the count to show.
        failed = [{"chat_id": e["chat_id"], "error": e["error"]} for e in summary["errors"]][:FAILED_CHATS_SHOWN]
        runner.set_status(state="idle", stage="done", chat=None, finished_at=_now(), message=message,
                          errors=len(summary["errors"]), failed_chats=failed)
        return summary
    except BaseException as exc:
        if runner is not None:
            if isinstance(exc, (NotAuthorized, config.SecretsUnavailable)):
                text = str(exc)
            elif isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                text = "Sync was interrupted. The next sync continues where this one stopped."
            else:
                text = f"Sync failed ({type(exc).__name__}). The next sync continues where this one stopped."
            state = "idle" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "error"
            runner.set_status(state=state, finished_at=_now(), message=text)
        raise
    finally:
        if conn is not None:
            try:
                conn.commit()
            except sqlite3.Error:
                pass
            conn.close()
        if own_client and client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        lock.release()


async def run_inventory(*, client=None, settings: config.Settings | None = None) -> int:
    """Refresh the chat list only (no messages). Returns the number of chats kept.

    Holds the same lock as run_sync and leaves status.json alone.
    """
    lock = _Lock()
    lock.acquire()
    own_client = client is None
    conn = None
    try:
        settings = settings or config.Settings.load()
        conn = db.open_rw()
        if own_client:
            client = await _connected_client()
        runner = _Runner(client, conn, settings, None, None, write_status=False)
        await runner.check_account()
        items = await runner.inventory(None)
        conn.commit()
        return len(items)
    except _Stop:
        return 0
    finally:
        if conn is not None:
            conn.close()
        if own_client and client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        lock.release()


async def run_avatars(chat_ids: list[int] | None = None, *, refresh: bool = False, client=None,
                      settings: config.Settings | None = None, progress: Callable[[dict], None] | None = None,
                      max_seconds: float | None = None) -> dict:
    """Fetch new or changed profile photos without syncing messages.

    Walks the chat list (the settings' filters apply, excluded chats are skipped), then
    downloads the photos that changed, or every photo with refresh. Holds the sync lock and
    leaves status.json alone. Returns counts: chats, downloaded, removed, failed, missing.
    """
    lock = _Lock()
    lock.acquire()
    own_client = client is None
    conn = None
    try:
        settings = settings or config.Settings.load()
        conn = db.open_rw()
        if own_client:
            client = await _connected_client()
        runner = _Runner(client, conn, settings, progress, max_seconds, write_status=False)
        await runner.check_account()
        stopped = None
        items: list[dict] = []
        missing: list[int] = []
        try:
            items = await runner.inventory(None)
            if chat_ids:
                wanted = set(chat_ids)
                missing = sorted(wanted - {i["chat_id"] for i in items})
                items = [i for i in items if i["chat_id"] in wanted]
            await runner.avatars(items, refresh=refresh)
        except _Stop as stop:
            stopped = stop
        conn.commit()
        return {"chats": len(items), **runner.avatar_counts, "missing": missing,
                "stopped": stopped.reason if stopped else None, "message": str(stopped) if stopped else None}
    finally:
        if conn is not None:
            conn.close()
        if own_client and client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        lock.release()


# --- media -----------------------------------------------------------------------------

async def _resolve_peer(client, row) -> Any:
    chat_id, kind = row["chat_id"], row["kind"]
    if kind == "group":
        return types.InputPeerChat(-chat_id)
    peer = types.PeerUser(chat_id) if kind in ("private", "bot") else types.PeerChannel(
        utils.resolve_id(chat_id)[0])
    try:
        return await client.get_input_entity(peer)
    except (ValueError, TypeError, errors.RPCError):
        pass
    # A fresh StringSession has no entity cache; the dialog list provides access hashes.
    async for dialog in client.iter_dialogs(ignore_migrated=False):
        if utils.get_peer_id(dialog.entity) == chat_id:
            return utils.get_input_peer(dialog.entity)
    raise LookupError(f"Chat {chat_id} is not in your Telegram chats any more.")


async def download_media(chat_id: int, message_ids: list[int] | None = None, *, client=None,
                         hold_lock: bool = True) -> dict:
    """Download attachments of one chat into media/<chat_id>/ and record their paths.

    Without message_ids, every not yet downloaded photo, video, document, voice note, audio
    file and sticker of the chat is fetched; files above 50 MB are skipped unless asked for
    by message id.

    Holds the sync lock, so `dotiphant forget` cannot delete the chat halfway through and
    have files come back. Raises SyncBusy when another process holds it. Pass
    hold_lock=False only when the caller already holds the lock (the automatic downloads
    of run_sync).
    """
    lock = _Lock()
    if hold_lock:
        lock.acquire()
    try:
        return await _download_media(chat_id, message_ids, client)
    finally:
        lock.release()


class _ChatGone(Exception):
    """The chat was deleted from the archive while its files were downloading."""


def _chat_exists(conn: sqlite3.Connection, chat_id: int) -> bool:
    return conn.execute("SELECT 1 FROM chats WHERE chat_id=?", (chat_id,)).fetchone() is not None


async def _download_media(chat_id: int, message_ids: list[int] | None, client) -> dict:
    conn = db.open_rw()
    own_client = client is None
    counts = {"downloaded": 0, "already_present": 0, "skipped_large": 0, "unavailable": 0, "failed": 0}
    try:
        row = conn.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
        if row is None:
            raise LookupError(f"Chat {chat_id} is not in the archive.")
        explicit = set(message_ids or [])
        marks = ",".join("?" * len(db.DOWNLOADABLE))
        if message_ids:
            rows = conn.execute(
                f"SELECT message_id, media_type, media_name, media_size, media_path FROM messages "
                f"WHERE chat_id=? AND message_id IN ({','.join('?' * len(explicit))}) AND media_type IN ({marks})",
                (chat_id, *explicit, *db.DOWNLOADABLE)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT message_id, media_type, media_name, media_size, media_path FROM messages "
                f"WHERE chat_id=? AND media_type IN ({marks}) AND media_path IS NULL ORDER BY message_id DESC",
                (chat_id, *db.DOWNLOADABLE)).fetchall()
        root = config.media_dir()
        folder = root / str(chat_id)
        todo = []
        for r in rows:
            if r["media_path"] and (root / r["media_path"]).exists():
                counts["already_present"] += 1
            elif (r["media_size"] or 0) > LARGE_FILE and r["message_id"] not in explicit:
                counts["skipped_large"] += 1
            else:
                todo.append(r)
        if not todo:
            return counts
        if is_whatsapp_id(chat_id):  # imported from a WhatsApp export: files come only with --copy-media
            counts["unavailable"] += len(todo)
            return counts
        if own_client:
            client = await _connected_client()
        me = await client.get_me()
        if me is not None and db.other_account(utils.get_peer_id(me), conn):
            raise OtherAccount()  # message ids are per account: the file would belong to another message
        peer = await _resolve_peer(client, row)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            folder.chmod(0o700)
        except OSError:
            pass
        for start in range(0, len(todo), PAGE_SIZE):
            batch = todo[start:start + PAGE_SIZE]
            while True:
                try:
                    found = await client.get_messages(peer, ids=[r["message_id"] for r in batch])
                    break
                except errors.FloodWaitError as exc:
                    if exc.seconds > MAX_FLOOD_WAIT:
                        raise
                    await asyncio.sleep(exc.seconds + 1)
            by_id = {m.id: m for m in (found or []) if m is not None}
            for r in batch:
                mid = r["message_id"]
                msg = by_id.get(mid)
                if msg is None or getattr(msg, "media", None) is None:
                    counts["unavailable"] += 1
                    continue
                name = db.safe_filename(r["media_name"] or DEFAULT_NAMES.get(r["media_type"], "file"))
                final = folder / f"{mid}-{name}"
                if not final.exists():
                    part = folder / f"{mid}-{name}.part"
                    try:
                        while True:
                            try:
                                result = await client.download_media(msg, file=str(part))
                                break
                            except errors.FloodWaitError as exc:
                                if exc.seconds > MAX_FLOOD_WAIT:
                                    raise
                                await asyncio.sleep(exc.seconds + 1)
                        if not result:
                            counts["unavailable"] += 1
                            continue
                        if not _chat_exists(conn, chat_id):
                            # Deleted from the archive meanwhile: keep nothing of it.
                            Path(result).unlink(missing_ok=True)
                            with contextlib.suppress(OSError):
                                folder.rmdir()  # only when empty
                            raise _ChatGone()
                        os.replace(result, final)
                        final.chmod(0o600)
                    except (errors.FloodWaitError, _ChatGone):
                        raise
                    except Exception as exc:
                        log.warning("Download failed for a message in %s: %s", chat_id, type(exc).__name__)
                        Path(part).unlink(missing_ok=True)
                        counts["failed"] += 1
                        continue
                    counts["downloaded"] += 1
                else:
                    counts["already_present"] += 1
                conn.execute("UPDATE messages SET media_path=? WHERE chat_id=? AND message_id=?",
                             (str(final.relative_to(root)), chat_id, mid))
                conn.commit()
                await asyncio.sleep(PAGE_DELAY)
        return counts
    except _ChatGone:
        return counts
    finally:
        conn.commit()
        conn.close()
        if own_client and client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
