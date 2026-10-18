"""Import a chat exported with WhatsApp's own "Export chat" into the archive.

WhatsApp writes one chat as a text file, `_chat.txt` on iPhone or
`WhatsApp Chat with NAME.txt` on Android, alone or in a .zip with the attached files.
Nothing here connects to WhatsApp: the importer only reads that file.

The text format depends on the phone and its language settings:

    [31/01/2024, 14:05:09] Clara: Hi              iPhone, day first, 24 hour clock
    [1/31/24, 2:05:09\u202fPM] Clara: Hi          iPhone, US style, 12 hour clock
    31/01/2024, 14:05 - Clara: Hi                Android, day first
    1/31/24, 2:05\u202fPM - Clara: Hi             Android, US style
    31.01.24, 14:05 - Clara: Hi                  Android, German

Lines without a timestamp continue the message above. Lines without "Name:" are system
lines (encryption notice, "X added Y"). iPhone puts an invisible U+200E mark in front of
attachments and system text, and writes group system lines with the group name as sender.

Rules:
- Day and month order is read from the file: a first number above 12 means day first, a
  second number above 12 means month first. If no value decides it, the order that keeps
  the messages in time order wins; when both do, `date_order` (or day first) is used and
  the report says it was a guess.
- Timestamps carry no zone. They are read in `tz` (an IANA name like Europe/Berlin) or the
  computer's local zone, and stored in UTC.
- Chat and sender ids come from a stable hash of the chat title and sender name, in a range
  Telegram never uses (see WHATSAPP_ID_MIN). The same title gives the same chat, so a later
  export of the chat lands in the same place. --title changes the title and so the chat.
- Message ids count up from 1 in file order. A message whose (date, sender, content)
  is already stored for the chat is not added again, counting repeats, so importing the
  same export again adds nothing and a newer export adds only what is new. New messages
  get ids after the highest stored one.
- `sync_state` is never written; the Telegram sync skips these chats (see is_whatsapp_id).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config, db, status
from .importer import BUSY, FORGOTTEN

# Telegram ids stay far below this (users and chats under 1e13, channels near -1e12).
WHATSAPP_ID_MIN = 9_000_000_000_000_000_000
SENDER_ID_MIN = 9_100_000_000_000_000_000
HASH_SPAN = 100_000_000_000_000_000  # 1e17: chat ids end below 9.1e18, sender ids below 9.2e18 (int64 max is 9.22e18)

MAX_CHAT_BYTES = 512 * 1024 * 1024
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024

CHAT_FILE_RE = re.compile(r"^(?:_chat\.txt|WhatsApp[ -]Chat.*\.txt)$", re.IGNORECASE)
# "WhatsApp Chat with Clara", "WhatsApp Chat mit Clara", "WhatsApp Chat - Clara"
TITLE_FROM_NAME_RE = re.compile(r"^WhatsApp[ -]Chat\s*(?:-\s*|with\s+|mit\s+|avec\s+|con\s+|met\s+|com\s+)?(.+)$",
                                re.IGNORECASE)

DIRECTION_MARKS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"
_STRIP_MARKS = str.maketrans("", "", DIRECTION_MARKS)

HEADER_RE = re.compile(
    r"^[\u200e\u200f]*\[?"
    r"(?P<date>\d{1,4}[-/.]\s?\d{1,2}[-/.]\s?\d{1,4})\.?"
    r"[,.]?\s[^\d\n]{0,6}?"
    r"(?P<time>\d{1,2}[.:]\d{2}(?:[.:]\d{2})?)"
    r"(?:\s?(?P<ampm>[aApP]\.?\s?[mM]\.?))?"
    r"\]?(?:\s[-\u2013]|:)?\s"
    r"(?P<rest>.*)$"
)
SENDER_RE = re.compile(r"^(?P<name>[^:\n]{1,80}?):\s(?P<text>.*)$", re.DOTALL)

ENCRYPTION_RE = re.compile(
    r"^(Messages and calls are end-to-end encrypted|Messages to this (group|chat) are now secured"
    r"|Nachrichten und Anrufe sind Ende-zu-Ende-verschl|Your security code with|Dein Sicherheitscode"
    r"|.*\bsecurity code changed|Messages you send to this (chat|group) and calls are now secured)",
    re.IGNORECASE)
GROUP_HINT_RE = re.compile(
    r"\b(created (the )?group|created this group|added|removed|left|joined using|changed the subject"
    r"|changed (this|the) group|group|Gruppe|hinzugef\u00fcgt|entfernt|ist beigetreten|verlassen)\b",
    re.IGNORECASE)
DELETED_RE = re.compile(
    r"^(This message was deleted\.?|You deleted this message\.?|Diese Nachricht wurde gel\u00f6scht\.?"
    r"|Du hast diese Nachricht gel\u00f6scht\.?)$", re.IGNORECASE)
EDITED_RE = re.compile(r"[\s\u200e]*<(This message was edited|Diese Nachricht wurde bearbeitet)\.?>\s*$", re.IGNORECASE)
MEDIA_OMITTED_RE = re.compile(r"^<(Media omitted|Medien ausgeschlossen|Medien weggelassen)>$", re.IGNORECASE)
IOS_OMITTED_RE = re.compile(
    r"^(?:(?P<name>.+?)\s+(?:\u2022.*?\s+)?)?(?P<what>image|video|audio|sticker|GIF|document|Contact card"
    r"|Bild|Video|Audio|Sticker|GIF|Dokument|Kontaktkarte) (omitted|weggelassen)$", re.IGNORECASE)
IOS_ATTACHED_RE = re.compile(r"^<(?:attached|Anhang):\s*(?P<name>[^<>\n]+?)\s*>$", re.IGNORECASE)
ANDROID_ATTACHED_RE = re.compile(r"^(?P<name>[^\n]+?\.\w{1,6})\s\((file attached|Datei angeh\u00e4ngt)\)$",
                                 re.IGNORECASE)
LOCATION_RE = re.compile(r"^(?:Location|Standort):\s*(https?://\S+)$", re.IGNORECASE)

OMITTED_TYPES = {"image": "photo", "bild": "photo", "video": "video", "audio": "audio", "sticker": "sticker",
                 "gif": "video", "document": "document", "dokument": "document",
                 "contact card": "contact", "kontaktkarte": "contact"}
EXT_TYPES = {".jpg": "photo", ".jpeg": "photo", ".png": "photo", ".heic": "photo", ".webp": "sticker",
             ".mp4": "video", ".mov": "video", ".3gp": "video", ".gif": "video",
             ".opus": "voice", ".m4a": "audio", ".mp3": "audio", ".aac": "audio", ".ogg": "audio"}


class ExportFormatError(ValueError):
    """The path is not a WhatsApp chat export."""


def is_whatsapp_id(chat_id: int | None) -> bool:
    """True for chat ids this importer made. The Telegram sync never fetches these."""
    return chat_id is not None and chat_id >= WHATSAPP_ID_MIN


def _stable_hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big") % HASH_SPAN


def chat_id_for(title: str) -> int:
    return WHATSAPP_ID_MIN + _stable_hash("chat:" + title.strip().casefold())


def sender_id_for(name: str) -> int:
    return SENDER_ID_MIN + _stable_hash("sender:" + name.strip().casefold())


def _clean_name(name: str) -> str:
    return " ".join(name.translate(_STRIP_MARKS).split())


# --- finding and reading the export ---------------------------------------------------------

@dataclass
class Source:
    text: str
    title: str | None
    zip_path: Path | None = None
    folder: Path | None = None
    chat_member: str | None = None


def _title_from_filename(name: str) -> str | None:
    stem = Path(name).stem
    match = TITLE_FROM_NAME_RE.match(stem)
    return _clean_name(match.group(1)) if match and _clean_name(match.group(1)) else None


def _chat_member(zf: zipfile.ZipFile) -> str | None:
    names = [i.filename for i in zf.infolist() if not i.is_dir() and CHAT_FILE_RE.match(PurePosixPath(i.filename).name)]
    return min(names, key=lambda n: (n.count("/"), n)) if names else None


def detect(path: Path | str) -> bool:
    """True when the path looks like a WhatsApp export: a chat .txt, a .zip holding one, or its folder."""
    p = Path(path).expanduser()
    try:
        if p.is_file() and p.suffix.lower() == ".txt":
            return bool(CHAT_FILE_RE.match(p.name)) or _looks_like_chat(p)
        if p.is_file() and p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as zf:
                return _chat_member(zf) is not None
        if p.is_dir() and not (p / "result.json").exists():
            return any(CHAT_FILE_RE.match(c.name) for c in p.iterdir() if c.is_file())
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def _looks_like_chat(p: Path) -> bool:
    with p.open(encoding="utf-8-sig", errors="replace") as f:
        head = [f.readline() for _ in range(5)]
    return any(HEADER_RE.match(line.rstrip("\r\n")) for line in head)


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def open_source(path: Path | str) -> Source:
    p = Path(path).expanduser()
    if p.is_dir():
        chats = sorted(c for c in p.iterdir() if c.is_file() and CHAT_FILE_RE.match(c.name))
        if not chats:
            raise ExportFormatError(f"No WhatsApp chat file (_chat.txt or 'WhatsApp Chat with ...txt') in {p}.")
        p = chats[0]
    if not p.is_file():
        raise ExportFormatError(f"No WhatsApp export at {p}.")
    if p.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(p) as zf:
                member = _chat_member(zf)
                if member is None:
                    raise ExportFormatError(f"{p} has no WhatsApp chat file (_chat.txt or 'WhatsApp Chat with ...txt').")
                info = zf.getinfo(member)
                if info.file_size > MAX_CHAT_BYTES:
                    raise ExportFormatError(f"The chat file in {p} is larger than {MAX_CHAT_BYTES // 2**20} MB.")
                text = _decode(zf.read(member))
        except zipfile.BadZipFile:
            raise ExportFormatError(f"{p} is not a readable .zip file.") from None
        title = _title_from_filename(p.name) or _title_from_filename(PurePosixPath(member).name)
        return Source(text=text, title=title, zip_path=p, chat_member=member)
    if p.stat().st_size > MAX_CHAT_BYTES:
        raise ExportFormatError(f"{p} is larger than {MAX_CHAT_BYTES // 2**20} MB.")
    title = _title_from_filename(p.name)
    if title is None and p.name.lower() == "_chat.txt":
        title = _title_from_filename(p.parent.name)
    return Source(text=_decode(p.read_bytes()), title=title, folder=p.parent)


# --- parsing --------------------------------------------------------------------------------

@dataclass
class Entry:
    date: tuple[int, int, int]
    year_first: bool
    time: str
    ampm: str | None
    lines: list[str] = field(default_factory=list)


def _normalize_line(line: str) -> str:
    return line.replace("\u202f", " ").replace("\u00a0", " ")


def split_entries(text: str) -> list[Entry]:
    entries: list[Entry] = []
    for raw in re.split(r"\r\n|\n|\r", text):
        line = _normalize_line(raw)
        m = HEADER_RE.match(line)
        if m:
            parts = [int(x) for x in re.split(r"[-/.]\s?", m["date"])]
            ampm = m["ampm"]
            if ampm:
                ampm = "pm" if ampm.lower().startswith("p") else "am"
            year_first = len(re.split(r"[-/.]", m["date"])[0].strip()) == 4
            entries.append(Entry((parts[0], parts[1], parts[2]), year_first, m["time"], ampm, [m["rest"]]))
        elif entries:
            entries[-1].lines.append(raw.replace("\u202f", " "))
    return entries


def _to_date(e: Entry, order: str) -> tuple[int, int, int]:
    """(year, month, day) for an entry under 'dmy' or 'mdy'."""
    a, b, c = e.date
    if e.year_first:
        return a, b, c
    year = c + 2000 if c < 100 else c
    return (year, b, a) if order == "dmy" else (year, a, b)


def detect_date_order(entries: list[Entry], preferred: str | None = None) -> tuple[str, bool]:
    """('dmy' or 'mdy', guessed). Values above 12 decide; then time order; then `preferred`."""
    day_first = month_first = False
    for e in entries:
        if e.year_first:
            continue
        day_first |= e.date[0] > 12
        month_first |= e.date[1] > 12
    if day_first and not month_first:
        return "dmy", False
    if month_first and not day_first:
        return "mdy", False
    if day_first and month_first:
        if preferred:
            return preferred, False
        raise ExportFormatError("The dates in this export mix day-first and month-first values. "
                                "Pass --date-order dmy or mdy.")

    def in_order(order: str) -> bool:
        last = None
        for e in entries:
            try:
                d = dt.date(*_to_date(e, order))
            except ValueError:
                return False
            if last and d < last:
                return False
            last = d
        return True

    dmy, mdy = in_order("dmy"), in_order("mdy")
    if dmy and not mdy:
        return "dmy", False
    if mdy and not dmy:
        return "mdy", False
    if preferred:
        return preferred, False
    if all(_to_date(e, "dmy") == _to_date(e, "mdy") for e in entries):
        return "dmy", False  # only dates like 5/5: both orders read the same
    return "dmy", True


def _timestamp(e: Entry, order: str, zone: dt.tzinfo | None) -> str:
    year, month, day = _to_date(e, order)
    parts = [int(x) for x in re.split(r"[.:]", e.time)]
    hour, minute, second = parts[0], parts[1], parts[2] if len(parts) > 2 else 0
    if e.ampm:
        if not 1 <= hour <= 12:
            raise ValueError("hour out of range for a 12 hour clock")
        hour = hour % 12 + (12 if e.ampm == "pm" else 0)
    naive = dt.datetime(year, month, day, hour, minute, second)
    local = naive.replace(tzinfo=zone) if zone is not None else naive.astimezone()
    return local.astimezone(dt.timezone.utc).isoformat()


def _strip_lrm(text: str) -> str:
    return "\n".join(line.lstrip("\u200e\u200f") for line in text.split("\n"))


@dataclass
class Parsed:
    date: str
    sender: str | None
    kind: str          # text | media | service
    text: str = ""
    service_text: str | None = None
    media_type: str | None = None
    media_name: str | None = None
    edited: bool = False
    system: bool = False


def _media_type_for(name: str) -> str:
    upper = name.upper()
    for marker, kind in (("-PHOTO-", "photo"), ("-VIDEO-", "video"), ("-AUDIO-", "voice"), ("-STICKER-", "sticker"),
                         ("PTT-", "voice"), ("IMG-", "photo"), ("VID-", "video"), ("STK-", "sticker"),
                         ("AUD-", "audio"), ("-GIF-", "video")):
        if marker in upper:
            return kind
    return EXT_TYPES.get(Path(name).suffix.lower(), "document")


def interpret(e: Entry, order: str, zone: dt.tzinfo | None, title: str | None) -> Parsed:
    date = _timestamp(e, order, zone)
    body = "\n".join(e.lines)
    m = SENDER_RE.match(body)
    if not m:
        return Parsed(date, None, "service", service_text=_strip_lrm(body).translate(_STRIP_MARKS).strip(), system=True)
    sender = _clean_name(m["name"])
    raw_text = m["text"]
    text = _strip_lrm(raw_text).strip("\n")
    head = text.split("\n", 1)[0].strip()
    if ENCRYPTION_RE.match(head):
        return Parsed(date, None, "service", service_text=text.strip(), system=True)
    if DELETED_RE.match(text.strip()):
        return Parsed(date, sender, "service", service_text="Message deleted")
    # iPhone writes group events as "GroupName: \u200e<event>".
    if (title and sender == title and raw_text.startswith("\u200e") and not _is_placeholder(head)):
        return Parsed(date, None, "service", service_text=text.strip(), system=True)
    edited = False
    stripped = EDITED_RE.sub("", text)
    if stripped != text:
        edited, text = True, stripped
    lines = text.split("\n")
    head = lines[0].replace("\u200e", "").strip()
    rest = "\n".join(lines[1:]).strip("\n")
    if MEDIA_OMITTED_RE.match(head):
        return Parsed(date, sender, "media", rest, media_type="other", edited=edited)
    if (mm := IOS_ATTACHED_RE.match(head)) or (mm := ANDROID_ATTACHED_RE.match(head)):
        name = mm["name"].strip()
        return Parsed(date, sender, "media", rest, media_type=_media_type_for(name), media_name=name, edited=edited)
    if mm := IOS_OMITTED_RE.match(head):
        kind = OMITTED_TYPES.get(mm["what"].lower(), "other")
        name = mm["name"].strip() if mm["name"] and kind == "document" else None
        return Parsed(date, sender, "media", rest, media_type=kind, media_name=name, edited=edited)
    if LOCATION_RE.match(head):
        return Parsed(date, sender, "media", text, media_type="location", edited=edited)
    return Parsed(date, sender, "text", text, edited=edited)


def _is_placeholder(head: str) -> bool:
    return bool(MEDIA_OMITTED_RE.match(head) or IOS_ATTACHED_RE.match(head) or IOS_OMITTED_RE.match(head)
                or DELETED_RE.match(head) or EDITED_RE.search(head))


# --- the import -----------------------------------------------------------------------------

def _zone(tz: str | None) -> dt.tzinfo | None:
    if not tz:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ExportFormatError(f"Unknown time zone '{tz}'. Use an IANA name like Europe/Berlin.") from None


class _MediaCopier:
    def __init__(self, source: Source, chat_id: int):
        self.source = source
        self.root = config.media_dir()
        self.folder = self.root / str(chat_id)
        self.zip = zipfile.ZipFile(source.zip_path) if source.zip_path else None
        self.members: dict[str, zipfile.ZipInfo] = {}
        if self.zip:
            for info in self.zip.infolist():
                pure = PurePosixPath(info.filename)
                if info.is_dir() or pure.is_absolute() or ".." in pure.parts:
                    continue
                self.members.setdefault(pure.name, info)

    def close(self) -> None:
        if self.zip:
            self.zip.close()

    def copy(self, message_id: int, name: str) -> str | None:
        """Copy one attachment by its bare file name; returns the path relative to the media dir."""
        if not name or "/" in name or "\\" in name or name in (".", "..") or "\0" in name:
            return None
        final = self.folder / f"{message_id}-{db.safe_filename(name)}"
        if self.zip:
            info = self.members.get(name)
            if info is None or info.file_size > MAX_MEDIA_BYTES:
                return None
            opener = lambda: self.zip.open(info)  # noqa: E731
        else:
            folder = self.source.folder.resolve()
            try:
                src = (folder / name).resolve()
                if not src.is_relative_to(folder) or not src.is_file():
                    return None
            except (OSError, ValueError):
                return None
            opener = lambda: src.open("rb")  # noqa: E731
        self.folder.mkdir(parents=True, exist_ok=True)
        if not final.exists():
            part = final.with_name(final.name + ".part")
            with opener() as src_f, part.open("wb") as dst:
                shutil.copyfileobj(src_f, dst)
            part.chmod(0o600)
            part.replace(final)
        return str(final.relative_to(self.root))


def _key(sender_id: int | None, p: Parsed) -> tuple:
    return (p.date, sender_id, p.kind, p.text, p.service_text, p.media_type, p.media_name)


def _attach(conn: sqlite3.Connection, copier: _MediaCopier, chat_id: int, mid: int, name: str,
            report: dict[str, Any]) -> None:
    try:
        stored = copier.copy(mid, name)
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        stored = None  # unreadable, encrypted or unsupported compression
    if stored:
        conn.execute("UPDATE messages SET media_path=? WHERE chat_id=? AND message_id=?", (stored, chat_id, mid))
        report["media_copied"] += 1
    else:
        report["media_missing"] += 1


def import_export(path: Path | str, *, me: str | None = None, title: str | None = None, tz: str | None = None,
                  date_order: str | None = None, copy_media: bool = False,
                  db_path: Path | None = None, include_forgotten: bool = False) -> dict[str, Any]:
    """Import one WhatsApp chat export. Returns {"owner_id", "chats": [one report]} like importer.py.

    Holds the sync lock (raises status.LockBusy when another process has it). A chat in
    excluded_chats is skipped unless include_forgotten.
    """
    if date_order not in (None, "dmy", "mdy"):
        raise ExportFormatError("--date-order must be dmy or mdy.")
    zone = _zone(tz)
    source = open_source(path)
    entries = split_entries(source.text)
    if not entries:
        raise ExportFormatError("No WhatsApp messages found. Is this a chat exported with WhatsApp's 'Export chat'?")
    chat_title = _clean_name(title) if title else source.title
    if not chat_title:
        raise ExportFormatError("Could not tell the chat name from the file name. Pass --title \"Chat name\".")
    order, guessed = detect_date_order(entries, date_order)
    me_name = _clean_name(me) if me else None

    report: dict[str, Any] = {"chat_id": chat_id_for(chat_title), "title": chat_title, "kind": "private",
                              "source": "whatsapp", "imported": 0, "already_present": 0, "media_copied": 0,
                              "media_missing": 0, "skipped_pre_upgrade": 0, "skipped_invalid": 0,
                              "date_order": order, "date_order_guessed": guessed}
    parsed: list[Parsed] = []
    for e in entries:
        try:
            parsed.append(interpret(e, order, zone, chat_title))
        except (ValueError, OverflowError):
            report["skipped_invalid"] += 1  # an impossible date or time
    senders = {p.sender for p in parsed if p.sender and not p.system}
    group = len(senders) > 2 or any(p.system and p.service_text and GROUP_HINT_RE.search(p.service_text)
                                    and not ENCRYPTION_RE.match(p.service_text) for p in parsed)
    kind = "group" if group else "private"
    report["kind"] = kind
    chat_id = report["chat_id"]

    with status.holding_sync_lock(BUSY):
        if not include_forgotten and chat_id in config.Settings.load().excluded_chats:
            skipped = {k: report[k] for k in ("chat_id", "title", "kind", "source")}
            return {"owner_id": None, "chats": [{**skipped, "skipped": FORGOTTEN}]}
        _store(report, parsed, senders, kind, chat_title, source, me_name, copy_media, db_path)
    return {"owner_id": sender_id_for(me_name) if me_name else None, "chats": [report]}


def _store(report: dict[str, Any], parsed: list, senders: set, kind: str, chat_title: str, source: Any,
           me_name: str | None, copy_media: bool, db_path: Path | None) -> None:
    chat_id = report["chat_id"]
    conn = db.open_rw(db_path)
    copier = _MediaCopier(source, chat_id) if copy_media else None
    try:
        with conn:
            now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            conn.execute(
                """INSERT INTO chats(chat_id, kind, title, username, first_seen_at, updated_at, raw_json)
                   VALUES(?,?,?,NULL,?,?,?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                     kind = CASE WHEN excluded.kind = 'group' THEN 'group' ELSE chats.kind END,
                     updated_at = excluded.updated_at""",
                (chat_id, kind, chat_title, now, now, json.dumps({"source": "whatsapp"})))
            for name in sorted(senders):
                conn.execute("INSERT OR IGNORE INTO senders(peer_id, name, is_bot) VALUES(?,?,0)",
                             (sender_id_for(name), name))
            # Stored messages by content, oldest id first; a repeat in the file matches the next one.
            existing: dict[tuple, list[tuple[int, str | None]]] = defaultdict(list)
            for r in conn.execute("SELECT date, sender_id, kind, text, service_text, media_type, media_name, "
                                  "message_id, media_path FROM messages WHERE chat_id=? ORDER BY message_id",
                                  (chat_id,)):
                existing[tuple(r)[:7]].append((r[7], r[8]))
            next_id = (conn.execute("SELECT max(message_id) FROM messages WHERE chat_id=?",
                                    (chat_id,)).fetchone()[0] or 0) + 1
            for p in parsed:
                sender_id = sender_id_for(p.sender) if p.sender else None
                key = _key(sender_id, p)
                if existing.get(key):
                    mid, media_path = existing[key].pop(0)
                    report["already_present"] += 1
                    if copier and p.media_name and p.media_type in db.DOWNLOADABLE and not media_path:
                        _attach(conn, copier, chat_id, mid, p.media_name, report)
                    continue
                out = int(bool(me_name and p.sender and p.sender.casefold() == me_name.casefold()))
                raw = {"source": "whatsapp", **({"edited": True} if p.edited else {})}
                try:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO messages(chat_id, message_id, sender_id, out, date, edit_date, kind,
                             text, media_type, media_name, service_text, raw_json)
                           VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?)""",
                        (chat_id, next_id, sender_id, out, p.date, p.kind, p.text, p.media_type, p.media_name,
                         p.service_text, json.dumps(raw)))
                except (sqlite3.InterfaceError, sqlite3.IntegrityError, ValueError):
                    report["skipped_invalid"] += 1
                    continue
                if not cur.rowcount:
                    report["already_present"] += 1
                    continue
                report["imported"] += 1
                if copier and p.media_name and p.media_type in db.DOWNLOADABLE:
                    _attach(conn, copier, chat_id, next_id, p.media_name, report)
                next_id += 1
            if me_name:
                # An earlier import without --me left these unmarked.
                conn.execute("UPDATE messages SET out=1 WHERE chat_id=? AND sender_id=? AND out=0",
                             (chat_id, sender_id_for(me_name)))
            newest = conn.execute("SELECT max(date) FROM messages WHERE chat_id=?", (chat_id,)).fetchone()[0]
            if newest:
                conn.execute("UPDATE chats SET last_message_at=? WHERE chat_id=? "
                             "AND (last_message_at IS NULL OR last_message_at < ?)", (newest, chat_id, newest))
    finally:
        if copier:
            copier.close()
        conn.close()
