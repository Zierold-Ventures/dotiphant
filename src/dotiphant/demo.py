"""A sample archive with fictional chats, so people can try Dotiphant without a Telegram login.

`dotiphant demo` points DOTIPHANT_HOME at a separate directory (see demo_home), seeds it with
seed_demo and opens the viewer. The real archive is never touched.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import struct
import sys
import zlib
from pathlib import Path

from . import config, db

ME = 1000

PEOPLE = {
    1001: ("Maya Chen", "mayachen"),
    1002: ("Tomas Okafor", "tokafor"),
    1003: ("Priya Raman", "priya_r"),
    1004: ("Jonas Weber", "jweber"),
    1005: ("Lena Fischer", None),
    1006: ("Sam Adeyemi", "samadeyemi"),
    1007: ("Ines Moreau", "ines_m"),
    1008: ("Kenji Sato", None),
    1009: ("Northwind Bot", "northwind_bot"),
}

ACME = -1001000000001
HELIOS = -1001000000002
KESTREL = -1001000000003
LUMEN_OLD = -4000000004
LUMEN = -1001000000004
MAYA = 1001
TOMAS = 1002
PRIYA = 1003
CHANNEL = -1001000000005

CHATS = [
    # chat_id, kind, title, username, is_forum, members, migrated_to
    (ACME, "supergroup", "Acme x Northwind integration", None, 0, 9, None),
    (HELIOS, "supergroup", "Helios Labs <> Orbit Ventures", None, 0, 6, None),
    (KESTREL, "supergroup", "Kestrel Protocol Builders", "kestrel_builders", 1, 214, None),
    (LUMEN_OLD, "group", "Lumen Wallet partners", None, 0, 5, LUMEN),
    (LUMEN, "supergroup", "Lumen Wallet partners", None, 0, 7, None),
    (MAYA, "private", "Maya Chen", "mayachen", 0, None, None),
    (TOMAS, "private", "Tomas Okafor", "tokafor", 0, None, None),
    (PRIYA, "private", "Priya Raman", "priya_r", 0, None, None),
    (CHANNEL, "channel", "Northwind Updates", "northwind_updates", 0, 1840, None),
]

TOPICS = {KESTREL: {1: "General", 2: "Integrations", 3: "Grants", 4: "Announcements"}}


# A script line is (sender, text) or (sender, text, extra). `extra` can hold:
#   photo: name of a generated image (see IMAGES)    file: (media_type, name, size)
#   reply: how many script lines back the message replies to    fwd: forwarded-from name
# The last TAIL[chat] lines form the most recent, uninterrupted stretch of the chat, so the
# newest messages (what the viewer opens on) read as a real conversation.
SCRIPTS: dict[int, list[tuple]] = {
    ACME: [
        (1001, "Kicking off the integration thread. Goal: Northwind settlement API live in Acme checkout by the end of the month. Scoping doc to follow."),
        (1004, "Northwind settlement API v2 is now generally available.", {"fwd": "Northwind Updates"}),
        (ME, "Thanks Maya. Main open point on our side is webhook retries. How long do you keep retrying a failed delivery?"),
        (1004, "Exponential backoff for up to 24 hours. After that the event goes to a dead-letter queue you can replay from the dashboard."),
        (1001, "24h is fine. Can we get idempotency keys on every webhook?"),
        (1004, "Yes, already in the v2 payload. Docs: https://docs.example.com/northwind/webhooks", {"reply": 1}),
        (ME, "Scoping doc v1. Comments welcome, especially on section 3 (refunds).",
         {"file": ("document", "Acme-Northwind-scoping.pdf", 284_113)}),
        (1005, "Sandbox keys are in the partner portal. Please don't paste credentials in this chat."),
        (ME, "Got them, thanks. First test transactions go out today."),
        (1004, "Heads-up: refunds older than 90 days need a manual review on our side. That's a card network rule, not ours."),
        (1001, "Noted. Finance is fine with that, we rarely refund after 30 days."),
        (1001, "Staging test passed for EUR and USD. GBP fails with a currency mismatch:\n\nsettlement_currency=GBP, account_currency=EUR"),
        (1004, "Known issue, the fix ships Thursday.", {"reply": 1}),
        (ME, "Can we do a short call on Friday to walk through the launch checklist?"),
        (1001, "Friday 15:00 UTC works for me."),
        (1005, "Same. I'll send the invite."),
        (1001, "Marketing wants a joint announcement for launch day. Any objections?"),
        (ME, "None, as long as legal signs off on the wording."),
        (1004, "GBP fix is deployed to staging. Settlement reports now also include the FX rate for each transaction."),
        (1001, "Staging after the fix: EUR, USD and GBP all green, p95 latency around 180 ms.",
         {"photo": "staging-dashboard.png"}),
        (ME, "Great work. That closes the last technical blocker on our side."),
        (1005, "Security questionnaire is done. Two open items: retention period for webhook payloads, and the pen test date.",
         {"file": ("document", "Security-questionnaire.xlsx", 51_220)}),
        (ME, "Retention is 90 days for webhook payloads and 7 years for settlement records.\nThe pen test is booked for the 14th, with the report a week later.", {"reply": 1}),
        (1005, "Perfect, I'll mark both as resolved."),
        (1004, "Production rate limits: 50 requests per second per merchant, burst 200. Tell us if you need more."),
        (1001, "Well above what we need."),
        (ME, "Proposal: launch on the 28th, with the 4th as fallback if the security review slips."),
        (1001, "Works for us. Locking the 28th."),
        (1005, "Agreed. I'll update the launch checklist and share it before Friday's call."),
    ],
    HELIOS: [
        (1006, "Hi all, adding Ines from our research team."),
        (1007, "Hello! Happy to help with the token design questions."),
        (ME, "Welcome Ines. We'd like a second opinion on the vesting schedule."),
        (1007, "Send me the current draft and I'll comment by Wednesday."),
        (1006, "Also: we're at the Lisbon conference next week. Anyone around?"),
        (ME, "I'll be there Tuesday to Thursday. Coffee on Wednesday?"),
        (1006, "Wednesday morning works."),
        (1007, "", {"file": ("voice", None, 18_000)}),
        (1007, "Draft reviewed. Main concern: the cliff is too short for the team allocation. Investors will ask about it."),
        (ME, "Fair point. 12 months instead of 6?"),
        (1007, "12 months is the norm, yes."),
        (1006, "Term sheet v3 is attached, changes highlighted.", {"file": ("document", "Term-sheet-v3.docx", 40_960)}),
        (ME, "Thanks, reviewing tonight."),
        (1006, "Any update on the term sheet?"),
        (ME, "Two small comments, otherwise good to sign:\n1. Pro-rata rights for the next round\n2. Information rights quarterly, not monthly"),
        (1006, "Both fine. I'll send the final version for signature tomorrow."),
    ],
    KESTREL: [
        (1008, "Welcome to the builders group. Please use topics so things stay findable."),
        (1003, "Is there a testnet faucet yet?"),
        (1008, "Yes: https://faucet.kestrel.example/ (one request per hour)"),
        (1002, "Grant round 3 opens next Monday."),
        (ME, "What's the max grant size this round?"),
        (1002, "Up to 50k, paid out per milestone."),
        (1003, "The SDK throws on empty memo fields. Bug or feature?"),
        (1008, "Bug, fixed in 0.9.2.", {"reply": 1}),
        (1008, "Reference architecture for payment integrations, as discussed.", {"photo": "architecture.png"}),
        (1002, "Reminder: grant applications close Friday."),
        (ME, "Submitted ours. Fingers crossed."),
        (1008, "Mainnet upgrade scheduled for the 15th, 14:00 UTC."),
        (1003, "Will the RPC endpoints change?"),
        (1008, "No, same URLs.", {"reply": 1}),
        (1004, "Integration guide for wallets is up on the docs site."),
        (1008, "Poll: which day for the community call? Tuesday / Thursday", {"file": ("poll", None, None)}),
        (1002, "Grant results go out next week."),
    ],
    LUMEN_OLD: [
        (1005, "Starting a small group for the Lumen partnership."),
        (ME, "Thanks Lena. Adding Kenji from our side."),
        (1008, "Hi everyone."),
        (1005, "First question: do you support hardware wallets at launch?"),
        (ME, "Two hardware wallets at launch, more in Q2."),
    ],
    LUMEN: [
        (1005, "Upgraded the group so we can add more people."),
        (1008, "Mobile build with the Lumen connector is ready for testing."),
        (ME, "Testing now on iOS."),
        (ME, "Works well. One crash when switching networks quickly.", {"photo": "ios-crash.png"}),
        (1008, "Reproduced, fix incoming.", {"reply": 1}),
        (1008, "Screen recording of the fix.", {"file": ("video", "repro.mp4", 4_200_000)}),
        (1005, "Can we co-host a Twitter Space at launch?"),
        (ME, "Yes, let's aim for launch week."),
        (1008, "Crash fix is in build 412."),
        (ME, "Confirmed fixed on iOS 18 and 19."),
    ],
    MAYA: [
        (1001, "Hey, quick one: is the 28th really realistic?"),
        (ME, "If the security review closes on time, yes."),
        (1001, "Ok. Internally I'll say the 28th with a fallback of the 4th."),
        (ME, "Sounds good. Lunch next week?"),
        (1001, "Tuesday?"),
        (ME, "Tuesday works."),
        (1001, "Sent you the deck from the board meeting.", {"file": ("document", "Board-deck-Q3.pdf", 2_310_000)}),
        (ME, "Thanks, very helpful. Slide 12 on merchant churn is a great argument for the integration."),
    ],
    TOMAS: [
        (1002, "Saw your grant application, looks strong."),
        (ME, "Thanks! Anything we should clarify?"),
        (1002, "Add a bit more detail on milestone 2. Reviewers like a clear test plan."),
        (ME, "Will do, updating today."),
        (1002, "Also an intro to Priya might be useful, she built the indexer."),
        (ME, "Please do."),
        (1002, "", {"file": ("sticker", None, 22_000)}),
    ],
    PRIYA: [
        (1003, "Hi, Tomas mentioned you're building on Kestrel."),
        (ME, "Yes, a payments integration. Your indexer would save us weeks."),
        (1003, "Happy to help. It's open source: https://git.example.org/priya/kestrel-indexer"),
        (ME, "Amazing, will take a look."),
        (1003, "Ping me if the schema is unclear. The settlement tables are the tricky part."),
    ],
    CHANNEL: [
        (1009, "Northwind settlement API v2 is now generally available."),
        (1009, "Scheduled maintenance on Sunday, 02:00 to 03:00 UTC."),
        (1009, "New: GBP and CHF settlement in sandbox."),
        (1009, "Our Q3 partner report is out: https://northwind.example.com/q3"),
        (1009, "We are hiring solutions engineers in Berlin and Lisbon."),
    ],
}

TAIL = {ACME: 11, HELIOS: 3, KESTREL: 3, LUMEN: 4, MAYA: 2, TOMAS: 2, PRIYA: 2}
# How many hours before now each chat's latest stretch ends.
LAST_ACTIVE = {ACME: 0.5, PRIYA: 2, LUMEN: 5, HELIOS: 26, KESTREL: 30, MAYA: 52, TOMAS: 98}

FILLER = [
    "Sounds good.", "Agreed.", "Thanks!", "On it.", "Let me check and come back to you.",
    "Can you share the link again?", "Done.", "Makes sense.", "Good catch.", "Will follow up tomorrow.",
    "Any blockers?", "Looks good to me.", "Noted.", "Perfect, thanks.", "+1",
    "I'll loop in the team.", "Let's discuss on the call.", "Updated the doc.",
    "Sharing the updated timeline in a minute.", "Can we move this to Thursday? Today is packed.",
    "Just checked with the team, no objections.", "I'll write up notes from the call and post them here.",
    "Is there a staging URL I can share with QA?", "Looping in our finance lead for the invoicing part.",
    "Quick update: we're on track for this week.", "Thanks for the fast turnaround.",
    "Can someone confirm the time zone for the call?", "Comments are in the doc.",
    "We need sign-off from legal before we can share this externally.",
    "Following up on this one. Any news?",
]

TOPIC_FOR = {
    "faucet": 2, "SDK": 2, "RPC": 2, "Integration": 2, "architecture": 2, "Grant": 3, "grant": 3,
    "Mainnet": 4, "Poll": 1,
}


# --- tiny raster images for the sample photos --------------------------------------

class _Canvas:
    """An RGB pixel buffer with just enough drawing to sketch a screenshot."""

    def __init__(self, width: int, height: int, color: str):
        self.w, self.h = width, height
        self.px = bytearray(_rgb(color) * (width * height))

    def rect(self, x: int, y: int, w: int, h: int, color: str, radius: int = 0) -> None:
        c = _rgb(color)
        for yy in range(max(y, 0), min(y + h, self.h)):
            inset = 0
            if radius:
                dy = max(y + radius - yy - 1, yy - (y + h - radius), 0)
                if dy:
                    inset = radius - math.isqrt(max(radius * radius - dy * dy, 0))
            x0, x1 = max(x + inset, 0), min(x + w - inset, self.w)
            if x1 > x0:
                i = (yy * self.w + x0) * 3
                self.px[i:i + (x1 - x0) * 3] = c * (x1 - x0)

    def card(self, x: int, y: int, w: int, h: int, fill: str = "#ffffff", border: str = "#dfe3e8", radius: int = 8) -> None:
        self.rect(x, y, w, h, border, radius)
        self.rect(x + 1, y + 1, w - 2, h - 2, fill, max(radius - 1, 0))

    def png(self) -> bytes:
        stride = self.w * 3
        raw = b"".join(b"\x00" + bytes(self.px[r * stride:(r + 1) * stride]) for r in range(self.h))

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _rgb(color: str) -> bytes:
    return bytes.fromhex(color.lstrip("#"))


def _dashboard() -> bytes:
    """A settlement dashboard: three green status cards and a volume chart."""
    c = _Canvas(640, 400, "#f4f6f9")
    c.rect(0, 0, 640, 44, "#1e2a3b")
    c.rect(20, 16, 90, 12, "#e2e8f0", 3)
    for i, w in enumerate((44, 52, 40)):
        c.rect(150 + i * 70, 18, w, 8, "#64748b", 3)
    c.rect(588, 12, 32, 20, "#334155", 10)
    for i in range(3):
        x = 20 + i * 205
        c.card(x, 62, 190, 92)
        c.rect(x + 16, 80, 70, 8, "#94a3b8", 3)
        c.rect(x + 16, 102, 110, 18, "#1e293b", 4)
        c.rect(x + 16, 132, 56, 12, "#dcfce7", 6)
        c.rect(x + 22, 136, 44, 4, "#16a34a", 2)
    c.card(20, 170, 600, 212)
    c.rect(36, 186, 120, 9, "#94a3b8", 3)
    heights = (60, 84, 72, 98, 90, 120, 108, 132, 126, 140, 150, 138, 160, 148)
    for i, h in enumerate(heights):
        x = 44 + i * 41
        c.rect(x, 360 - h, 26, h, "#2257d6" if i < len(heights) - 3 else "#16a34a", 3)
    c.rect(36, 361, 568, 1, "#cbd5e1")
    return c.png()


def _phone_crash() -> bytes:
    """A phone screenshot with an error dialog."""
    c = _Canvas(300, 560, "#0f172a")
    c.rect(110, 14, 80, 18, "#000000", 9)
    c.rect(20, 60, 140, 14, "#e2e8f0", 4)
    c.card(20, 92, 260, 120, "#1e293b", "#334155", 14)
    c.rect(40, 116, 90, 10, "#94a3b8", 4)
    c.rect(40, 140, 170, 22, "#f8fafc", 5)
    c.rect(40, 178, 60, 18, "#16a34a", 9)
    for i in range(4):
        c.card(20, 228 + i * 62, 260, 52, "#1e293b", "#334155", 10)
        c.rect(36, 246 + i * 62, 120, 8, "#cbd5e1", 3)
        c.rect(36, 262 + i * 62, 70, 6, "#64748b", 3)
    c.card(34, 200, 232, 150, "#ffffff", "#e2e8f0", 14)
    c.rect(130, 218, 40, 40, "#fee2e2", 20)
    c.rect(146, 226, 8, 16, "#dc2626", 3)
    c.rect(146, 246, 8, 6, "#dc2626", 3)
    c.rect(80, 272, 140, 10, "#1e293b", 4)
    c.rect(66, 292, 168, 7, "#64748b", 3)
    c.rect(34, 314, 232, 1, "#e2e8f0")
    c.rect(118, 326, 64, 10, "#2257d6", 4)
    return c.png()


def _architecture() -> bytes:
    """A simple box-and-arrow architecture diagram."""
    c = _Canvas(560, 300, "#ffffff")
    boxes = [(30, 40, "#dbeafe", "#2563eb"), (220, 40, "#dcfce7", "#16a34a"), (410, 40, "#fef3c7", "#d97706"),
             (220, 190, "#f3e8ff", "#9333ea"), (410, 190, "#e0f2fe", "#0284c7")]
    for x, y, fill, edge in boxes:
        c.card(x, y, 120, 70, fill, edge, 10)
        c.rect(x + 20, y + 24, 80, 8, edge, 3)
        c.rect(x + 30, y + 40, 60, 6, "#94a3b8", 3)
    for x in (150, 340):
        c.rect(x, 74, 70, 3, "#64748b")
        c.rect(x + 62, 70, 8, 11, "#64748b", 2)
    c.rect(279, 110, 3, 80, "#64748b")
    c.rect(340, 224, 70, 3, "#64748b")
    c.rect(402, 220, 8, 11, "#64748b", 2)
    return c.png()


def _avatar(bg: str, fg: str, accent: str, shape: str) -> bytes:
    """A small abstract profile photo: shapes on a color, no faces."""
    c = _Canvas(96, 96, bg)
    if shape == "rings":
        c.rect(18, 18, 60, 60, fg, 30)
        c.rect(30, 30, 36, 36, bg, 18)
        c.rect(40, 40, 16, 16, accent, 8)
    elif shape == "stack":
        for i, color in enumerate((fg, accent, fg)):
            c.rect(22, 24 + i * 18, 52, 12, color, 6)
    elif shape == "peak":
        for i in range(24):
            c.rect(48 - i - 2, 26 + i * 2, 2 * i + 4, 2, fg)
        c.rect(58, 22, 14, 14, accent, 7)
    else:  # "dots"
        for x, y, r, color in ((26, 30, 14, fg), (52, 44, 20, accent), (30, 62, 10, fg)):
            c.rect(x, y, r * 2, r * 2, color, r)
    return c.png()


# Chats with a sample profile photo: (background, shape color, accent, shape). The others keep the initial.
AVATARS = {
    ACME: ("#2257d6", "#e0e9ff", "#facc15", "rings"),
    KESTREL: ("#0f766e", "#ccfbf1", "#f97316", "peak"),
    CHANNEL: ("#1e293b", "#93c5fd", "#f8fafc", "stack"),
    MAYA: ("#be185d", "#fce7f3", "#fde68a", "dots"),
    PRIYA: ("#7c3aed", "#ede9fe", "#34d399", "rings"),
}


IMAGES = {"staging-dashboard.png": _dashboard, "ios-crash.png": _phone_crash, "architecture.png": _architecture}


def demo_home() -> Path:
    """A data directory for the demo archive, next to (never inside) the real one.

    It is a sibling of the data directory: "Dotiphant Demo" next to "Dotiphant", or
    "dotiphant-demo" next to "dotiphant" on Linux. With DOTIPHANT_HOME=/tmp/dev it is "/tmp/Dotiphant Demo".
    """
    home = config.data_dir_path()
    if home.name in ("Dotiphant Demo", "dotiphant-demo"):
        return home
    return home.parent / ("dotiphant-demo" if home.name == "dotiphant" else "Dotiphant Demo")


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def seed_demo(home: Path | str | None = None) -> Path:
    """Create the sample archive in `home` (default: demo_home()). Returns the database path.

    Refuses to touch a database that holds a real (non-demo) archive.
    """
    home = Path(home) if home else demo_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "dotiphant.db"
    if path.exists():
        conn = db.connect(path)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            has_chats = "chats" in tables and conn.execute("SELECT 1 FROM chats LIMIT 1").fetchone() is not None
            is_demo = not has_chats or ("meta" in tables and db.get_meta(conn, "demo") == "1")
        finally:
            conn.close()
        if not is_demo:
            raise RuntimeError(f"{path} holds a real archive; the demo only writes into its own directory.")
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)

    rng = random.Random(7)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = now - dt.timedelta(days=30)
    media_root = home / "media"
    media_root.mkdir(exist_ok=True)

    conn = db.open_rw(path)
    try:
        with conn:
            db.set_meta(conn, "demo", "1")
            db.set_meta(conn, "owner_id", str(ME))
            # The account owner is a sender too, as after a real sync.
            conn.execute("INSERT INTO senders VALUES(?,?,?,?)", (ME, "Me", None, 0))
            for pid, (name, username) in PEOPLE.items():
                conn.execute("INSERT INTO senders VALUES(?,?,?,?)", (pid, name, username, int(pid == 1009)))
            for chat_id, kind, title, username, is_forum, members, migrated_to in CHATS:
                conn.execute(
                    "INSERT INTO chats(chat_id, kind, title, username, is_forum, member_count, migrated_to,"
                    " unread_count, first_seen_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (chat_id, kind, title, username, is_forum, members, migrated_to, 0, _iso(start), _iso(now)),
                )
            for chat_id, topics in TOPICS.items():
                for topic_id, title in topics.items():
                    conn.execute("INSERT INTO topics VALUES(?,?,?)", (chat_id, topic_id, title))

            total = 0
            for chat_id, kind, title, *_ in CHATS:
                total += _seed_chat(conn, rng, chat_id, kind, title, start, now, media_root)

            for chat_id, *_ in CHATS:
                row = conn.execute(
                    "SELECT max(date), max(message_id), min(message_id) FROM messages WHERE chat_id=?", (chat_id,)
                ).fetchone()
                conn.execute("UPDATE chats SET last_message_at=? WHERE chat_id=?", (row[0], chat_id))
                conn.execute(
                    "INSERT INTO sync_state VALUES(?,?,?,?,?,NULL)",
                    (chat_id, row[1] or 0, row[2], 1, _iso(now - dt.timedelta(minutes=5))),
                )
            conn.execute("UPDATE chats SET unread_count=3 WHERE chat_id=?", (ACME,))
            (media_root / "avatars").mkdir(exist_ok=True)
            for n, (chat_id, look) in enumerate(AVATARS.items(), start=1):
                rel = f"avatars/{chat_id}-{n}.png"
                (media_root / rel).write_bytes(_avatar(*look))
                conn.execute("UPDATE chats SET avatar_path=?, photo_id=? WHERE chat_id=?", (rel, n, chat_id))
            db.set_meta(conn, "last_sync", _iso(now - dt.timedelta(minutes=5)))
    finally:
        conn.close()

    status = {
        "state": "idle", "stage": "done", "chat": None, "chats_done": len(CHATS), "chats_total": len(CHATS),
        "new_messages": 0, "started_at": _iso(now - dt.timedelta(minutes=6)),
        "finished_at": _iso(now - dt.timedelta(minutes=5)), "message": None, "pid": None,
    }
    (home / "status.json").write_text(json.dumps(status, indent=2))
    return path




def _workday(t: dt.datetime, rng: random.Random) -> dt.datetime:
    """Move a time into 07:00 to 20:59 UTC, never later than it was."""
    if t.hour >= 21:
        return t.replace(hour=20, minute=rng.randint(5, 55))
    if t.hour < 7:
        return (t - dt.timedelta(days=1)).replace(hour=20, minute=rng.randint(5, 55))
    return t


def _recent_stamps(rng: random.Random, end: dt.datetime, n: int) -> list[dt.datetime]:
    """n close-together times ending at `end`, like a recent back-and-forth."""
    t = _workday(end - dt.timedelta(minutes=rng.randint(0, 20)), rng)
    stamps = [t]
    for _ in range(n - 1):
        t = _workday(t - dt.timedelta(seconds=rng.randint(60, 1500)), rng)
        stamps.append(t)
    return sorted(stamps)


def _seed_chat(conn, rng, chat_id, kind, title, start, now, media_root) -> int:
    script = [(s[0], s[1], s[2] if len(s) > 2 else {}) for s in SCRIPTS.get(chat_id, [])]
    target = {ACME: 110, HELIOS: 60, KESTREL: 90, LUMEN_OLD: 12, LUMEN: 40, MAYA: 30, TOMAS: 20, PRIYA: 18, CHANNEL: 5}[chat_id]
    tail_n = TAIL.get(chat_id, 0)
    head, tail = script[:len(script) - tail_n], script[len(script) - tail_n:]
    members = sorted({s for s, *_ in script if s != ME}) or [1001]

    # (sender, text, extra, index in the script or None for filler)
    lines: list[tuple[int, str, dict, int | None]] = []
    n_head = target - tail_n
    i = 0
    while len(lines) < n_head and (i < len(head) or kind != "channel"):
        if i < len(head) and (not lines or rng.random() < 0.45 or len(lines) >= n_head - (len(head) - i)):
            lines.append((*head[i], i))
            i += 1
        else:
            who = ME if rng.random() < 0.3 else rng.choice(members)
            lines.append((who, rng.choice(FILLER), {}, None))
    lines += [(*item, k) for k, item in enumerate(head[i:], start=i)]
    lines += [(*item, k) for k, item in enumerate(tail, start=len(head))]

    end = now - dt.timedelta(hours=LAST_ACTIVE.get(chat_id, 1))
    tail_stamps = _recent_stamps(rng, end, tail_n) if tail_n else []
    if chat_id == LUMEN_OLD:
        t0, t1 = start, start + dt.timedelta(days=6)
    elif chat_id == LUMEN:
        t0, t1 = start + dt.timedelta(days=6, hours=2), tail_stamps[0] - dt.timedelta(hours=3)
    else:
        t0 = start + dt.timedelta(hours=rng.randint(0, 48))
        t1 = (tail_stamps[0] - dt.timedelta(hours=rng.randint(3, 30))) if tail_stamps else now - dt.timedelta(minutes=rng.randint(10, 60 * 30))
    n_early = len(lines) - tail_n
    span = (t1 - t0).total_seconds()
    stamps = sorted(t0 + dt.timedelta(seconds=rng.random() * span) for _ in range(n_early))
    # Cluster some messages close together, like a real back-and-forth.
    for k in range(1, len(stamps)):
        if rng.random() < 0.55:
            stamps[k] = max(stamps[k], stamps[k - 1]) if stamps[k] - stamps[k - 1] < dt.timedelta(minutes=3) else stamps[k - 1] + dt.timedelta(seconds=rng.randint(20, 900))
    # Fold night hours into 07:00 to 20:59 UTC so the sample reads like a working week.
    stamps = [s if 7 <= s.hour < 21 else s.replace(hour=7 + s.hour % 14) for s in stamps]
    stamps = sorted(min(s, t1) for s in stamps) + tail_stamps

    msg_id = 1
    by_script: dict[int, int] = {}
    ids: list[int] = []
    rows = []

    def add(**m):
        nonlocal msg_id
        base = dict(chat_id=chat_id, message_id=msg_id, topic_id=None, sender_id=None, out=0, date=None,
                    edit_date=None, reply_to_id=None, forwarded_from=None, kind="text", text="",
                    media_type=None, media_name=None, media_size=None, media_path=None, service_text=None)
        base.update(m)
        rows.append(base)
        msg_id += 1
        return base

    first_sender = lines[0][0]
    if kind in ("group", "supergroup"):
        creator = PEOPLE.get(first_sender, ("You",))[0]
        if chat_id == LUMEN:
            add(date=_iso(stamps[0] - dt.timedelta(minutes=1)), kind="service",
                service_text="Group upgraded from an older group; earlier history is shown above")
        else:
            add(date=_iso(stamps[0] - dt.timedelta(minutes=2)), kind="service", sender_id=first_sender,
                service_text=f'{creator} created the group "{title}"')
            add(date=_iso(stamps[0] - dt.timedelta(minutes=1)), kind="service", sender_id=first_sender,
                service_text=f"{creator} added {', '.join(PEOPLE[p][0] for p in members[:3] if p != first_sender) or 'you'}")
        if chat_id in TOPICS:
            for topic_id, topic_title in list(TOPICS[chat_id].items())[1:]:
                add(date=_iso(stamps[0] - dt.timedelta(seconds=30)), kind="service", sender_id=1008,
                    topic_id=topic_id, service_text=f'Kenji Sato created the topic "{topic_title}"')

    for (who, text, extra, idx), when in zip(lines, stamps):
        out = int(who == ME)
        m = dict(date=_iso(when), sender_id=who, out=out, text=text)
        if chat_id in TOPICS:
            m["topic_id"] = next((t for k, t in TOPIC_FOR.items() if k in text), rng.choice([1, 1, 2, 3]))
        if extra.get("reply") and idx is not None and (idx - extra["reply"]) in by_script:
            m["reply_to_id"] = by_script[idx - extra["reply"]]
        elif idx is None and ids and rng.random() < 0.12 and kind != "channel":
            m["reply_to_id"] = rng.choice(ids[-8:])
        if idx is not None and not extra and len(text) > 40 and rng.random() < 0.12:
            m["edit_date"] = _iso(when + dt.timedelta(minutes=rng.randint(1, 30)))
        if extra.get("fwd"):
            m["forwarded_from"] = extra["fwd"]
        if name := extra.get("photo"):
            rel = f"{chat_id}/{msg_id}-{name}"
            dest = media_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = IMAGES[name]()
            dest.write_bytes(data)
            m.update(kind="media", media_type="photo", media_name=name, media_size=len(data), media_path=rel)
        elif extra.get("file"):
            mtype, name, size = extra["file"]
            m.update(kind="media", media_type=mtype, media_name=name, media_size=size)
        row = add(**m)
        ids.append(row["message_id"])
        if idx is not None:
            by_script[idx] = row["message_id"]
        if idx == 0 and kind in ("group", "supergroup") and chat_id != LUMEN_OLD:
            add(date=_iso(when + dt.timedelta(seconds=40)), kind="service", sender_id=who, reply_to_id=row["message_id"],
                service_text=f"{PEOPLE.get(who, ('You',))[0]} pinned a message")

    if chat_id == LUMEN_OLD:
        add(date=_iso(stamps[-1] + dt.timedelta(minutes=5)), kind="service",
            service_text="Group upgraded to a supergroup; history continues in the new group")

    for r in rows:
        conn.execute(
            "INSERT INTO messages(chat_id, message_id, topic_id, sender_id, out, date, edit_date, reply_to_id,"
            " forwarded_from, kind, text, media_type, media_name, media_size, media_path, service_text)"
            " VALUES(:chat_id,:message_id,:topic_id,:sender_id,:out,:date,:edit_date,:reply_to_id,"
            ":forwarded_from,:kind,:text,:media_type,:media_name,:media_size,:media_path,:service_text)",
            r,
        )
        if r["edit_date"]:
            conn.execute("INSERT INTO message_versions VALUES(?,?,?,?)",
                         (chat_id, r["message_id"], r["text"].rstrip(".") + " (draft)", r["edit_date"]))
    return len(rows)


if __name__ == "__main__":
    print(seed_demo(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
