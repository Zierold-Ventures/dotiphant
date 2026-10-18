"""The Dotiphant viewer: a small Starlette app on 127.0.0.1 that reads the archive read-only.

It never writes the database: syncs and media downloads run as separate `dotiphant`
processes, with their output in logs/sync.log. The viewer itself writes only:
- settings.json (favorites, which chats AI apps may read, and the archive choices made in /setup),
- secrets through /setup (API id and hash, the Telegram session) and its own viewer_token,
- AI app configs when /setup connects an app (see connect.py),
- the background refresh schedule when /setup turns it on or off (see schedule.py).

Every request needs the per-install viewer token: `dotiphant open` opens /?key=TOKEN once, the
viewer swaps the key for an HttpOnly session cookie that works until the viewer stops, and
scripts can send `Authorization: Bearer TOKEN`.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hmac
import mimetypes
import re
import secrets
import sqlite3
import threading
import webbrowser
import zlib
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import jinja2
from markupsafe import Markup, escape
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__, config, db, query, status as sync_status, viewer

HERE = Path(__file__).resolve().parent
PAGE_SIZE = 200
EXIT_PARTIAL = 3  # `dotiphant sync` finished, but some chats could not be read (cli.EXIT_PARTIAL)
COLORS = 8
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}
AUDIO_EXT = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".opus", ".flac"}
MEDIA_LABELS = {
    "photo": "Photo", "video": "Video", "voice": "Voice message", "audio": "Audio", "document": "File",
    "sticker": "Sticker", "poll": "Poll", "contact": "Contact", "location": "Location", "webpage": "Link preview",
}
INLINE_TYPES = ("image/", "video/", "audio/", "application/pdf")
CSP = (
    "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

TOKEN_SECRET = "viewer_token"
KEY_CHECK_SECONDS = 5.0  # how often a running viewer reads the stored viewer key again
DEMO_NOTE = "Not available in the demo."

_job_lock = threading.Lock()
_job: dict[str, Any] = {}


# --- formatting helpers ------------------------------------------------------------

def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def relative_time(value: str | None, now: dt.datetime | None = None) -> str:
    """'just now', '5 min ago', '3 h ago', 'yesterday', '4 days ago' or a date."""
    when = parse_iso(value)
    if not when:
        return "never"
    now = now or dt.datetime.now(dt.timezone.utc)
    secs = (now - when).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    days = int(secs // 86400)
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    return when.strftime("%-d %b %Y")


def short_when(value: str | None) -> str:
    """Time today, weekday this week, otherwise the date."""
    when = parse_iso(value)
    if not when:
        return ""
    now = dt.datetime.now(dt.timezone.utc)
    if when.date() == now.date():
        return when.strftime("%H:%M")
    if (now - when).days < 7:
        return when.strftime("%a")
    if when.year == now.year:
        return when.strftime("%-d %b")
    return when.strftime("%-d %b %Y")


_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def linkify(text: str | None) -> Markup:
    """Escape text and turn http(s) URLs into safe links."""
    if not text:
        return Markup("")
    out: list[str] = []
    pos = 0
    for m in _URL.finditer(text):
        url = m.group(0)
        trail = ""
        while url and url[-1] in ".,;:!?)]}":
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            trail = url[-1] + trail
            url = url[:-1]
        out.append(str(escape(text[pos:m.start()])))
        out.append(f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{escape(url)}</a>{escape(trail)}')
        pos = m.end()
    out.append(str(escape(text[pos:])))
    return Markup("".join(out))


MARK_OPEN, MARK_CLOSE = "\x02", "\x03"


def highlight_snippet(snippet: str | None) -> Markup:
    """Turn the search marks around matched words into <mark>, escaping everything else."""
    if not snippet:
        return Markup("")
    # Stored text may hold the mark characters too. A real match is a token, so it never
    # contains them: only an innermost open/close pair is a mark, and strays are dropped.
    out: list[str] = []
    for i, part in enumerate(re.split(f"{MARK_OPEN}([^{MARK_OPEN}{MARK_CLOSE}]*){MARK_CLOSE}", snippet)):
        text = str(escape(part.replace(MARK_OPEN, "").replace(MARK_CLOSE, "")))
        out.append(f"<mark>{text}</mark>" if i % 2 and part else text)
    return Markup("".join(out))


def color_class(sender_id: Any, out: int = 0) -> str:
    if out:
        return "c-me"
    return f"c{zlib.crc32(str(sender_id).encode()) % COLORS}"


def short_day(value: str | None) -> str:
    """'30 Sep' this year, '30 Sep 2025' otherwise."""
    when = parse_iso(value)
    if not when:
        return ""
    if when.year == dt.datetime.now(dt.timezone.utc).year:
        return when.strftime("%-d %b")
    return when.strftime("%-d %b %Y")


def human_size(n: int | None) -> str:
    if not n:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


KIND_LABELS = {"private": "Person", "bot": "Bot", "group": "Group", "supergroup": "Group", "channel": "Channel"}
KIND_FILTER = {"private": "people", "bot": "people", "group": "groups", "supergroup": "groups", "channel": "channels"}


# --- brand art -----------------------------------------------------------------------

BRAND = HERE / "static" / "brand"
_SVG_UNSAFE = re.compile(r"<script|<foreignobject|<image|\son[a-z]+\s*=|javascript:|\sstyle\s*=|@import", re.I)
_SVG_STYLE = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
_SVG_RULE = re.compile(r"\.([\w-]+)\s*\{([^{}]*)\}")
_SVG_PROPS = {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
              "opacity", "fill-opacity", "stroke-opacity", "fill-rule"}
_SVG_CLASSED = re.compile(r'<([a-zA-Z][\w:-]*)((?:\s[^>]*?)?)\sclass="([^"]*)"([^>]*?)(/?)>')


def _inline_svg(text: str, prefix: str) -> str | None:
    """An SVG file made fit to sit inside a page under the strict CSP, or None if it can't be.

    The brand files keep their colors in a <style> block of class rules that read CSS variables
    (--lens-ink, --lens-dot and the other --lens-* tokens). Inline <style> is blocked by the CSP,
    so each rule moves onto its elements as presentation attributes, where the variables still
    resolve against the page's theme tokens. Ids get a prefix so two drawings never share a mask.
    """
    text = re.sub(r"<\?xml[^>]*>|<!--.*?-->", "", text, flags=re.S).strip()
    rules: dict[str, dict[str, str]] = {}
    for block in _SVG_STYLE.findall(text):
        for cls, body in _SVG_RULE.findall(block):
            decls = {}
            for decl in body.split(";"):
                if not decl.strip():
                    continue
                prop, _, value = decl.partition(":")
                prop, value = prop.strip().lower(), value.strip()
                if prop not in _SVG_PROPS or '"' in value or "<" in value:
                    return None
                decls[prop] = value
            rules[cls] = decls
        if _SVG_RULE.sub("", block).strip():
            return None  # selectors other than single classes: leave the file to an <img>
    text = _SVG_STYLE.sub("", text)
    if not text.startswith("<svg") or _SVG_UNSAFE.search(text) or "<style" in text:
        return None
    if any(not ref.startswith("#") for ref in re.findall(r'href="([^"]*)"', text)):
        return None  # only references inside the drawing itself

    def apply(m: re.Match) -> str:
        attrs, keep = {}, []
        for cls in m.group(3).split():
            if cls in rules:
                attrs.update(rules[cls])
            else:
                keep.append(cls)  # a hook for the page's own CSS, such as the lens-drift motion
        rest = m.group(2) + m.group(4)
        for prop in attrs:  # the class wins over a plain attribute, as it would in CSS
            rest = re.sub(rf'\s{prop}="[^"]*"', "", rest)
        extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
        if keep:
            extra += f' class="{" ".join(keep)}"'
        return f"<{m.group(1)}{rest}{extra}{m.group(5)}>"

    text = _SVG_CLASSED.sub(apply, text)
    for ident in set(re.findall(r'\sid="([^"]+)"', text)):
        new = f"{prefix}-{ident}"
        text = (text.replace(f'id="{ident}"', f'id="{new}"').replace(f"url(#{ident})", f"url(#{new})")
                .replace(f'href="#{ident}"', f'href="#{new}"'))
    return text


def brand_art(name: str, cls: str = "") -> Markup:
    """The lens mark or the lens drawing from static/brand/, drawn inline so it follows the theme (decorative)."""
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError(name)
    path = BRAND / f"{name}.svg"
    try:
        svg = _inline_svg(path.read_text(encoding="utf-8"), name)
    except OSError:
        svg = None
    if svg is None:
        return Markup(f'<img class="{escape(cls)}" src="/static/brand/{name}.svg" alt="">')
    head = f'<svg class="{escape(cls)}" aria-hidden="true" focusable="false"'
    svg = re.sub(r'\s(role|aria-label|aria-hidden|focusable)="[^"]*"', "", svg, count=0)
    return Markup(head + svg[len("<svg"):])


def initials(title: str | None) -> str:
    """Up to two letters for a chat without a photo: the first letters of its first two words."""
    words = [w for w in re.split(r"[^\w]+", title or "") if w and w[0].isalnum()]
    words = [w for w in words if len(w) > 1] or words  # skip connectors like the "x" in "Acme x Northwind"
    letters = "".join(w[0] for w in words[:2]).upper()
    return letters or "?"


def tone(chat_id: int | None) -> int:
    """A stable tone (1 to 4) per chat, so a chat keeps its colour wherever it is listed."""
    return abs(int(chat_id or 0)) % 4 + 1


def _env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(HERE / "templates"),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(
        linkify=linkify, relative=relative_time, short_when=short_when, short_day=short_day, size=human_size,
        urlpath=lambda p: quote(p or "", safe="/"), initials=initials,
    )
    env.globals.update(
        KIND_LABELS=KIND_LABELS, KIND_FILTER=KIND_FILTER, color_class=color_class, version=__version__, art=brand_art, tone=tone)
    return env


ENV = _env()


def archive_is_empty() -> bool:
    """True before the first sync has stored a chat, so the nav offers no refresh."""
    try:
        with reader() as conn:
            return conn.execute("SELECT 1 FROM chats LIMIT 1").fetchone() is None
    except Exception:
        return True


def render(request: Request, name: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("chat", None)
    ctx.setdefault("demo", is_demo())
    state = getattr(request.scope.get("app"), "state", None)
    ctx.setdefault("csrf_token", getattr(state, "csrf_token", ""))
    if "empty_archive" not in ctx:
        ctx["empty_archive"] = archive_is_empty()
    html = ENV.get_template(name).render(request=request, job=job_status(), demo_note=DEMO_NOTE, **ctx)
    return HTMLResponse(html, status_code=status_code)


@contextlib.contextmanager
def reader():
    """A read-only connection for one request."""
    conn = db.connect(readonly=True)
    try:
        yield conn
    finally:
        conn.close()


# --- small read helpers (only SELECTs; everything else lives in query.py) ------------

def day_counts(conn: sqlite3.Connection, chat_id: int, topic_id: int | None = None) -> list[tuple[str, int]]:
    ids = query.chat_ids_with_predecessor(conn, chat_id)
    sql = f"SELECT substr(date,1,10), count(*) FROM messages WHERE chat_id IN ({','.join('?' * len(ids))})"
    args: list[Any] = list(ids)
    if topic_id is not None:
        sql += " AND topic_id=?"
        args.append(topic_id)
    return [(r[0], r[1]) for r in conn.execute(sql + " GROUP BY 1 ORDER BY 1", args)]


def whatsapp_chat_ids() -> set[int]:
    """Chats imported from a WhatsApp export. They cannot be refreshed from Telegram."""
    with reader() as conn:
        rows = conn.execute(
            """SELECT chat_id FROM chats
               WHERE raw_json LIKE '%"source": "whatsapp"%' OR raw_json LIKE '%"source":"whatsapp"%'"""
        ).fetchall()
    return {r[0] for r in rows}


def missing_media_count(conn: sqlite3.Connection, chat_id: int) -> int:
    ids = query.chat_ids_with_predecessor(conn, chat_id)
    marks = ",".join("?" * len(ids))
    types = ",".join("?" * len(db.DOWNLOADABLE))
    return conn.execute(
        f"SELECT count(*) FROM messages WHERE chat_id IN ({marks}) AND media_type IN ({types}) AND media_path IS NULL",
        (*ids, *db.DOWNLOADABLE),
    ).fetchone()[0]


def has_older(conn: sqlite3.Connection, chat_id: int, first: dict, topic_id: int | None) -> bool:
    ids = query.chat_ids_with_predecessor(conn, chat_id)
    sql = f"SELECT 1 FROM messages m WHERE m.chat_id IN ({','.join('?' * len(ids))}) AND {query.CURSOR_BEFORE}"
    args: list[Any] = [*ids, *query.cursor_args(first)]
    if topic_id is not None:
        sql += " AND m.topic_id=?"
        args.append(topic_id)
    return conn.execute(sql + " LIMIT 1", args).fetchone() is not None


def has_newer(conn: sqlite3.Connection, chat_id: int, last: dict, topic_id: int | None) -> bool:
    ids = query.chat_ids_with_predecessor(conn, chat_id)
    sql = f"SELECT 1 FROM messages m WHERE m.chat_id IN ({','.join('?' * len(ids))}) AND {query.CURSOR_AFTER}"
    args: list[Any] = [*ids, *query.cursor_args(last)]
    if topic_id is not None:
        sql += " AND m.topic_id=?"
        args.append(topic_id)
    return conn.execute(sql + " LIMIT 1", args).fetchone() is not None


# --- message view model -------------------------------------------------------------

def anchor(m: dict) -> str:
    return f"m{m['chat_id']}_{m['message_id']}"


def media_view(m: dict) -> dict | None:
    mtype = m.get("media_type")
    if not mtype:
        return None
    kind = MEDIA_LABELS.get(mtype, mtype.replace("_", " ").capitalize())
    label = f"{kind}: {m['media_name']}" if m.get("media_name") and mtype != "document" else (m.get("media_name") or kind)
    view = {"type": mtype, "label": label, "name": m.get("media_name") or mtype, "size": m.get("media_size")}
    path = m.get("media_path")
    if path and _media_file(path):
        ext = Path(path).suffix.lower()
        view["url"] = "/media/" + quote(path, safe="/")
        if ext in IMAGE_EXT and mtype in ("photo", "sticker", "document"):
            view["show"] = "image"
        elif ext in VIDEO_EXT:
            view["show"] = "video"
        elif ext in AUDIO_EXT or mtype in ("voice", "audio"):
            view["show"] = "audio"
        else:
            view["show"] = "file"
    else:
        view["show"] = "missing" if mtype in db.DOWNLOADABLE else "note"
    return view


def build_items(conn: sqlite3.Connection, chat: dict, messages: list[dict]) -> list[dict]:
    """Day separators, service pills and message cards, ready for the template."""
    on_page = {(m["chat_id"], m["message_id"]): m for m in messages}
    forum = bool(chat.get("is_forum"))
    photos = query.sender_photos(conn, (m["sender_id"] for m in messages))
    items: list[dict] = []
    day = None
    prev = None
    for m in messages:
        when = parse_iso(m["date"])
        d = when.date() if when else None
        if d != day:
            day = d
            prev = None
            items.append({"type": "day", "id": f"d{d}", "label": when.strftime("%A, %-d %B %Y") if when else ""})
        m = dict(m)
        m["anchor"] = anchor(m)
        m["time"] = when.strftime("%H:%M") if when else ""
        if m["kind"] == "service":
            if m.get("reply_to_id"):
                m["reply_href"] = _message_href(chat["chat_id"], m["chat_id"], m["reply_to_id"], on_page)
            items.append({"type": "service", "m": m})
            prev = None
            continue
        rid = m.get("reply_to_id")
        is_reply = bool(rid) and not (forum and rid == m.get("topic_id"))
        if is_reply:
            target = on_page.get((m["chat_id"], rid)) or query.get_message(conn, m["chat_id"], rid)
            m["reply"] = {
                "href": _message_href(chat["chat_id"], m["chat_id"], rid, on_page),
                "sender": target["sender"] if target else None,
                "text": _one_line(target) if target else f"message #{rid}",
            }
        m["media"] = media_view(m)
        m["ref"] = query.message_ref(m)
        m["color"] = color_class(m["sender_id"], m["out"])
        sid = m["sender_id"]
        m["face"] = {"chat_id": sid, "title": m["sender"], "photo_id": photos.get(sid), "avatar_path": sid in photos}
        m["cont"] = bool(
            prev and prev["sender_id"] == m["sender_id"] and prev.get("topic_id") == m.get("topic_id")
            and not is_reply and when and (when - prev["_when"]).total_seconds() < 300
        )
        m["show_topic"] = forum and m.get("topic")
        m["_when"] = when
        items.append({"type": "msg", "m": m})
        prev = m
    return items


def _one_line(m: dict) -> str:
    text = (m.get("text") or "").strip().replace("\n", " ")
    if not text and m.get("media_type"):
        text = query.message_line_text(m)
    if not text and m.get("kind") == "service":
        text = m.get("service_text") or ""
    return text[:160] or "Message"


def _message_href(page_chat: int, chat_id: int, message_id: int, on_page: dict) -> str:
    frag = f"#m{chat_id}_{message_id}"
    if (chat_id, message_id) in on_page:
        return frag
    return f"/chat/{page_chat}?at={message_id}{frag}" if chat_id == page_chat else f"/chat/{chat_id}?at={message_id}{frag}"


# --- request helpers ----------------------------------------------------------------

def same_origin(request: Request) -> bool:
    """POSTs must come from this viewer's own pages."""
    host = request.headers.get("host", "")
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    return bool(host) and urlsplit(source).netloc == host


def forbidden() -> JSONResponse:
    return JSONResponse({"error": "Requests must come from the Dotiphant viewer itself."}, status_code=403)


def chat_id_param(value: Any) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"-?[0-9]{1,20}", text) or not query.fits_int64(int(text)):
        raise ValueError("bad chat id")
    return int(text)


def int64_param(value: str | None) -> int | None:
    """An optional integer query parameter; ValueError when it is not one sqlite can hold."""
    if not value:
        return None
    n = int(value)
    if not query.fits_int64(n):
        raise ValueError("number out of range")
    return n


async def json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def not_found(request: Request, message: str = "Not found.") -> HTMLResponse:
    return render(request, "error.html", status_code=404, message=message, title="Not found")


def _media_file(rel: str) -> Path | None:
    base = config.media_dir().resolve()
    try:
        path = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    try:
        if not path.is_relative_to(base) or not path.is_file():
            return None
    except OSError:  # e.g. a name longer than the file system allows
        return None
    return path


def load_favorites() -> list[int]:
    return list(config.Settings.load().favorites)


def ai_scope(conn: sqlite3.Connection) -> query.AiScope:
    """Which chats AI apps may read, for the markers and switches. The viewer itself shows every chat."""
    return query.ai_scope(conn, config.Settings.load())


def is_demo() -> bool:
    """True for the made-up archive of `dotiphant demo`, where syncs and downloads cannot work."""
    with contextlib.suppress(Exception):
        from .. import demo

        if config.data_dir().resolve() == Path(demo.demo_home()).resolve():
            return True
    try:
        with reader() as conn:
            return db.get_meta(conn, "demo") == "1"
    except sqlite3.Error:
        return False


# --- background jobs ---------------------------------------------------------------

def last_job(status: dict | None = None) -> dict | None:
    """The outcome of the last job this viewer started, judged only by that job's own process.

    A sync child writes status.json with its own pid; a media download does not touch
    status.json, so only its exit code counts. Results of earlier or other runs are ignored.
    """
    proc = _job.get("proc")
    if proc is None:
        return None
    status = sync_status.read_status() if status is None else status
    code = proc.poll()
    out: dict[str, Any] = {
        "id": _job.get("id"), "kind": _job.get("kind"), "chat": _job.get("chat"),
        "started_at": _job.get("started_at"), "pid": _job.get("pid"), "done": code is not None,
        "ok": None, "message": None, "new_messages": None,
    }
    if code is None:
        return out
    own = _job.get("kind") == "sync" and _job.get("pid") is not None and status.get("pid") == _job.get("pid")
    partial = _job.get("kind") == "sync" and code == EXIT_PARTIAL
    out["partial"] = partial  # finished, but some chats could not be read
    if own:
        out["ok"] = status.get("state") != "error" and (code == 0 or partial)
        out["message"] = status.get("message")
        out["new_messages"] = status.get("new_messages")
    else:
        out["ok"] = code == 0 or partial
    if not out["ok"] and not out["message"] and code == 75:  # EXIT_BUSY
        out["message"] = "Another sync was already running. Try again in a minute."
    if not out["ok"] and not out["message"]:
        what = "The download" if _job.get("kind") == "media" else "The refresh"
        out["message"] = f"{what} stopped with an error. See logs/sync.log in the Dotiphant folder."
    return out


def job_status() -> dict:
    """status.json, whether a job is running, and the outcome of the job this viewer started."""
    status = sync_status.read_status()
    proc = _job.get("proc")
    mine = proc is not None and proc.poll() is None
    file_running = sync_status.is_running(status)
    out = {k: status.get(k) for k in (
        "state", "stage", "chat", "chats_done", "chats_total", "new_messages", "started_at", "finished_at", "message",
        "errors")}
    out["running"] = bool(mine or file_running)
    out["job"] = _job.get("kind") if mine else None
    out["job_chat"] = _job.get("chat") if mine else None
    out["last_job"] = last_job(status)
    return out


def start_job(kind: str, args: list[str], chat: int | None = None) -> tuple[bool, str]:
    with _job_lock:
        if job_status()["running"]:
            return False, "A sync or download is already running."
        proc = sync_status.start_background(args)
        _job.clear()
        _job.update(
            proc=proc, kind=kind, chat=chat, id=secrets.token_hex(6), pid=getattr(proc, "pid", None),
            started_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        )
        return True, "Started."


# --- pages -------------------------------------------------------------------------

async def home(request: Request) -> Response:
    from . import onboarding

    if (to_setup := onboarding.redirect_home_to_setup()) is not None:
        return to_setup
    favs = set(load_favorites())
    with reader() as conn:
        ov = query.overview(conn)
        chats = query.list_chats(conn, limit=100_000)
        scope = ai_scope(conn)
    status = sync_status.read_status()
    # Only a run that did not fail counts as a sync; per-chat times come from chats that synced fine.
    finished_ok = status.get("finished_at") if status.get("state") != "error" else None
    last_sync = max(filter(None, [ov.get("last_sync_at"), finished_ok]), default=None)
    sync_error = None if is_demo() else ((status.get("message") or "See logs/sync.log in the Dotiphant folder.") if status.get("state") == "error" else None)
    errors = status.get("errors") if status.get("state") == "idle" and not is_demo() else 0
    sync_warning = (f"The last sync could not read {errors} chat{'' if errors == 1 else 's'}. "
                    "Dotiphant tries again on the next sync.") if isinstance(errors, int) and errors > 0 else None
    imported = whatsapp_chat_ids()
    for c in chats:
        c["fav"] = c["chat_id"] in favs
        c["ai_hidden"] = not scope.allows(c["chat_id"])
        c["filter"] = KIND_FILTER.get(c["kind"], "groups")
        c["source"] = "whatsapp" if c["chat_id"] in imported else "telegram"
    return render(
        request, "home.html", overview=ov, last_sync=last_sync, sync_error=sync_error, sync_warning=sync_warning, chats=chats,
        favorites=[c for c in chats if c["fav"]], title="Dotiphant", empty_archive=not chats,
    )


async def chat_page(request: Request) -> Response:
    try:
        chat_id = chat_id_param(request.path_params["chat_id"])
    except ValueError:
        return not_found(request)
    p = request.query_params
    try:
        topic_id = int64_param(p.get("topic"))
        at = int64_param(p.get("at"))
        before = int64_param(p.get("before"))
        before_chat = int64_param(p.get("before_chat"))
        after = int64_param(p.get("after"))
        after_chat = int64_param(p.get("after_chat"))
    except ValueError:
        return not_found(request, "That link is not valid.")
    since = p.get("since") or None
    partial = p.get("partial") == "1"
    with reader() as conn:
        try:
            info = query.chat_info(conn, chat_id)
        except query.ChatNotFound:
            # AI apps sometimes put a cited message id where the chat id belongs.
            if chat_id > 0 and query.messages_by_id(conn, chat_id, limit=1):
                return RedirectResponse(f"/search?q=%23{chat_id}", status_code=303)
            return not_found(request, "This chat is not in the archive.")
        try:
            if at is not None:
                messages = query.get_messages(conn, chat_id, around_id=at, topic_id=topic_id, limit=PAGE_SIZE)
            elif since or after is not None:
                messages = query.get_messages(conn, chat_id, since=since, after_id=after, after_chat_id=after_chat,
                                              topic_id=topic_id, limit=PAGE_SIZE)
            else:
                messages = query.get_messages(conn, chat_id, before_id=before, before_chat_id=before_chat,
                                              topic_id=topic_id, limit=PAGE_SIZE)
        except ValueError as e:
            return not_found(request, str(e))
        items = build_items(conn, info, messages)
        older = bool(messages) and has_older(conn, chat_id, messages[0], topic_id)
        newer = bool(messages) and has_newer(conn, chat_id, messages[-1], topic_id)
        ctx = dict(
            info=info, items=items, older=older, newer=newer, first=messages[0] if messages else None,
            last=messages[-1] if messages else None, topic_id=topic_id,
        )
        if partial:
            return render(request, "_messages.html", **ctx)
        days = day_counts(conn, chat_id, topic_id)
        missing = missing_media_count(conn, chat_id)
        predecessor = [c for c in query.chat_ids_with_predecessor(conn, chat_id) if c != chat_id]
        info["ai_visible"] = ai_scope(conn).allows(chat_id)
    months: dict[str, list[tuple[str, int]]] = {}
    for d, n in days:
        months.setdefault(d[:7], []).append((d, n))
    rail = [
        {"label": dt.date.fromisoformat(ds[0][0]).strftime("%B %Y"), "count": sum(n for _, n in ds),
         "first": ds[0][0], "days": [{"date": d, "label": dt.date.fromisoformat(d).strftime("%a %-d"), "count": n} for d, n in ds]}
        for _, ds in sorted(months.items())
    ]
    info["fav"] = chat_id in load_favorites()
    return render(
        request, "chat.html", title=info["title"], chat=info, rail=rail, missing_media=missing,
        auto_download=chat_id in config.Settings.load().auto_download_chats,
        latest=at is None and not since and before is None and after is None, at=at, since=since,
        has_predecessor=bool(predecessor), **ctx,
    )


async def chat_export(request: Request) -> Response:
    try:
        chat_id = chat_id_param(request.path_params["chat_id"])
    except ValueError:
        return PlainTextResponse("Not found.", status_code=404)
    since = request.query_params.get("since") or None
    with reader() as conn:
        try:
            info = query.chat_info(conn, chat_id)
            messages = query.get_messages(conn, chat_id, since=since, limit=1_000_000)
        except query.ChatNotFound:
            return PlainTextResponse("Not found.", status_code=404)
        except ValueError as e:
            return PlainTextResponse(str(e), status_code=400)
    note = f"Messages since {query.parse_when(since)[:16].replace('T', ' ')} UTC." if since else None
    text = query.to_markdown(info, messages, note=note)
    headers = {}
    if request.query_params.get("download"):
        safe = re.sub(r"[^\w.-]+", "-", info["title"] or "").strip("-")[:60] or "chat"
        ascii_safe = re.sub(r"[^A-Za-z0-9.-]+", "-", safe).strip("-") or "chat"
        headers["Content-Disposition"] = (f'attachment; filename="{ascii_safe}.md"; '
                                          f"filename*=UTF-8''{quote(safe + '.md', safe='')}")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers=headers)


async def message_link(request: Request) -> Response:
    """/m/dp-3k9xq: the short link AI apps give for a cited message."""
    with reader() as conn:
        m = query.find_ref(conn, request.path_params["ref"])
    if m is None:
        return not_found(request, "No message has this ref. It may be mistyped, or from another archive.")
    return RedirectResponse(_message_href(0, m["chat_id"], m["message_id"], {}), status_code=303)


async def search_page(request: Request) -> Response:
    p = request.query_params
    q = (p.get("q") or "").strip()
    since = p.get("since") or ""
    chat_ref = p.get("chat") or ""
    error = None
    hits: list[dict] = []
    with reader() as conn:
        chats = query.list_chats(conn, limit=100_000)
        chat_id = None
        if chat_ref:
            try:
                chat_id = chat_id_param(chat_ref)
            except ValueError:
                error = "Unknown chat."
        ref = query.find_ref(conn, q) if q.lower().startswith(query.REF_PREFIX) else None
        if ref:
            return RedirectResponse(_message_href(0, ref["chat_id"], ref["message_id"], {}), status_code=303)
        by_id = re.fullmatch(r"#(\d{1,18})", q)
        if by_id and not error:
            # "#40120", the message id an AI app cites: open it directly when only one chat has it.
            hits = query.messages_by_id(conn, int(by_id.group(1)), chat_id=chat_id)
            if len(hits) == 1:
                return RedirectResponse(_message_href(0, hits[0]["chat_id"], hits[0]["message_id"], {}), status_code=303)
        elif q and not error:
            try:
                hits = query.search(conn, q, chat_id=chat_id, since=since or None, limit=200, marks=(MARK_OPEN, MARK_CLOSE))
            except ValueError as e:
                error = str(e)
            except sqlite3.OperationalError:
                error = "That search could not be read. Try plain words."
            if not hits and not error and q.isdigit() and len(q) <= 18:
                hits = query.messages_by_id(conn, int(q), chat_id=chat_id)  # a message id typed without its #
    for h in hits:
        h["snippet_html"] = highlight_snippet(h.get("snippet"))
        h["anchor"] = anchor(h)
        h["color"] = color_class(h["sender_id"], h["out"])
    return render(
        request, "search.html", title=f"Search: {q}" if q else "Search", q=q, since=since, chat_id=chat_id,
        chats=chats, chat_map={c["chat_id"]: c for c in chats}, hits=hits, error=error,
    )


async def connect_page(request: Request) -> Response:
    from .. import connect as connect_mod

    snippets = [{"key": key, "name": client.label, "text": connect_mod.snippet(key), "command": f"dotiphant connect {key}"}
                for key, client in connect_mod.CLIENTS.items()]
    with reader() as conn:
        scope = ai_scope(conn)
        total = conn.execute("SELECT count(*) FROM chats WHERE migrated_to IS NULL").fetchone()[0]
        visible = conn.execute(
            f"SELECT count(*) FROM chats WHERE migrated_to IS NULL AND {scope.sql('chat_id')}").fetchone()[0]
    access = {"mode": config.Settings.load().ai_access, "total": total, "visible": visible}
    return render(request, "connect.html", title="Connect AI", snippets=snippets, access=access)


AVATAR_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _avatar_file(rel: str) -> Path | None:
    """A stored profile photo, only ever a file directly inside media/avatars/."""
    media = config.media_dir()
    try:
        path = (media / rel).resolve()
        if path.parent != (media / "avatars").resolve() or not path.is_file():
            return None
    except (OSError, ValueError):
        return None
    return path if path.suffix.lower() in AVATAR_TYPES else None


async def avatar_file(request: Request) -> Response:
    """A chat's profile photo. With ?v=<photo_id> it may be cached for good: a new photo gets a new link."""
    try:
        chat_id = chat_id_param(request.path_params["chat_id"])
    except ValueError:
        return PlainTextResponse("Not found.", status_code=404)
    with reader() as conn:
        row = conn.execute("SELECT avatar_path, photo_id FROM chats WHERE chat_id=?", (chat_id,)).fetchone() \
            if db.has_avatars(conn) else None
    path = _avatar_file(row["avatar_path"]) if row is not None and row["avatar_path"] else None
    if path is None:
        return PlainTextResponse("Not found.", status_code=404)
    fresh = request.query_params.get("v") == str(row["photo_id"])
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
               "Cache-Control": "private, max-age=31536000, immutable" if fresh else "private, no-cache"}
    return FileResponse(path, media_type=AVATAR_TYPES[path.suffix.lower()], headers=headers)


async def media_file(request: Request) -> Response:
    rel = request.path_params["path"]
    path = _media_file(rel)
    if path is None:
        return PlainTextResponse("Not found.", status_code=404)
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
    if not ctype.startswith(INLINE_TYPES) or ctype == "image/svg+xml":
        # Anything that could run script in our origin (HTML, SVG, ...) is downloaded, never rendered.
        return FileResponse(path, media_type="application/octet-stream", filename=path.name, headers=headers)
    return FileResponse(path, media_type=ctype, headers=headers)


# --- API ---------------------------------------------------------------------------

async def api_status(request: Request) -> Response:
    return JSONResponse(job_status())


async def api_markdown(request: Request) -> Response:
    if not same_origin(request):
        return forbidden()
    body = await json_body(request)
    try:
        chat_id = chat_id_param(body.get("chat_id"))
    except ValueError:
        return JSONResponse({"error": "chat_id is required"}, status_code=400)
    refs = body.get("messages") or [[chat_id, i] for i in body.get("message_ids") or []]
    if not isinstance(refs, list) or len(refs) > 5000:
        return JSONResponse({"error": "messages must be a list"}, status_code=400)
    with reader() as conn:
        try:
            info = query.chat_info(conn, chat_id)
        except query.ChatNotFound:
            return JSONResponse({"error": "unknown chat"}, status_code=404)
        allowed = set(query.chat_ids_with_predecessor(conn, chat_id))
        found = []
        for ref in refs:
            # Ids arrive as strings: WhatsApp chat ids do not fit a JavaScript number.
            try:
                pair = ref if isinstance(ref, (list, tuple)) else (chat_id, ref)
                cid, mid = chat_id_param(pair[0]), chat_id_param(pair[1])
            except (ValueError, IndexError):
                continue
            if cid in allowed and (m := query.get_message(conn, cid, mid)):
                found.append(m)
    found.sort(key=lambda m: (m["date"], m["message_id"]))
    return JSONResponse({"markdown": query.to_markdown(info, found), "count": len(found)})


async def api_favorite(request: Request) -> Response:
    if not same_origin(request):
        return forbidden()
    body = await json_body(request)
    try:
        chat_id = chat_id_param(body.get("chat_id"))
    except ValueError:
        return JSONResponse({"error": "chat_id is required"}, status_code=400)
    def toggle(settings: config.Settings) -> None:
        starred = chat_id not in settings.favorites
        settings.favorites = [c for c in settings.favorites if c != chat_id] + ([chat_id] if starred else [])

    settings = config.update_settings(toggle)
    return JSONResponse({"chat_id": chat_id, "starred": chat_id in settings.favorites})


async def api_ai_access(request: Request) -> Response:
    """Show one chat to AI apps or hide it: {"chat_id": ..., "visible": true|false}."""
    if not same_origin(request):
        return forbidden()
    body = await json_body(request)
    try:
        chat_id = chat_id_param(body.get("chat_id"))
    except ValueError:
        return JSONResponse({"error": "chat_id is required"}, status_code=400)
    if not isinstance(body.get("visible"), bool):
        return JSONResponse({"error": "visible must be true or false"}, status_code=400)
    with reader() as conn:
        if conn.execute("SELECT 1 FROM chats WHERE chat_id=?", (chat_id,)).fetchone() is None:
            return JSONResponse({"error": "unknown chat"}, status_code=404)
        linked = query.linked_chat_ids(conn, [chat_id])
        settings = config.update_settings(lambda s: s.set_ai_visible(linked, body["visible"]))
        visible = query.ai_scope(conn, settings).allows(chat_id)
    return JSONResponse({"chat_id": chat_id, "visible": visible, "mode": settings.ai_access})


async def api_auto_download(request: Request) -> Response:
    """Download a chat's attachments with every sync, or only on request: {"chat_id": ..., "on": true|false}."""
    if not same_origin(request):
        return forbidden()
    body = await json_body(request)
    try:
        chat_id = chat_id_param(body.get("chat_id"))
    except ValueError:
        return JSONResponse({"error": "chat_id is required"}, status_code=400)
    if not isinstance(body.get("on"), bool):
        return JSONResponse({"error": "on must be true or false"}, status_code=400)
    with reader() as conn:
        if conn.execute("SELECT 1 FROM chats WHERE chat_id=?", (chat_id,)).fetchone() is None:
            return JSONResponse({"error": "unknown chat"}, status_code=404)
    def change(settings: config.Settings) -> None:
        chats = [c for c in settings.auto_download_chats if c != chat_id]
        settings.auto_download_chats = chats + [chat_id] if body["on"] else chats

    config.update_settings(change)
    return JSONResponse({"chat_id": chat_id, "on": body["on"]})


async def api_ai_mode(request: Request) -> Response:
    """Which chats AI apps may read: {"mode": "all"} or {"mode": "selected"}."""
    if not same_origin(request):
        return forbidden()
    body = await json_body(request)
    mode = body.get("mode")
    if mode not in config.AI_ACCESS:
        return JSONResponse({"error": "mode must be all or selected"}, status_code=400)
    config.update_settings(lambda s: setattr(s, "ai_access", mode))
    return JSONResponse({"mode": mode})


def demo_refusal() -> JSONResponse:
    return JSONResponse({"started": False, "message": DEMO_NOTE, "demo": True}, status_code=409)


def job_response(started: bool, text: str) -> JSONResponse:
    """The job this request started (id, pid, started_at), so the page reports only that job's result."""
    status = job_status()
    body = {"started": started, "message": text, "job": status["last_job"] if started else None, "status": status}
    return JSONResponse(body, status_code=200 if started else 409)


async def api_refresh(request: Request) -> Response:
    if not same_origin(request):
        return forbidden()
    if is_demo():
        return demo_refusal()
    body = await json_body(request)
    raw = body.get("chat_ids") or []
    try:
        ids = [chat_id_param(c) for c in (raw if isinstance(raw, list) else [raw])]
    except ValueError:
        return JSONResponse({"error": "chat ids must be numbers"}, status_code=400)
    started, text = start_job("sync", ["sync", "--quiet", *map(str, ids)], ids[0] if len(ids) == 1 else None)
    return job_response(started, text)


async def api_shutdown(request: Request) -> Response:
    """Stop this viewer, so `dotiphant open` can start an updated one. Only the viewer key may ask.

    ViewerAuth lets this path through only with `Authorization: Bearer KEY`; the Origin must match too.
    """
    if not same_origin(request):
        return forbidden()
    stop = getattr(request.app.state, "shutdown", None)
    if stop is None:
        return JSONResponse({"error": "This viewer cannot be stopped from here."}, status_code=409)
    stop()
    return JSONResponse({"stopping": True})


async def api_media(request: Request) -> Response:
    if not same_origin(request):
        return forbidden()
    if is_demo():
        return demo_refusal()
    body = await json_body(request)
    try:
        chat_id = chat_id_param(body.get("chat_id"))
    except ValueError:
        return JSONResponse({"error": "chat_id is required"}, status_code=400)
    message_id = body.get("message_id")
    args = ["media", str(chat_id)]
    if message_id is not None:
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            return JSONResponse({"error": "message_id must be a number"}, status_code=400)
        if message_id <= 0:
            return JSONResponse({"error": "message_id must be a number"}, status_code=400)
        args.append(str(message_id))  # just this file, even above the 50 MB limit of a whole-chat download
    started, text = start_job("media", args, chat_id)
    return job_response(started, text)


# --- app ---------------------------------------------------------------------------

SECURITY_HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
    "cache-control": "no-store",
}
SERVER_ERROR = "Dotiphant hit an error. Reload the page; if it keeps happening, the details are in logs/ in the Dotiphant folder."
STALE = ("Dotiphant was updated while this viewer was running. "
         "Stop it (Ctrl+C) and run `dotiphant open` again.")
SHUTDOWN_PATH = "/api/shutdown"


def stale_response(path: str) -> Response:
    """Shown instead of any page once the installed files no longer match the code this process runs.

    A standalone page: the templates on disk may expect context the old code does not provide.
    """
    if path.startswith("/api/"):
        return JSONResponse({"error": STALE, "stale": True}, status_code=503)
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Dotiphant was updated</title><link rel="stylesheet" href="/static/app.css"></head>'
        '<body><main class="er" role="alert"><h1>Dotiphant was updated while this viewer was running.</h1>'
        '<p>Stop it (Ctrl+C) and run <code>dotiphant open</code> again.</p></main></body></html>'
    )
    return HTMLResponse(html, status_code=503)


async def http_error(request: Request, exc: Exception) -> Response:
    """Unknown pages get the viewer's own error page; /api/ gets JSON."""
    code = getattr(exc, "status_code", 404)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not found." if code == 404 else "Request not allowed."}, status_code=code)
    if code == 404:
        return render(request, "error.html", status_code=404, title="Not found", message="Not found.")
    return PlainTextResponse(getattr(exc, "detail", "") or "Request not allowed.", status_code=code)


async def settings_error(request: Request, exc: Exception) -> Response:
    """settings.json could not be read (SettingsUnreadable) or another process kept it busy."""
    return JSONResponse({"error": str(exc)}, status_code=409)


async def server_error(request: Request, exc: Exception) -> Response:
    """A crash still shows a calm page. This runs outside the other middleware, so it adds the headers itself."""
    if viewer.is_stale():
        response = stale_response(request.url.path)
    elif request.url.path.startswith("/api/"):
        response: Response = JSONResponse({"error": SERVER_ERROR}, status_code=500)
    else:
        try:
            response = render(request, "error.html", status_code=500, title="Something went wrong", message=SERVER_ERROR)
        except Exception:
            response = PlainTextResponse(SERVER_ERROR, status_code=500)
    response.headers.update(SECURITY_HEADERS)
    response.headers.update(getattr(getattr(request.app, "state", None), "identity", None) or {})
    return response


class SecurityHeaders:
    """Adds a strict CSP and friends to every response, and the viewer's identity headers (see viewer.py)."""

    def __init__(self, app, identity: dict[str, str] | None = None):
        self.app = app
        self.extra = {k.lower().encode(): v.encode() for k, v in {**SECURITY_HEADERS, **(identity or {})}.items()}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                for k, v in self.extra.items():
                    if k not in names:
                        headers.append((k, v))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class StaleGuard:
    """Once a reinstall changed the files on disk, every page explains that this viewer must restart.

    Static files and the shutdown request still work, so `dotiphant open` can replace this viewer.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or path.startswith("/static/") or path == SHUTDOWN_PATH
                or not viewer.is_stale()):
            return await self.app(scope, receive, send)
        await stale_response(path)(scope, receive, send)


def viewer_token() -> str:
    """The per-install viewer secret, created on first use and kept with the other secrets."""
    token = config.get_secret(TOKEN_SECRET)
    if not token:
        token = secrets.token_urlsafe(32)
        config.set_secret(TOKEN_SECRET, token)
    return token


def cookie_name(scope) -> str:
    # Browsers share cookies across ports, so a demo viewer next to the real one needs its own name.
    server = scope.get("server") or (None, None)
    return f"dotiphant_viewer_{server[1] or 0}"


# Shown to a browser without the viewer key: a standalone page that loads only static files.
LOCKED_PAGE = ENV.get_template("locked.html").render()


CSRF_HEADER = "x-dotiphant-token"


class ViewerAuth:
    """Requires the viewer token on every request except static assets.

    `?key=TOKEN` is accepted once: it sets an HttpOnly, SameSite=Strict session cookie and redirects
    to the same URL without the key. Scripts can send `Authorization: Bearer TOKEN` instead.

    The cookie holds a random session value made for this run, never the viewer key itself. It is
    accepted only as a cookie, never as a Bearer token, and stops working when the viewer stops.

    `stored` returns the viewer key as stored now (read again every few seconds). Once it is
    missing or differs from the key this viewer started with, every request is refused and the
    viewer stops itself, so `dotiphant logout --forget-credentials` ends it on any port.

    Browsers send cookies for 127.0.0.1 to every port, so the cookie alone is not enough for
    /api/ and POST requests: those also need the per-run page token from the page's
    <meta name="dotiphant-token">, sent by app.js as the X-Dotiphant-Token header.
    """

    def __init__(self, app, token: str, csrf_token: str = "", session: str = "",
                 stored: Callable[[], str | None] | None = None):
        self.app = app
        self.token = token
        self.csrf_token = csrf_token
        self.session = session or secrets.token_urlsafe(32)
        self.stored = stored

    def _key_current(self) -> bool:
        return self.stored is None or self._ok(self.stored())

    @staticmethod
    def _same(value: str | None, expected: str) -> bool:
        return bool(value) and bool(expected) and hmac.compare_digest(value.encode(), expected.encode())

    def _ok(self, value: str | None) -> bool:
        return self._same(value, self.token)

    def _session_ok(self, value: str | None) -> bool:
        return self._same(value, self.session)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/static/"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        if not self._key_current():
            stop = getattr(getattr(scope.get("app"), "state", None), "shutdown", None)
            if stop is not None:
                stop()  # the key was removed or replaced: this viewer can serve no one any more
            return await self._deny(request, scope, receive, send)
        name = cookie_name(scope)
        pairs = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        keys = [v for k, v in pairs if k == "key"]
        if keys:
            if len(keys) != 1 or not self._ok(keys[0]):
                return await self._deny(request, scope, receive, send)
            rest = urlencode([(k, v) for k, v in pairs if k != "key"])
            # One leading slash only, so the redirect can never point at another host ("//evil").
            path = "/" + quote(scope["path"], safe="/").lstrip("/")
            response = RedirectResponse(path + (f"?{rest}" if rest else ""), status_code=303)
            response.set_cookie(name, self.session, httponly=True, samesite="strict", path="/")
            return await response(scope, receive, send)
        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else None
        if self._ok(bearer):
            return await self.app(scope, receive, send)
        if scope["path"] == SHUTDOWN_PATH:  # only the viewer key itself, never the browser cookie
            if self._session_ok(request.cookies.get(name)):
                response = JSONResponse({"error": "Only `dotiphant` itself can stop the viewer."}, status_code=403)
                return await response(scope, receive, send)
            return await self._deny(request, scope, receive, send)
        if self._session_ok(request.cookies.get(name)):
            needs_page_token = scope["path"].startswith("/api/") or scope["method"] not in ("GET", "HEAD")
            supplied = request.headers.get(CSRF_HEADER, "")
            if needs_page_token and not (self.csrf_token and hmac.compare_digest(
                    supplied.encode(), self.csrf_token.encode())):
                response = JSONResponse({"error": "Requests must come from the Dotiphant viewer itself. "
                                                  "Reload the page."}, status_code=403)
                return await response(scope, receive, send)
            return await self.app(scope, receive, send)
        return await self._deny(request, scope, receive, send)

    async def _deny(self, request, scope, receive, send):
        if scope["path"].startswith("/api/"):
            response: Response = JSONResponse(
                {"error": "Open Dotiphant with `dotiphant open`. This viewer needs its private link."}, status_code=401)
        else:
            response = HTMLResponse(LOCKED_PAGE, status_code=401)
        await response(scope, receive, send)


def create_app(token: str | None = None) -> Starlette:
    csrf_token = secrets.token_urlsafe(24)  # per run; pages carry it, /api/ and POSTs must echo it
    session = secrets.token_urlsafe(32)  # per run; the browser cookie, never the viewer key
    routes = [
        Route("/", home),
        Route("/chat/{chat_id}", chat_page),
        Route("/chat/{chat_id}/export.md", chat_export),
        Route("/search", search_page),
        Route("/m/{ref}", message_link),
        Route("/connect", connect_page),
        Route("/media/{path:path}", media_file),
        Route("/avatar/{chat_id}", avatar_file),
        Route("/api/status", api_status),
        Route("/api/markdown", api_markdown, methods=["POST"]),
        Route("/api/favorite", api_favorite, methods=["POST"]),
        Route("/api/ai-access", api_ai_access, methods=["POST"]),
        Route("/api/ai-mode", api_ai_mode, methods=["POST"]),
        Route("/api/refresh", api_refresh, methods=["POST"]),
        Route("/api/media", api_media, methods=["POST"]),
        Route("/api/auto-download", api_auto_download, methods=["POST"]),
        Route(SHUTDOWN_PATH, api_shutdown, methods=["POST"]),
        Mount("/static", StaticFiles(directory=HERE / "static"), name="static"),
    ]
    from . import onboarding

    routes[-1:-1] = onboarding.routes()
    token = token or viewer_token()
    identity = viewer.identity_headers(config.data_dir(), token)
    middleware = [
        # Blocks DNS rebinding: only requests addressed to the loopback host are served.
        Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]),
        Middleware(SecurityHeaders, identity=identity),
        Middleware(StaleGuard),
        Middleware(ViewerAuth, token=token, csrf_token=csrf_token, session=session,
                   stored=config.StoredSecret(TOKEN_SECRET, ttl=KEY_CHECK_SECONDS)),
    ]
    app = Starlette(
        routes=routes, middleware=middleware,
        exception_handlers={404: http_error, 405: http_error, 500: server_error,
                            config.SettingsUnreadable: settings_error, config.SettingsBusy: settings_error},
    )
    app.state.csrf_token = csrf_token
    app.state.session = session
    app.state.identity = identity
    return app


def serve(port: int = 8765, open_browser: bool = True, path: str = "/") -> None:
    """Run the viewer on 127.0.0.1 and open its private link in the browser.

    The link carries the viewer token once. Without a browser, the full link is printed so
    the user can open it; otherwise only the plain address is shown. `path` is the page to
    open, like /setup.
    """
    import uvicorn

    token = viewer_token()
    base = f"http://127.0.0.1:{port}/{path.lstrip('/')}"
    link = f"{base}?key={token}"

    def launch() -> None:
        if not webbrowser.open(link):
            print(f"Could not open a browser. Open this private link: {link}", flush=True)

    if open_browser:
        threading.Timer(0.8, launch).start()
        print(f"Dotiphant viewer at {base} (Ctrl+C to stop)", flush=True)
    else:
        print(f"Dotiphant viewer at {link}", flush=True)
        print("This link is private: it unlocks your archive. Ctrl+C to stop.", flush=True)
    app = create_app(token)
    # No access log: the first request carries the key in its URL.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    app.state.shutdown = lambda: setattr(server, "should_exit", True)
    server.run()
