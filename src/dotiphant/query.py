"""Read-side queries shared by the CLI, the web viewer and the MCP server.

Every function takes an open connection and returns plain dicts, so callers can render
them as HTML, Markdown or JSON.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .config import Settings

from .db import DOWNLOADABLE, has_avatars, has_stats

MESSAGE_COLUMNS = """
m.row_id, m.chat_id, m.message_id, m.topic_id, m.sender_id, m.out, m.date, m.edit_date, m.reply_to_id,
m.forwarded_from, m.kind, m.text, m.media_type, m.media_name, m.media_size, m.media_path,
m.service_text, coalesce(s.name, CASE WHEN m.out THEN 'Me' ELSE 'Unknown' END) AS sender,
s.username AS sender_username, t.title AS topic
"""
MESSAGE_JOINS = """
LEFT JOIN senders s ON s.peer_id = m.sender_id
LEFT JOIN topics t ON t.chat_id = m.chat_id AND t.topic_id = m.topic_id
"""


SEARCH_SORT_THRESHOLD = 5000
# media filter values: "files" means things a person shares on purpose, "any" every attachment.
FILE_MEDIA = tuple(t for t in DOWNLOADABLE if t != "sticker")
MEDIA_ALIASES = {"link": "webpage", "links": "webpage", "file": "files", "photos": "photo", "videos": "video",
                 "documents": "document", "doc": "document", "docs": "document"}


class ChatNotFound(LookupError):
    def __init__(self, ref: str, candidates: list[dict] | None = None):
        self.ref = ref
        self.candidates = candidates or []
        if self.candidates:
            names = ", ".join(f"{c['title']} ({c['chat_id']})" for c in self.candidates[:8])
            super().__init__(f"'{ref}' matches several chats: {names}. Use the chat id.")
        else:
            super().__init__(f"No archived chat matches '{ref}'.")


INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


def fits_int64(n: int) -> bool:
    """True when n can be bound to sqlite as an INTEGER."""
    return INT64_MIN <= n <= INT64_MAX


def like_contains(text: str) -> str:
    """A LIKE pattern matching `text` literally anywhere; use with ESCAPE '\\'."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# --- time helpers ------------------------------------------------------------------

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_when(value: str | None) -> str | None:
    """Turn '24h', '7d', '2w', '2026-09-01' or an ISO timestamp into an ISO UTC string."""
    if not value:
        return None
    value = value.strip()
    if m := re.fullmatch(r"(\d+)\s*([mhdw])", value.lower()):
        n, unit = int(m[1]), m[2]
        try:
            delta = {"m": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n),
                     "d": dt.timedelta(days=n), "w": dt.timedelta(weeks=n)}[unit]
            return (dt.datetime.now(dt.timezone.utc) - delta).replace(microsecond=0).isoformat()
        except (OverflowError, ValueError):
            raise ValueError(f"The time '{value}' is too far back. Use a smaller number or a date.") from None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Cannot read the time '{value}'. Use 24h, 7d, 2026-09-01 or an ISO timestamp.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    try:
        return parsed.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    except (OverflowError, ValueError):
        raise ValueError(f"The time '{value}' is outside the supported calendar.") from None


# --- chats -------------------------------------------------------------------------

# --- AI access -----------------------------------------------------------------------

@dataclass(frozen=True)
class AiScope:
    """The chats AI apps may read. With `only` set, just those; in any case never `hidden`.

    Build it with ai_scope(). The MCP server passes it to every query it runs; the viewer
    and the CLI pass none, because they are the owner's own tools.
    """

    only: frozenset[int] | None = None
    hidden: frozenset[int] = frozenset()

    def allows(self, chat_id: int) -> bool:
        if chat_id in self.hidden:
            return False
        return self.only is None or chat_id in self.only

    def sql(self, column: str) -> str:
        """An SQL condition that keeps rows whose `column` is an allowed chat id.

        The ids are integers from ai_scope, so they go into the SQL text directly: a long
        list would pass SQLite's limit on bound values.
        """
        parts = []
        if self.only is not None:
            parts.append(f"{column} IN ({_int_list(self.only)})" if self.only else "0")
        if self.hidden:
            parts.append(f"{column} NOT IN ({_int_list(self.hidden)})")
        return "(" + " AND ".join(parts) + ")" if parts else "1"


def _int_list(ids: Iterable[int]) -> str:
    return ",".join(str(int(i)) for i in sorted(ids))


def linked_chat_ids(conn: sqlite3.Connection, chat_ids: Iterable[int]) -> list[int]:
    """The given chats plus the basic groups they were upgraded from and the supergroups they
    became. They read as one chat, so they are shown to AI apps or hidden together.
    The given ids come first, in order."""
    out = list(dict.fromkeys(int(c) for c in chat_ids))
    seen = set(out)
    pairs = conn.execute("SELECT chat_id, migrated_to FROM chats WHERE migrated_to IS NOT NULL").fetchall()
    grew = True
    while grew:
        grew = False
        for old, new in pairs:
            if (old in seen) != (new in seen):
                for cid in (old, new):
                    if cid not in seen:
                        seen.add(cid)
                        out.append(cid)
                grew = True
    return out


def ai_scope(conn: sqlite3.Connection, settings: "Settings") -> AiScope:
    """Which chats AI apps may read under these settings. Hidden always wins over allowed."""
    hidden = frozenset(linked_chat_ids(conn, settings.ai_hidden_chats))
    if settings.ai_access == "all":
        return AiScope(hidden=hidden)
    allowed = frozenset(linked_chat_ids(conn, settings.ai_allowed_chats)) - hidden
    return AiScope(only=allowed, hidden=hidden)


def _chat_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    raw = d.pop("raw_json", None)
    d["source"] = "whatsapp" if raw and '"source": "whatsapp"' in raw.replace('":"', '": "') else "telegram"
    return d


def _without_upgraded(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Drop basic groups whose successor supergroup is also a candidate: they read as one chat."""
    ids = {r["chat_id"] for r in rows}
    return [r for r in rows if r["migrated_to"] not in ids]


def resolve_chat(conn: sqlite3.Connection, ref: str | int, scope: AiScope | None = None) -> dict[str, Any]:
    """Find a chat by id, @username or title (exact, then partial match).

    With a scope, only chats it allows are found or offered as candidates. A chat it does
    not allow is treated exactly like a chat that does not exist, so an AI app cannot learn
    from the answer whether a hidden chat with that id, username or title exists.
    """
    text = str(ref).strip()

    def visible(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
        return [r for r in rows if scope is None or scope.allows(r["chat_id"])]

    if re.fullmatch(r"-?\d+", text):
        if not fits_int64(int(text)):
            raise ChatNotFound(text)
        row = conn.execute("SELECT * FROM chats WHERE chat_id=?", (int(text),)).fetchone()
        if row and visible([row]):
            return _chat_dict(row)
        raise ChatNotFound(text)
    if text.startswith("@"):
        row = conn.execute("SELECT * FROM chats WHERE lower(username)=lower(?)", (text[1:],)).fetchone()
        if row and visible([row]):
            return _chat_dict(row)
    rows = visible(_without_upgraded(conn.execute("SELECT * FROM chats WHERE lower(title)=lower(?)", (text,)).fetchall()))
    if len(rows) == 1:
        return _chat_dict(rows[0])
    if not rows:
        rows = visible(_without_upgraded(conn.execute(
            "SELECT * FROM chats WHERE title LIKE ? ESCAPE '\\' ORDER BY last_message_at DESC", (like_contains(text),)
        ).fetchall()))
    if len(rows) == 1:
        return _chat_dict(rows[0])
    raise ChatNotFound(text, [_chat_dict(r) for r in rows])


def list_chats(
    conn: sqlite3.Connection,
    query: str | None = None,
    kind: str | None = None,
    active_since: str | None = None,
    limit: int = 50,
    offset: int = 0,
    scope: AiScope | None = None,
) -> list[dict[str, Any]]:
    """Chats newest-activity first, with message counts. A scope leaves out chats hidden from AI apps."""
    where, args = ["c.migrated_to IS NULL"], []
    if scope is not None:
        where.append(scope.sql("c.chat_id"))
    if query:
        where.append("(c.title LIKE ? ESCAPE '\\' OR c.username LIKE ? ESCAPE '\\')")
        args += [like_contains(query)] * 2
    if kind:
        kinds = {"group": ("group", "supergroup"), "groups": ("group", "supergroup")}.get(kind, (kind,))
        where.append(f"c.kind IN ({','.join('?' * len(kinds))})")
        args += list(kinds)
    if active_since:
        where.append("c.last_message_at >= ?")
        args.append(parse_when(active_since))
    if has_stats(conn):
        count = """coalesce((SELECT s.messages FROM chat_stats s WHERE s.chat_id = c.chat_id), 0)
                   + coalesce((SELECT sum(s.messages) FROM chats p JOIN chat_stats s ON s.chat_id = p.chat_id
                               WHERE p.migrated_to = c.chat_id), 0)"""
    else:  # an archive not yet upgraded by a sync: count the slow way
        count = """(SELECT count(*) FROM messages m WHERE m.chat_id IN (c.chat_id,
                        coalesce((SELECT p.chat_id FROM chats p WHERE p.migrated_to = c.chat_id), 0)))"""
    # Profile photos are for the viewer; an archive not yet upgraded by a sync has none.
    photo = "c.avatar_path, c.photo_id" if has_avatars(conn) else "NULL AS avatar_path, NULL AS photo_id"
    rows = conn.execute(
        f"""SELECT c.chat_id, c.kind, c.title, c.username, c.is_forum, c.archived, c.member_count,
                   c.last_message_at, c.unread_count, {count} AS message_count, {photo}
            FROM chats c WHERE {' AND '.join(where)}
            ORDER BY c.last_message_at IS NULL, c.last_message_at DESC LIMIT ? OFFSET ?""",
        (*args, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def similar_chats(conn: sqlite3.Connection, ref: str, limit: int = 5, scope: AiScope | None = None) -> list[dict[str, Any]]:
    """Chats whose title or username looks like `ref`, for "did you mean" on a misspelled name.

    A scope leaves out chats hidden from AI apps."""
    import difflib

    ref_l = str(ref).strip().lstrip("@").lower()
    words = re.findall(r"\w+", ref_l)
    if not ref_l or not words:
        return []
    def ratio(a: str, b: str, exact: bool) -> float:
        sm = difflib.SequenceMatcher(None, a, b)
        return sm.ratio() if exact else sm.quick_ratio()

    def score(name: str, exact: bool) -> float:
        name_words = re.findall(r"\w+", name) or [name]
        whole = ratio(ref_l, name, exact)
        per_word = sum(max(ratio(w, nw, exact) for nw in name_words) for w in words) / len(words)
        return max(whole, per_word)

    scored = []
    where = "migrated_to IS NULL" + (f" AND {scope.sql('chat_id')}" if scope is not None else "")
    for row in conn.execute(f"SELECT chat_id, kind, title, username, last_message_at FROM chats WHERE {where}"):
        names = [n.lower() for n in (row["title"], row["username"]) if n]
        # quick_ratio is an upper bound of ratio: skip the exact score when even that is too low.
        best = max((score(n, True) for n in names if score(n, False) >= 0.7), default=0.0)
        if best >= 0.7:
            scored.append((best, dict(row)))
    scored.sort(key=lambda x: (-x[0], x[1]["last_message_at"] is None, x[1]["last_message_at"] or ""))
    return [d for _, d in scored[:limit]]


def chat_ids_with_predecessor(conn: sqlite3.Connection, chat_id: int) -> list[int]:
    """A supergroup plus the basic group it was upgraded from, so history reads as one."""
    ids = [chat_id]
    ids += [r[0] for r in conn.execute("SELECT chat_id FROM chats WHERE migrated_to=?", (chat_id,))]
    return ids


def chat_info(conn: sqlite3.Connection, chat_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        raise ChatNotFound(str(chat_id))
    info = _chat_dict(row)
    ids = chat_ids_with_predecessor(conn, chat_id)
    marks = ",".join("?" * len(ids))
    if has_stats(conn):
        # Counters kept by triggers, and first/last dates from the (chat_id, date) index.
        counts = conn.execute(
            f"SELECT sum(messages), sum(media) FROM chat_stats WHERE chat_id IN ({marks})", ids).fetchone()
        firsts = [conn.execute("SELECT min(date) FROM messages WHERE chat_id=?", (i,)).fetchone()[0] for i in ids]
        lasts = [conn.execute("SELECT max(date) FROM messages WHERE chat_id=?", (i,)).fetchone()[0] for i in ids]
        info.update(messages=counts[0] or 0, first_message_at=min(filter(None, firsts), default=None),
                    last_message_at=max(filter(None, lasts), default=None),
                    media=counts[1] if counts[0] else None)
        top_sql = f"""SELECT coalesce(s.name, 'Unknown') AS name, s.username, nullif(cs.sender_key, 0) AS sender_id,
                             sum(cs.messages) AS messages, max(cs.last_message_at) AS last_message_at
                      FROM chat_senders cs LEFT JOIN senders s ON s.peer_id = nullif(cs.sender_key, 0)
                      WHERE cs.chat_id IN ({marks}) AND cs.messages > 0
                      GROUP BY cs.sender_key ORDER BY messages DESC LIMIT 15"""
    else:
        stats = conn.execute(
            f"SELECT count(*) AS messages, min(date) AS first_message_at, max(date) AS last_message_at,"
            f" sum(media_type IS NOT NULL) AS media FROM messages WHERE chat_id IN ({marks})",
            ids,
        ).fetchone()
        info.update(dict(stats))
        top_sql = f"""SELECT coalesce(s.name, 'Unknown') AS name, s.username, m.sender_id, count(*) AS messages,
                             max(m.date) AS last_message_at
                      FROM messages m LEFT JOIN senders s ON s.peer_id = m.sender_id
                      WHERE m.chat_id IN ({marks}) AND m.kind != 'service'
                      GROUP BY m.sender_id ORDER BY messages DESC LIMIT 15"""
    info["topics"] = [dict(r) for r in conn.execute(
        "SELECT topic_id, title FROM topics WHERE chat_id=? ORDER BY topic_id", (chat_id,))]
    info["top_senders"] = [dict(r) for r in conn.execute(top_sql, ids)]
    sync = conn.execute("SELECT * FROM sync_state WHERE chat_id=?", (chat_id,)).fetchone()
    info["sync"] = dict(sync) if sync else None
    return info


# --- messages ----------------------------------------------------------------------

# Paging cursors on (date, message_id), the sort order of every message list. Each takes
# the arguments of cursor_args(): the cursor message's date twice, then its id.
CURSOR_BEFORE = "(m.date < ? OR (m.date = ? AND m.message_id < ?))"
CURSOR_UP_TO = "(m.date < ? OR (m.date = ? AND m.message_id <= ?))"
CURSOR_AFTER = "(m.date > ? OR (m.date = ? AND m.message_id > ?))"


def cursor_args(m: Any) -> tuple[str, str, int]:
    """The bound values of a CURSOR_* condition, from a row or dict with date and message_id."""
    return m["date"], m["date"], m["message_id"]

def _message_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _cursor_message(conn: sqlite3.Connection, chat_id: int, ids: list[int], message_id: int,
                    in_chat: int | None) -> sqlite3.Row | None:
    """Date and id of a paging cursor message: in `in_chat` if given, else preferably in chat_id."""
    chats = [in_chat] if in_chat in ids else ids
    return conn.execute(
        "SELECT date, message_id FROM messages WHERE chat_id IN (%s) AND message_id=? ORDER BY chat_id = ? DESC"
        % ",".join("?" * len(chats)),
        (*chats, message_id, chat_id),
    ).fetchone()


def get_messages(
    conn: sqlite3.Connection,
    chat_id: int,
    since: str | None = None,
    until: str | None = None,
    around_id: int | None = None,
    before_id: int | None = None,
    topic_id: int | None = None,
    limit: int = 100,
    media: str | None = None,
    before_chat_id: int | None = None,
    after_id: int | None = None,
    after_chat_id: int | None = None,
) -> list[dict[str, Any]]:
    """Messages of one chat in chronological order, sorted by (date, message_id).

    Without since/around/before/after this returns the newest `limit` messages. `media`
    keeps only messages with an attachment: "files", "any" or one media type (see
    media_filter). around_id, before_id and after_id are cursors on (date, message_id), so
    messages that share a timestamp are neither skipped nor repeated across pages.
    after_id returns the first `limit` messages after that one. A group upgraded to a
    supergroup can reuse a message id in its earlier basic group: before_chat_id and
    after_chat_id say which chat the cursor message is in (default: this chat, if it has
    that id).
    """
    ids = chat_ids_with_predecessor(conn, chat_id)
    where = [f"m.chat_id IN ({','.join('?' * len(ids))})"]
    args: list[Any] = list(ids)
    if topic_id is not None:
        where.append("m.topic_id = ?")
        args.append(topic_id)
    if media:
        clause, extra = media_filter(media)
        where.append(clause)
        args += extra
    if until:
        where.append("m.date <= ?")
        args.append(parse_when(until))
    base = f"SELECT {MESSAGE_COLUMNS} FROM messages m {MESSAGE_JOINS} WHERE "

    if around_id is not None:
        anchor = conn.execute(
            "SELECT date, message_id FROM messages WHERE chat_id IN (%s) AND message_id=? ORDER BY chat_id = ? DESC"
            % ",".join("?" * len(ids)),
            (*ids, around_id, chat_id),
        ).fetchone()
        if anchor:
            half = max(limit // 2, 1)
            older = conn.execute(
                base + " AND ".join(where + [CURSOR_UP_TO]) + " ORDER BY m.date DESC, m.message_id DESC LIMIT ?",
                (*args, *cursor_args(anchor), half + 1),
            ).fetchall()
            newer = conn.execute(
                base + " AND ".join(where + [CURSOR_AFTER]) + " ORDER BY m.date, m.message_id LIMIT ?",
                (*args, *cursor_args(anchor), half),
            ).fetchall()
            return _message_dicts(list(reversed(older)) + list(newer))
    if since or after_id is not None:
        if since:
            where.append("m.date >= ?")
            args.append(parse_when(since))
        if after_id is not None and (anchor := _cursor_message(conn, chat_id, ids, after_id, after_chat_id)):
            where.append(CURSOR_AFTER)
            args += cursor_args(anchor)
        rows = conn.execute(
            base + " AND ".join(where) + " ORDER BY m.date, m.message_id LIMIT ?", (*args, limit)
        ).fetchall()
        return _message_dicts(rows)
    if before_id is not None:
        if anchor := _cursor_message(conn, chat_id, ids, before_id, before_chat_id):
            where.append(CURSOR_BEFORE)
            args += cursor_args(anchor)
    rows = conn.execute(
        base + " AND ".join(where) + " ORDER BY m.date DESC, m.message_id DESC LIMIT ?", (*args, limit)
    ).fetchall()
    return _message_dicts(list(reversed(rows)))


def media_filter(media: str) -> tuple[str, list[str]]:
    """SQL condition on m.media_type for "files", "any" or one media type like "photo"."""
    value = MEDIA_ALIASES.get(media.strip().lower(), media.strip().lower())
    if value == "any":
        return "m.media_type IS NOT NULL", []
    if value == "files":
        return f"m.media_type IN ({','.join('?' * len(FILE_MEDIA))})", list(FILE_MEDIA)
    return "m.media_type = ?", [value]


def get_message(conn: sqlite3.Connection, chat_id: int, message_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {MESSAGE_COLUMNS} FROM messages m {MESSAGE_JOINS} WHERE m.chat_id=? AND m.message_id=?",
        (chat_id, message_id),
    ).fetchone()
    return dict(row) if row else None


def fts_query(text: str, prefix_all: bool = False) -> str:
    """Turn free text into a safe FTS5 query: every word must match, the last as a prefix.

    Quoted phrases stay phrases. Words joined by OR stay alternatives. With prefix_all,
    every word of 3+ letters is a prefix and a plural "s" is dropped, so "audits" also
    finds "audit" and "audited".
    """
    parts: list[str] = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', text):
        if phrase:
            parts.append('"' + phrase.replace('"', "") + '"')
        elif word.upper() == "OR" and parts:
            parts.append("OR")
        else:
            cleaned = re.sub(r"[^\w@#$.\-]", "", word, flags=re.UNICODE).strip(".-")
            if cleaned and prefix_all and len(cleaned) >= 3:
                if len(cleaned) > 4 and cleaned[-1] in "sS" and cleaned[-2] not in "sSuU":
                    cleaned = cleaned[:-1]
                parts.append('"' + cleaned + '"*')
            elif cleaned:
                parts.append('"' + cleaned + '"')
    if not parts:
        return ""
    if parts[-1].startswith('"') and parts[-1].endswith('"') and " " not in parts[-1]:
        parts[-1] = parts[-1] + "*"
    while parts and parts[-1] == "OR":
        parts.pop()
    return " ".join(parts)


def search(
    conn: sqlite3.Connection,
    query: str,
    chat_id: int | None = None,
    sender: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 20,
    offset: int = 0,
    marks: tuple[str, str] = ("[", "]"),
    prefix_all: bool = False,
    media: str | None = None,
    scope: AiScope | None = None,
) -> list[dict[str, Any]]:
    """Full-text search, newest first. Each hit carries the chat title and a snippet.

    Matched words in the snippet are wrapped in `marks`: [ ] by default, which reads well
    for people and AI; the viewer passes control characters so it can highlight exactly.
    prefix_all and media work as in fts_query and get_messages. A scope keeps only hits in
    chats AI apps may read.
    """
    match = fts_query(query, prefix_all=prefix_all)
    if not match:
        return []
    where: list[str] = []
    args: list[Any] = []
    if scope is not None:
        where.append(scope.sql("m.chat_id"))
    if chat_id is not None:
        ids = chat_ids_with_predecessor(conn, chat_id)
        where.append(f"m.chat_id IN ({','.join('?' * len(ids))})")
        args += ids
    if sender:
        where.append("(s.name LIKE ? OR s.username LIKE ?)")
        args += [f"%{sender.lstrip('@')}%", f"%{sender.lstrip('@')}%"]
    if since:
        where.append("m.date >= ?")
        args.append(parse_when(since))
    if until:
        where.append("m.date <= ?")
        args.append(parse_when(until))
    if media:
        clause, extra = media_filter(media)
        where.append(clause)
        args += extra

    # Rare words: read the few matches and sort them. Common words: walk messages newest
    # first and stop at `limit` matches, instead of sorting hundreds of thousands of hits.
    matches = conn.execute(
        "SELECT count(*) FROM (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? LIMIT ?)",
        (match, SEARCH_SORT_THRESHOLD + 1),
    ).fetchone()[0]
    if not matches:
        return []
    filters = "".join(f" AND {w}" for w in where)
    sender_join = "LEFT JOIN senders s ON s.peer_id = m.sender_id" if sender else ""
    if matches <= SEARCH_SORT_THRESHOLD:
        sql = f"""SELECT m.row_id FROM messages_fts JOIN messages m ON m.row_id = messages_fts.rowid
                  {sender_join} WHERE messages_fts MATCH ?{filters}
                  ORDER BY m.date DESC, m.row_id DESC LIMIT ? OFFSET ?"""
    else:
        # The unary + keeps SQLite from looking up every match by id and sorting them all;
        # it walks the date index instead and stops after `limit` hits.
        sql = f"""SELECT m.row_id FROM messages m {sender_join}
                  WHERE +m.row_id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?){filters}
                  ORDER BY m.date DESC LIMIT ? OFFSET ?"""
    row_ids = [r[0] for r in conn.execute(sql, (match, *args, limit, offset))]
    if not row_ids:
        return []
    marks_sql = ",".join("?" * len(row_ids))
    snippets = _snippets(conn, match, row_ids, marks)
    rows = conn.execute(
        f"""SELECT {MESSAGE_COLUMNS}, c.title AS chat_title FROM messages m {MESSAGE_JOINS}
            JOIN chats c ON c.chat_id = m.chat_id WHERE m.row_id IN ({marks_sql})""",
        row_ids,
    ).fetchall()
    by_id = {r["row_id"]: dict(r) for r in rows}
    hits = []
    for rid in row_ids:
        hit = by_id[rid]
        hit["snippet"] = snippets.get(rid, hit["text"][:200])
        hits.append(hit)
    return hits


SNIPPET_RANGES = 6


def _snippets(conn: sqlite3.Connection, match: str, row_ids: list[int], marks: tuple[str, str]) -> dict[int, str]:
    """Snippets for the given hits.

    FTS5 sets up a MATCH again for every rowid in an IN list, which costs about 10 ms
    each for a prefix query. A rowid range sets it up once and walks only that part of
    the match list, so the hits are covered with a few ranges, split at the widest gaps.
    """
    ids = sorted(set(row_ids))
    gaps = sorted(range(1, len(ids)), key=lambda i: ids[i] - ids[i - 1], reverse=True)[:SNIPPET_RANGES - 1]
    bounds = [0, *sorted(gaps), len(ids)]
    out: dict[int, str] = {}
    for lo, hi in zip(bounds, bounds[1:]):
        chunk = ids[lo:hi]
        marks_sql = ",".join("?" * len(chunk))
        # The unary + leaves the IN list to SQLite, so FTS5 only sees the rowid range.
        out.update(conn.execute(
            f"SELECT rowid, snippet(messages_fts, 0, ?, ?, ' ... ', 24) FROM messages_fts"
            f" WHERE messages_fts MATCH ? AND rowid BETWEEN ? AND ? AND +rowid IN ({marks_sql})",
            (*marks, match, chunk[0], chunk[-1], *chunk),
        ).fetchall())
    return out


def recent_activity(
    conn: sqlite3.Connection,
    since: str = "24h",
    chat_ids: list[int] | None = None,
    max_chats: int = 30,
    per_chat: int = 5,
    kind: str | None = None,
    scope: AiScope | None = None,
) -> list[dict[str, Any]]:
    """What happened since a point in time: busiest chats first, with their latest messages.

    kind keeps one kind of chat: private, group (basic groups and supergroups), channel or bot.
    A scope leaves out chats hidden from AI apps.
    """
    since_iso = parse_when(since)
    where, args = ["m.date >= ?", "m.kind != 'service'"], [since_iso]
    if scope is not None:
        where.append(scope.sql("m.chat_id"))
    if chat_ids:
        where.append(f"m.chat_id IN ({','.join('?' * len(chat_ids))})")
        args += chat_ids
    if kind:
        kinds = ("group", "supergroup") if kind in ("group", "groups") else (kind,)
        where.append(f"coalesce(t.kind, c.kind) IN ({','.join('?' * len(kinds))})")
        args += list(kinds)
    # Messages of an upgraded basic group count toward its successor supergroup.
    chats = conn.execute(
        f"""SELECT coalesce(t.chat_id, c.chat_id) AS chat_id, coalesce(t.title, c.title) AS title,
                   coalesce(t.kind, c.kind) AS kind, count(*) AS new_messages, max(m.date) AS last_message_at,
                   sum(m.out) AS sent_by_me
            FROM messages m JOIN chats c ON c.chat_id = m.chat_id
            LEFT JOIN chats t ON t.chat_id = c.migrated_to
            WHERE {' AND '.join(where)} GROUP BY coalesce(t.chat_id, c.chat_id)
            ORDER BY last_message_at DESC LIMIT ?""",
        (*args, max_chats),
    ).fetchall()
    out = []
    for chat in chats:
        entry = dict(chat)
        entry["latest"] = get_messages(conn, chat["chat_id"], since=None, limit=per_chat)
        entry["latest"] = [m for m in entry["latest"] if m["date"] >= since_iso]
        out.append(entry)
    return out


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    count = "SELECT coalesce(sum(messages), 0) FROM chat_stats" if has_stats(conn) else "SELECT count(*) FROM messages"
    row = conn.execute(
        "SELECT (SELECT count(*) FROM chats WHERE migrated_to IS NULL) AS chats,"
        f" ({count}) AS messages,"
        " (SELECT min(date) FROM messages) AS first_message_at,"
        " (SELECT max(date) FROM messages) AS last_message_at,"
        " (SELECT max(last_sync_at) FROM sync_state) AS last_sync_at"
    ).fetchone()
    return dict(row)


# --- Markdown ----------------------------------------------------------------------

def _short_time(iso: str) -> str:
    return iso[:16].replace("T", " ")


def message_line_text(m: dict[str, Any]) -> str:
    """The readable body of a message, including media and service notes."""
    if m["kind"] == "service":
        return f"_{m.get('service_text') or 'service message'}_"
    body = m["text"] or ""
    if m.get("media_type"):
        label = m["media_type"]
        if m.get("media_name"):
            label += f": {m['media_name']}"
        body = (body + "\n" if body else "") + f"[{label}]"
    return body


def to_markdown(
    chat: dict[str, Any],
    messages: list[dict[str, Any]],
    note: str | None = None,
) -> str:
    """A transcript an AI assistant (or a person) can read: one heading per message."""
    lines = [f"# {chat['title']}", ""]
    if messages:
        first, last = _short_time(messages[0]["date"])[:10], _short_time(messages[-1]["date"])[:10]
        span = first if first == last else f"{first} to {last}"
        app = "WhatsApp" if chat.get("source") == "whatsapp" else "Telegram"
        kind = {"private": "private chat", "bot": "bot chat", "supergroup": "group"}.get(chat.get("kind"), chat.get("kind") or "chat")
        lines.append(f"{app} {kind}. {len(messages)} messages, {span}, times in UTC.")
    else:
        lines.append("No messages in this range.")
    if note:
        lines.append(note)
    lines.append("")
    # Keyed by chat too: an upgraded group and its supergroup reuse the same message ids.
    by_id = {(m.get("chat_id"), m["message_id"]): m for m in messages}
    for m in messages:
        head = f"## {_short_time(m['date'])}, {m['sender']} (#{m['message_id']}{', ' + message_ref(m) if m.get('row_id') else ''})"
        if m.get("topic"):
            head += f" in {m['topic']}"
        lines.append(head)
        if m.get("forwarded_from"):
            lines.append(f"Forwarded from {m['forwarded_from']}")
        if m.get("reply_to_id"):
            target = by_id.get((m.get("chat_id"), m["reply_to_id"]))
            if target:
                quoted = (target["text"] or message_line_text(target)).splitlines()[0][:120] if (target["text"] or target.get("media_type")) else ""
                lines.append(f"> Reply to {target['sender']}: {quoted}")
            else:
                lines.append(f"> Reply to #{m['reply_to_id']}")
        lines.append(message_line_text(m))
        if m.get("edit_date"):
            lines.append(f"_(edited {_short_time(m['edit_date'])})_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def sender_photos(conn: sqlite3.Connection, sender_ids: Iterable[int]) -> dict[int, int]:
    """sender_id -> photo_id for senders whose own chat has a stored profile photo."""
    ids = sorted({s for s in sender_ids if s})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT chat_id, photo_id FROM chats WHERE chat_id IN ({marks}) AND avatar_path IS NOT NULL", ids
    ).fetchall()
    return {r["chat_id"]: r["photo_id"] for r in rows}


def messages_by_id(conn: sqlite3.Connection, message_id: int, chat_id: int | None = None,
                   limit: int = 50) -> list[dict[str, Any]]:
    """Messages with this id, newest first, shaped like search hits.

    Ids are per chat in groups and channels, so "#40120" can name a message in several chats.
    """
    where, args = "m.message_id = ?", [message_id]
    if chat_id is not None:
        where, args = where + " AND m.chat_id = ?", [*args, chat_id]
    rows = conn.execute(
        f"""SELECT {MESSAGE_COLUMNS}, c.title AS chat_title FROM messages m {MESSAGE_JOINS}
            JOIN chats c ON c.chat_id = m.chat_id WHERE {where} ORDER BY m.date DESC LIMIT ?""",
        (*args, limit),
    ).fetchall()
    hits = [dict(r) for r in rows]
    for h in hits:
        h["snippet"] = (h["text"] or h["service_text"] or message_line_text(h) or "")[:200]
    return hits


# --- message refs -------------------------------------------------------------------
# A ref like "dp-3k9xq" names one message across the whole archive, so an AI app can cite
# it and the user can open it. It is the message's row id in base 36 plus one check
# character from its chat and message id: a typo, or a row id reused after a chat was
# forgotten, then finds nothing instead of the wrong message.

REF_PREFIX = "dp-"
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
REF_RE = re.compile(r"(?:dp-)?([0-9a-z]{2,14})", re.I)


def _ref_check(chat_id: int, message_id: int) -> str:
    return _B36[(chat_id * 31 + message_id) % 36]


def message_ref(m: dict[str, Any]) -> str | None:
    """The short ref of a message dict that has row_id, chat_id and message_id."""
    row_id = m.get("row_id")
    if row_id is None:
        return None
    n, digits = int(row_id), ""
    while True:
        n, r = divmod(n, 36)
        digits = _B36[r] + digits
        if not n:
            break
    return REF_PREFIX + digits + _ref_check(m["chat_id"], m["message_id"])


def find_ref(conn: sqlite3.Connection, text: str) -> dict[str, Any] | None:
    """The message a ref names, or None. Accepts it with or without the dp- prefix."""
    match = REF_RE.fullmatch((text or "").strip())
    if not match:
        return None
    body = match.group(1).lower()
    row_id = int(body[:-1], 36)
    row = conn.execute(
        f"SELECT {MESSAGE_COLUMNS}, c.title AS chat_title FROM messages m {MESSAGE_JOINS} "
        "JOIN chats c ON c.chat_id = m.chat_id WHERE m.row_id = ?", (row_id,)).fetchone()
    if row is None or _ref_check(row["chat_id"], row["message_id"]) != body[-1]:
        return None
    return dict(row)
