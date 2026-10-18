"""Chaos tests for the web viewer: weird routes and params, hostile stored content, escaping.

Failing cases are marked xfail(strict=True) with the bug they document.
"""
from __future__ import annotations

from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from dotiphant import config, db, demo, query
from dotiphant.web import app as webapp

BASE = "http://127.0.0.1:8765"
SAME = {"Origin": BASE}
EVIL = -1009999999999
UNI = -1008888888888
XSS = '"><script>alert(1)</script><img src=x onerror=alert(2)>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    webapp._job.clear()
    demo.seed_demo(tmp_path)
    now = query.now_iso()
    conn = db.connect()
    with conn:
        conn.execute("INSERT INTO chats(chat_id, kind, title, username, is_forum, last_message_at, first_seen_at, updated_at)"
                     " VALUES(?,?,?,?,?,?,?,?)", (EVIL, "supergroup", XSS + "\n>>>", XSS, 1, now, now, now))
        conn.execute("INSERT INTO chats(chat_id, kind, title, last_message_at, first_seen_at, updated_at)"
                     " VALUES(?,?,?,?,?,?)", (UNI, "group", "\u00dcn\u00efc\u00f8d\u00e9 \u6f22\u5b57 \u202e chat", now, now, now))
        conn.execute("INSERT INTO senders(peer_id, name, username) VALUES(?,?,?)", (31337, XSS, XSS))
        conn.execute("INSERT INTO topics VALUES(?,?,?)", (EVIL, 5, XSS))
        rows = [
            (1, XSS + " https://ex.com/\"onmouseover=alert(3) javascript:alert(4)", None, None, None),
            (2, "\x02fake mark\x03 \x00 nul \r\n crlf \u202e rtl \U0001F468\u200d\U0001F469", None, None, None),
            (3, "", "document", XSS + ".pdf", "../../etc/passwd"),
            (4, "x" * 200_000, "photo", None, None),
            (5, "reply", None, None, None),
        ]
        for mid, text, mtype, mname, mpath in rows:
            conn.execute("INSERT INTO messages(chat_id, message_id, topic_id, sender_id, date, text, media_type, media_name,"
                         " media_path, reply_to_id, forwarded_from, kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (EVIL, mid, 5, 31337, now, text, mtype, mname, mpath, 99999 if mid == 5 else None,
                          XSS if mid == 1 else None, "media" if mtype else "text"))
        conn.execute("INSERT INTO messages(chat_id, message_id, date, kind, service_text) VALUES(?,?,?,?,?)",
                     (EVIL, 6, now, "service", XSS))
        conn.execute("INSERT INTO messages(chat_id, message_id, date, text) VALUES(?,?,?,?)",
                     (UNI, 1, "1970-01-01T00:00:00+00:00", "epoch"))
        conn.execute("INSERT INTO messages(chat_id, message_id, date, text) VALUES(?,?,?,?)",
                     (UNI, 2**31, "2099-12-31T23:59:59+00:00", "future"))
    conn.close()
    with TestClient(webapp.create_app(), base_url=BASE, raise_server_exceptions=False) as c:
        r = c.get(f"/?key={webapp.viewer_token()}", follow_redirects=False)
        assert r.status_code == 303
        c.headers["X-Dotiphant-Token"] = c.app.state.csrf_token
        yield c


def no_raw_xss(html: str) -> None:
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    assert 'onerror=alert' not in html or "&lt;img" in html
    assert 'href="javascript:' not in html.lower()
    assert '"onmouseover=' not in html


# --- escaping ----------------------------------------------------------------------------------

def test_hostile_content_is_escaped_everywhere(client):
    for url in ["/", f"/chat/{EVIL}", f"/chat/{EVIL}?topic=5", f"/chat/{EVIL}?at=5", f"/chat/{EVIL}?partial=1",
                "/search?q=alert", "/search?q=script", f"/search?q=reply&chat={EVIL}"]:
        r = client.get(url)
        assert r.status_code == 200, url
        no_raw_xss(r.text)


def test_control_marks_in_text_do_not_inject_html(client):
    r = client.get("/search", params={"q": "fake"})
    assert r.status_code == 200
    no_raw_xss(r.text)


def test_stored_mark_characters_do_not_shift_highlights(client):
    out = str(webapp.highlight_snippet("\x02\x02fake\x03 mark\x03 tail"))
    assert "<mark>fake</mark>" in out and "<mark> mark" not in out


def test_escaped_query_in_title_and_input(client):
    r = client.get("/search", params={"q": XSS})
    assert r.status_code == 200
    no_raw_xss(r.text)


# --- weird routes -----------------------------------------------------------------------------

@pytest.mark.parametrize("cid", ["abc", "-0", "0", "-", "--5", "1e5", "0x10", " 5", "%00", "%0a", "\u0665", "１２",
                                 "99999999999999999", "-9223372036854775808", "9223372036854775807"])
def test_weird_chat_ids_are_404_not_500(client, cid):
    for path in (f"/chat/{quote(cid)}", f"/chat/{quote(cid)}/export.md"):
        r = client.get(path)
        assert r.status_code in (200, 404), (path, r.status_code)


@pytest.mark.parametrize("cid", ["99999999999999999999", "-99999999999999999999", "9223372036854775808"])
def test_chat_id_beyond_int64_is_404(client, cid):
    assert client.get(f"/chat/{cid}").status_code == 404


@pytest.mark.parametrize("param", ["at", "before", "topic"])
def test_huge_query_ints_are_handled(client, param):
    r = client.get(f"/chat/{EVIL}", params={param: "9" * 30})
    assert r.status_code in (200, 404)


@pytest.mark.parametrize("params", [
    {"at": "-0"}, {"at": "-1"}, {"before": "0"}, {"topic": "abc"}, {"at": "1_0"}, {"at": " 5 "}, {"since": "garbage"},
    {"since": "\x00"}, {"since": "2026-13-01"}, {"partial": "\x01"}, {"topic": "-5"},
])
def test_weird_chat_params(client, params):
    r = client.get(f"/chat/{EVIL}", params=params)
    assert r.status_code in (200, 404), (params, r.status_code)


@pytest.mark.parametrize("path", [f"/chat/{EVIL}", f"/chat/{EVIL}/export.md", "/search"])
def test_huge_since_is_handled(client, path):
    r = client.get(path, params={"since": "99999999999d", "q": "reply"})
    assert r.status_code < 500


@pytest.mark.parametrize("q", ["\x00", "\x01\x02\x03", "\r\n", "a" * 60_000, "NEAR(a b)", "\"", "*", "text:x", "^x",
                               "%", "\u202e", "' OR 1=1 --"], ids=lambda q: repr(q)[:20])
def test_weird_search_queries(client, q):
    r = client.get("/search", params={"q": q})
    assert r.status_code == 200, q[:20]
    no_raw_xss(r.text)


def test_search_huge_chat(client):
    assert client.get("/search", params={"q": "reply", "chat": "9" * 20}).status_code == 200


@pytest.mark.parametrize("chat", ["abc", "-0", "", "\x00"])
def test_search_weird_chat(client, chat):
    assert client.get("/search", params={"q": "reply", "chat": chat}).status_code == 200


# --- export ------------------------------------------------------------------------------------

def test_export_download_with_unicode_title(client):
    r = client.get(f"/chat/{UNI}/export.md", params={"download": "1"})
    assert r.status_code == 200
    assert "epoch" in r.text and "future" in r.text


def test_export_with_hostile_title(client):
    r = client.get(f"/chat/{EVIL}/export.md", params={"download": "1"})
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "\n" not in cd and cd.count('"') == 2


def test_extreme_dates_render(client):
    r = client.get(f"/chat/{UNI}")
    assert r.status_code == 200
    assert "1970" in r.text and "2099" in r.text


# --- media -------------------------------------------------------------------------------------

def test_media_paths(client, tmp_path):
    media = config.media_dir()
    (media / "x").mkdir()
    name = "\u6f22\u5b57 \U0001F600 \u202efdp.png"
    (media / "x" / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    (media / "x" / "page.html").write_text("<script>alert(1)</script>")
    (tmp_path / "secret.txt").write_text("secret")
    assert client.get("/media/" + quote(f"x/{name}")).status_code == 200
    r = client.get("/media/x/page.html")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    for bad in ["../secret.txt", "..%2fsecret.txt", "%2e%2e/secret.txt", "x/../../secret.txt", "/etc/passwd",
                "%00", "x/%00", "\u202e", "C:\\Windows", "x/..%5c..%5csecret.txt"]:
        r = client.get("/media/" + bad)
        assert r.status_code in (404,), (bad, r.status_code)
        assert "secret" not in r.text


def test_media_dangling_path_in_db_renders(client):
    r = client.get(f"/chat/{EVIL}")
    assert r.status_code == 200
    assert "/media/../../etc/passwd" not in r.text


# --- API ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"chat_id": EVIL, "message_ids": [1, "2", None, [], {}, "x", -1]},
    {"chat_id": EVIL, "messages": [[EVIL, 1], [EVIL], ["a", "b"], None, [2**70, 1]]},
    {"chat_id": str(EVIL), "messages": "nope"},
    {"chat_id": None}, {"chat_id": [1]}, {"chat_id": "9" * 25}, {"chat_id": 1.5}, {},
])
def test_api_markdown_weird_bodies(client, body):
    r = client.post("/api/markdown", json=body, headers=SAME)
    assert r.status_code in (200, 400, 404), (body, r.status_code)


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b"null", b"\xff\xfe", b'{"chat_id": NaN}', b'"x"' * 1000])
def test_api_markdown_garbage_json(client, raw):
    r = client.post("/api/markdown", content=raw, headers={**SAME, "Content-Type": "application/json"})
    assert r.status_code in (400, 404)


def test_api_markdown_huge_ids(client):
    r = client.post("/api/markdown", json={"chat_id": EVIL, "message_ids": [2**70]}, headers=SAME)
    assert r.status_code == 200


def test_api_favorite_weird(client):
    for cid in ["abc", None, [], "-0", 1.5]:
        r = client.post("/api/favorite", json={"chat_id": cid}, headers=SAME)
        assert r.status_code in (200, 400)


def test_media_very_long_name_is_404(client):
    assert client.get("/media/x/" + "a" * 5000).status_code == 404
