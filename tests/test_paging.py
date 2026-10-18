"""Paging through a chat when many messages share a timestamp.

WhatsApp exports have minute resolution, so busy chats have long runs of messages with the
same date. Every page boundary must use (date, message_id), or messages are lost.
"""
from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from dotiphant import db, query
from dotiphant import importer_whatsapp as wa
from dotiphant import mcp_server as ms
from dotiphant.web import app as webapp

BASE = "http://127.0.0.1:8765"


def import_lines(home, name: str, lines: list[str]) -> int:
    src = home / f"WhatsApp Chat with {name}.txt"
    src.write_text("\n".join(lines) + "\n")
    return wa.import_export(src, date_order="mdy", tz="UTC")["chats"][0]["chat_id"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    webapp._job.clear()
    return tmp_path


@pytest.fixture
def busy(home):
    """1000 messages, three per minute."""
    lines = [f"01/02/2024, {10 + i // 180:02d}:{(i // 3) % 60:02d} - Ann: m{i}" for i in range(1000)]
    return import_lines(home, "Group", lines)


@pytest.fixture
def burst(home):
    """5 messages at 09:00, then 251 messages all at 10:00."""
    lines = [f"01/02/2024, 09:00 - Ann: early {i}" for i in range(5)]
    lines += [f"01/02/2024, 10:00 - Ann: burst {i}" for i in range(251)]
    return import_lines(home, "Ann", lines)


def ids(msgs) -> list[int]:
    return [m["message_id"] for m in msgs]


def test_before_cursor_reaches_every_message(busy):
    conn = db.connect(readonly=True)
    seen: list[int] = []
    page = query.get_messages(conn, busy, limit=200)
    while page:
        seen = ids(page) + seen
        page = query.get_messages(conn, busy, before_id=page[0]["message_id"], limit=200)
    assert seen == list(range(1, 1001))


def test_around_inside_a_large_tie_includes_the_anchor(burst):
    conn = db.connect(readonly=True)
    for anchor in (1, 6, 130, 256):
        page = query.get_messages(conn, burst, around_id=anchor, limit=50)
        assert anchor in ids(page)
        assert ids(page) == sorted(ids(page)) and len(set(ids(page))) == len(page)
    page = query.get_messages(conn, burst, around_id=130, limit=50)
    assert ids(page) == list(range(105, 156))


def test_has_older_and_newer_with_ties(burst):
    conn = db.connect(readonly=True)
    page = query.get_messages(conn, burst, limit=200)
    assert ids(page) == list(range(57, 257))
    assert webapp.has_older(conn, burst, page[0], None)  # #1-#56, partly at the same minute as #57
    assert not webapp.has_newer(conn, burst, page[-1], None)
    page = query.get_messages(conn, burst, around_id=1, limit=20)
    assert not webapp.has_older(conn, burst, page[0], None)
    assert webapp.has_newer(conn, burst, page[-1], None)


def test_viewer_load_older_walks_to_the_start(busy):
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        assert c.get(f"/?key={webapp.viewer_token()}", follow_redirects=False).status_code == 303
        html = c.get(f"/chat/{busy}").text
        seen: list[int] = []
        while True:
            seen = [int(i) for i in re.findall(r'data-chat="-?\d+" data-id="(\d+)"', html)] + seen
            link = re.search(r'data-older href="([^"]+)"', html)
            if not link:
                break
            html = c.get(link.group(1).replace("&amp;", "&") + "&partial=1").text
    assert seen == list(range(1, 1001))


def test_mcp_paging_never_repeats_or_skips(busy):
    seen: list[int] = []
    out = ms.get_messages(str(busy), limit=300)
    for _ in range(10):
        page = [int(i) for i in re.findall(r"\] #(\d+) ", out)]
        assert not set(page) & set(seen)
        seen = page + seen
        hint = re.search(r"before_message_id=(\d+)", out)
        if not hint:
            break
        out = ms.get_messages(str(busy), before_message_id=int(hint.group(1)), limit=300)
    assert seen == list(range(1, 1001))


def test_mcp_before_unknown_message(busy):
    assert "not in this chat" in ms.get_messages(str(busy), before_message_id=5000, limit=5)


@pytest.fixture
def upgraded(home):
    """A basic group upgraded to a supergroup; both chats have message ids 1 to 3."""
    db.init()
    conn = db.connect()
    now = "2024-01-01T00:00:00+00:00"
    for cid, kind, mig in ((-100900, "supergroup", None), (-900, "group", -100900)):
        conn.execute("INSERT INTO chats(chat_id, kind, title, migrated_to, first_seen_at, updated_at)"
                     " VALUES (?,?,?,?,?,?)", (cid, kind, f"Team {cid}", mig, now, now))
    for cid, day in ((-900, "01"), (-100900, "02")):
        for mid in (1, 2, 3):
            conn.execute("INSERT INTO messages(chat_id, message_id, date, text) VALUES (?,?,?,?)",
                         (cid, mid, f"2024-01-{day}T10:00:00+00:00", f"{cid} #{mid}"))
    conn.commit()
    conn.close()
    return -100900


def test_before_cursor_in_the_earlier_basic_group(upgraded):
    conn = db.connect(readonly=True)
    page = query.get_messages(conn, upgraded, limit=4)
    assert [(m["chat_id"], m["message_id"]) for m in page][0] == (-900, 3)
    older = query.get_messages(conn, upgraded, before_id=3, before_chat_id=-900, limit=4)
    assert [(m["chat_id"], m["message_id"]) for m in older] == [(-900, 1), (-900, 2)]
    # Without the chat, the id means the supergroup's own message.
    older = query.get_messages(conn, upgraded, before_id=3, limit=4)
    assert [(m["chat_id"], m["message_id"]) for m in older][-1] == (-100900, 2)


def test_viewer_load_older_carries_the_basic_group(upgraded, monkeypatch):
    monkeypatch.setattr(webapp, "PAGE_SIZE", 4)
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        assert c.get(f"/?key={webapp.viewer_token()}", follow_redirects=False).status_code == 303
        html = c.get(f"/chat/{upgraded}").text
        link = re.search(r'data-older href="([^"]+)"', html).group(1).replace("&amp;", "&")
        assert link == f"/chat/{upgraded}?before=3&before_chat=-900"
        part = c.get(link + "&partial=1").text
        assert re.findall(r'data-chat="(-?\d+)" data-id="(\d+)"', part) == [("-900", "1"), ("-900", "2")]


def test_mcp_hint_names_the_basic_group(upgraded):
    from dotiphant import config
    config.Settings(ai_access="all").save()
    out = ms.get_messages(str(upgraded), limit=4)
    assert "call again with chat=-900 and before_message_id=3." in out
    out = ms.get_messages("-900", before_message_id=3, limit=4)
    assert re.findall(r"\] #(\d+) ", out) == ["1", "2"]


def test_after_cursor_walks_forward_through_a_tie(burst):
    conn = db.connect(readonly=True)
    seen: list[int] = []
    page = query.get_messages(conn, burst, since="2024-01-02T10:00:00+00:00", limit=200)
    while page:
        seen += ids(page)
        page = query.get_messages(conn, burst, after_id=page[-1]["message_id"], limit=200)
    assert seen == list(range(6, 257))


def test_mcp_forward_paging_through_a_tie(burst):
    seen: list[int] = []
    out = ms.get_messages(str(burst), since="2024-01-02T10:00:00+00:00", limit=200)
    for _ in range(5):
        page = [int(i) for i in re.findall(r"\] #(\d+) ", out)]
        assert not set(page) & set(seen)
        seen += page
        assert "since=" not in out.split(">>>")[-1]
        hint = re.search(r"after_message_id=(\d+)", out)
        if not hint:
            break
        out = ms.get_messages(str(burst), since="2024-01-02T10:00:00+00:00",
                              after_message_id=int(hint.group(1)), limit=200)
    assert seen == list(range(6, 257))


def test_viewer_load_newer_walks_through_a_tie(burst, monkeypatch):
    monkeypatch.setattr(webapp, "PAGE_SIZE", 200)
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        assert c.get(f"/?key={webapp.viewer_token()}", follow_redirects=False).status_code == 303
        html = c.get(f"/chat/{burst}?since=2024-01-02").text
        seen: list[int] = []
        for _ in range(5):
            seen += [int(i) for i in re.findall(r'data-chat="-?\d+" data-id="(\d+)"', html)]
            link = re.search(r'href="([^"]+)">Load newer messages', html)
            if not link:
                break
            assert "since=" not in link.group(1)
            html = c.get(link.group(1).replace("&amp;", "&")).text
    assert seen == list(range(1, 257))


def test_after_cursor_from_the_earlier_basic_group(upgraded):
    from dotiphant import config
    config.Settings(ai_access="all").save()
    conn = db.connect(readonly=True)
    page = query.get_messages(conn, upgraded, after_id=2, after_chat_id=-900, limit=2)
    assert [(m["chat_id"], m["message_id"]) for m in page] == [(-900, 3), (-100900, 1)]
    out = ms.get_messages(str(upgraded), since="2000-01-01", limit=2)
    assert "Call again with after_message_id=2 and after_chat=-900." in out
    out = ms.get_messages(str(upgraded), after_message_id=2, after_chat=-900, limit=2)
    assert re.findall(r"\] #(\d+) ", out) == ["3", "1"]
    assert "Call again with after_message_id=1." in out
