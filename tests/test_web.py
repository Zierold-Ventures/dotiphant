"""Web viewer tests against a demo archive in a temporary DOTIPHANT_HOME."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import dotiphant
from dotiphant import config, db, demo, query
from dotiphant import status as sync_status
from dotiphant.web import app as webapp

BASE = "http://127.0.0.1:8765"
SAME = {"Origin": BASE}
ACME = demo.ACME


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    webapp._job.clear()
    return tmp_path


@pytest.fixture
def seeded(home):
    demo.seed_demo(home)
    return home


def login(c: TestClient) -> TestClient:
    """Authenticate like the browser does after `dotiphant open`: exchange ?key= for the cookie."""
    r = c.get(f"/?key={webapp.viewer_token()}", follow_redirects=False)
    assert r.status_code == 303
    c.headers["X-Dotiphant-Token"] = c.app.state.csrf_token  # what app.js reads from the page
    return c


@pytest.fixture
def client(seeded):
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        yield login(c)


@pytest.fixture
def not_demo(monkeypatch):
    """Treat the seeded archive as a real one, so refresh and download are offered."""
    monkeypatch.setattr(webapp, "is_demo", lambda: False)


@pytest.fixture
def anon(seeded):
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        yield c


def add_message(chat_id: int, message_id: int, text: str, **extra) -> None:
    conn = db.connect()
    with conn:
        conn.execute(
            "INSERT INTO messages(chat_id, message_id, sender_id, date, text, kind, media_type, media_name, media_path)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (chat_id, message_id, 1001, query.now_iso(), text, extra.get("kind", "text"),
             extra.get("media_type"), extra.get("media_name"), extra.get("media_path")),
        )
    conn.close()


def test_home_lists_chats(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Acme x Northwind integration" in r.text
    assert "Kestrel Protocol Builders" in r.text
    assert "min ago" in r.text  # last sync, relative
    assert "Content-Security-Policy" in r.headers or "content-security-policy" in r.headers


def test_chat_page_renders_and_escapes(client, not_demo):
    add_message(ACME, 9001, '<script>alert("x")</script> see https://example.com/a?b=1&c=2.')
    r = client.get(f"/chat/{ACME}")
    assert r.status_code == 200
    assert "<script>alert" not in r.text
    assert "&lt;script&gt;alert(" in r.text
    assert 'href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer"' in r.text
    assert 'class="day"' in r.text
    assert "staging-dashboard.png" in r.text  # downloaded demo photo
    assert f'data-media="{ACME}"' in r.text


def test_chat_deep_link_and_partial(client):
    r = client.get(f"/chat/{ACME}?at=5")
    assert r.status_code == 200
    assert f'id="m{ACME}_5"' in r.text
    r = client.get(f"/chat/{ACME}?before=60&partial=1")
    assert r.status_code == 200
    assert "<html" not in r.text
    assert 'class="msg' in r.text


def test_forum_topics_and_merged_predecessor(client):
    r = client.get(f"/chat/{demo.KESTREL}")
    assert "All topics" in r.text and "Grants" in r.text
    r = client.get(f"/chat/{demo.KESTREL}?topic=3")
    assert r.status_code == 200
    r = client.get(f"/chat/{demo.LUMEN}?since=2000-01-01")
    assert "Starting a small group for the Lumen partnership." in r.text


def test_unknown_chat_is_404(client):
    assert client.get("/chat/424242").status_code == 404
    assert client.get("/chat/abc").status_code == 404


def test_search_highlights_safely(client):
    add_message(ACME, 9002, "Invoice <b>bold</b> [not a mark] zebrafish payload")
    r = client.get("/search", params={"q": "zebrafish"})
    assert r.status_code == 200
    assert "<mark>zebrafish</mark>" in r.text
    assert "&lt;b&gt;bold&lt;/b&gt;" in r.text
    assert "[not a mark]" in r.text
    assert f"/chat/{ACME}?at=9002#m{ACME}_9002" in r.text


def test_highlight_snippet_unit():
    out = str(webapp.highlight_snippet("a [<img>] and \x02word\x03 x"))
    assert out == "a [&lt;img&gt;] and <mark>word</mark> x"


def test_api_markdown(client):
    r = client.post("/api/markdown", json={"chat_id": ACME, "message_ids": [5, 6]}, headers=SAME)
    assert r.status_code == 200
    md = r.json()["markdown"]
    assert md.startswith("# Acme x Northwind integration")
    assert "(#5, dp-" in md and "(#6, dp-" in md


def test_api_markdown_whatsapp_chat_id(client, home):
    """WhatsApp chat ids exceed 2**53: the viewer sends them as strings, rounding finds nothing."""
    from dotiphant import importer_whatsapp as wa
    src = home / "WhatsApp Chat with Ann.txt"
    src.write_text("01/02/2024, 10:00 - Ann: hello\n01/02/2024, 10:01 - Ann: again\n")
    cid = wa.import_export(src, date_order="mdy", tz="UTC")["chats"][0]["chat_id"]
    assert cid == wa.chat_id_for("Ann") and cid > 2**53
    r = client.post("/api/markdown", json={"chat_id": str(cid), "messages": [[str(cid), "1"], [str(cid), "2"]]},
                    headers=SAME)
    assert r.status_code == 200 and r.json()["count"] == 2
    assert "hello" in r.json()["markdown"] and "again" in r.json()["markdown"]
    rounded = int(float(cid))  # what Number() in JavaScript would send
    r = client.post("/api/markdown", json={"chat_id": str(cid), "messages": [[rounded, 1]]}, headers=SAME)
    assert r.json()["count"] == 0
    r = client.post("/api/markdown", json={"chat_id": str(cid), "messages": [[str(cid), "1.5"], [True, 1]]},
                    headers=SAME)
    assert r.json()["count"] == 0


def test_app_js_keeps_message_ids_as_strings():
    js = (Path(webapp.__file__).parent / "static" / "app.js").read_text()
    assert not re.search(r"(Number|parseInt|parseFloat)\(\s*\w+\.dataset\.(chat|id)\b", js)
    assert not re.search(r"\+\s*\w+\.dataset\.(chat|id)\b", js)


def test_post_without_matching_origin_is_rejected(client):
    body = {"chat_id": ACME, "message_ids": [5]}
    assert client.post("/api/markdown", json=body).status_code == 403
    assert client.post("/api/markdown", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/favorite", json={"chat_id": ACME}, headers={"Referer": "http://evil.example/x"}).status_code == 403
    assert client.post("/api/refresh", json={}, headers={"Origin": "http://127.0.0.1:9999"}).status_code == 403


def test_foreign_host_header_is_rejected(seeded):
    with TestClient(webapp.create_app(), base_url="http://attacker.example") as c:
        assert c.get("/").status_code == 400


def test_media_serving_and_traversal(client, seeded):
    (seeded / "secret.txt").write_text("top secret")
    rel = next((seeded / "media" / str(ACME)).iterdir()).name
    r = client.get(f"/media/{ACME}/{rel}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    for path in ["/media/../secret.txt", "/media/%2e%2e/secret.txt", "/media/..%2fsecret.txt", "/media/" + str(seeded / "secret.txt")]:
        r = client.get(path)
        assert r.status_code == 404, path
        assert "top secret" not in r.text
    (seeded / "media" / "evil.html").write_text("<script>alert(1)</script>")
    r = client.get("/media/evil.html")
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]


def test_favorites_toggle_persists(client):
    r = client.post("/api/favorite", json={"chat_id": ACME}, headers=SAME)
    assert r.json()["starred"] is True
    assert ACME in config.Settings.load().favorites
    assert "Favorites" in client.get("/").text
    r = client.post("/api/favorite", json={"chat_id": ACME}, headers=SAME)
    assert r.json()["starred"] is False
    assert ACME not in config.Settings.load().favorites



def test_viewer_settings_change_waits_for_another_process(client):
    """`dotiphant ai mode selected` is saving while the viewer stars a chat: both changes stay."""
    inside, release = threading.Event(), threading.Event()

    def slow_cli_change(s):
        inside.set()
        assert release.wait(5)
        s.ai_access = "selected"

    cli = threading.Thread(target=lambda: config.update_settings(slow_cli_change))
    cli.start()
    assert inside.wait(5)
    answers = []
    viewer = threading.Thread(target=lambda: answers.append(
        client.post("/api/favorite", json={"chat_id": ACME}, headers=SAME)))
    viewer.start()
    viewer.join(0.2)
    assert viewer.is_alive()  # waiting for settings.lock
    release.set()
    cli.join(5)
    viewer.join(5)
    assert answers[0].json()["starred"] is True
    s = config.Settings.load()
    assert s.ai_access == "selected" and ACME in s.favorites


def test_viewer_leaves_an_unreadable_settings_file_alone(client):
    path = config.data_dir() / "settings.json"
    path.write_text('{"ai_access": "selected"')
    r = client.post("/api/ai-mode", json={"mode": "all"}, headers=SAME)
    assert r.status_code == 409 and "cannot be read" in r.json()["error"]
    assert path.read_text() == '{"ai_access": "selected"'

def test_empty_db_shows_onboarding(home):
    # Set up but nothing synced yet (a fresh install is sent to /setup, see test_onboarding.py).
    for name, value in (("api_id", "12345"), ("api_hash", "a" * 32), ("session", "s")):
        config.set_secret(name, value)
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        r = login(c).get("/")
        pages = [c.get(p).text for p in ("/connect", "/search")]
    assert r.status_code == 200
    assert "dotiphant setup" in r.text and "dotiphant demo" in r.text
    # Nothing to refresh yet, on any page.
    assert all("data-refresh=" not in p for p in [r.text, *pages])


def test_refresh_starts_detached_process(client, seeded, monkeypatch, not_demo):
    calls = []

    class FakeProc:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

        def poll(self):
            return None

    monkeypatch.setattr(sync_status.subprocess, "Popen", FakeProc)
    r = client.post("/api/refresh", json={"chat_ids": [ACME]}, headers=SAME)
    assert r.status_code == 200 and r.json()["started"] is True
    args, kwargs = calls[0]
    assert args[1:] == ["-m", "dotiphant", "sync", "--quiet", str(ACME)]
    assert kwargs["start_new_session"] is True
    assert (seeded / "logs" / "sync.log").exists()
    # A second start while the first runs is refused.
    r = client.post("/api/refresh", json={}, headers=SAME)
    assert r.status_code == 409 and r.json()["started"] is False
    assert len(calls) == 1
    assert client.get("/api/status").json()["running"] is True


def test_refresh_refused_when_status_says_running(client, seeded, monkeypatch, not_demo):
    import os

    (seeded / "status.json").write_text(json.dumps({"state": "running", "stage": "fetch", "pid": os.getpid()}))
    monkeypatch.setattr(sync_status.subprocess, "Popen", lambda *a, **k: pytest.fail("should not start"))
    with open(config.lock_path(), "a+") as lock:  # a running sync holds the lock
        assert sync_status.try_lock(lock)
        r = client.post("/api/media", json={"chat_id": ACME}, headers=SAME)
    assert r.status_code == 409
    # Without the lock the same status.json is a crashed run, and a new job may start.
    assert sync_status.read_status()["stale"]


def test_media_download_command(client, monkeypatch, not_demo):
    calls = []

    class FakeProc:
        def __init__(self, args, **kwargs):
            calls.append(args)

        def poll(self):
            return 0

    monkeypatch.setattr(sync_status.subprocess, "Popen", FakeProc)
    r = client.post("/api/media", json={"chat_id": ACME}, headers=SAME)
    assert r.status_code == 200
    assert calls[0][1:] == ["-m", "dotiphant", "media", str(ACME)]


def test_one_attachment_downloads_alone(client, monkeypatch, not_demo):
    calls = []

    class FakeProc:
        pid = 1

        def __init__(self, args, **kwargs):
            calls.append(args)

        def poll(self):
            return 0

    monkeypatch.setattr(sync_status.subprocess, "Popen", FakeProc)
    assert client.post("/api/media", json={"chat_id": ACME, "message_id": "x"}, headers=SAME).status_code == 400
    assert client.post("/api/media", json={"chat_id": ACME, "message_id": 7}, headers=SAME).status_code == 200
    assert calls[-1][1:] == ["-m", "dotiphant", "media", str(ACME), "7"]

    page = client.get(f"/chat/{ACME}").text
    assert re.search(r'<button type="button" class="file missing" data-media="%d" data-media-msg="\d+"' % ACME, page)


def test_auto_download_setting(client, not_demo):
    box = re.search(r'<input type="checkbox" data-auto-download="%d"( checked)?>' % ACME, client.get(f"/chat/{ACME}").text)
    assert box and not box.group(1)
    r = client.post("/api/auto-download", json={"chat_id": ACME, "on": True}, headers=SAME)
    assert r.status_code == 200 and config.Settings.load().auto_download_chats == [ACME]
    assert f'data-auto-download="{ACME}" checked' in client.get(f"/chat/{ACME}").text
    client.post("/api/auto-download", json={"chat_id": ACME, "on": False}, headers=SAME)
    assert config.Settings.load().auto_download_chats == []
    assert client.post("/api/auto-download", json={"chat_id": ACME, "on": "yes"}, headers=SAME).status_code == 400
    assert client.post("/api/auto-download", json={"chat_id": 123456, "on": True}, headers=SAME).status_code == 404
    assert client.post("/api/auto-download", json={"chat_id": ACME, "on": True}).status_code == 403


def test_export_markdown(client):
    r = client.get(f"/chat/{ACME}/export.md?since=7d")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# Acme x Northwind integration")
    r = client.get(f"/chat/{ACME}/export.md?download=1")
    assert "attachment" in r.headers["content-disposition"]


def test_connect_page(client):
    r = client.get("/connect")
    assert r.status_code == 200
    assert "dotiphant connect claude-desktop" in r.text
    assert "mcpServers" in r.text or "dotiphant" in r.text


def test_viewer_never_writes_db(client, seeded):
    before = (seeded / "dotiphant.db").stat().st_mtime_ns
    for path in ["/", f"/chat/{ACME}", "/search?q=launch", "/connect", "/api/status"]:
        assert client.get(path).status_code == 200
    assert (seeded / "dotiphant.db").stat().st_mtime_ns == before


def test_demo_refuses_real_archive(home):
    conn = db.open_rw(home / "dotiphant.db")
    with conn:
        conn.execute("INSERT INTO chats(chat_id, kind, title, first_seen_at, updated_at) VALUES(1,'private','x','t','t')")
    conn.close()
    with pytest.raises(RuntimeError):
        demo.seed_demo(home)


def test_linkify():
    out = str(webapp.linkify('go to https://a.example/x). or javascript:alert(1) "https://b.example/?q=<x>"'))
    assert '<a href="https://a.example/x"' in out
    assert "javascript:alert(1)" in out and 'href="javascript' not in out
    assert "&lt;x&gt;" in out


# --- viewer authentication -----------------------------------------------------------

def test_requests_without_cookie_get_the_locked_page(anon, seeded):
    rel = next((seeded / "media" / str(ACME)).iterdir()).name
    for path in ["/", f"/chat/{ACME}", "/search?q=launch", f"/chat/{ACME}/export.md", f"/media/{ACME}/{rel}"]:
        r = anon.get(path)
        assert r.status_code == 401, path
        assert "dotiphant open" in r.text
        assert "Acme" not in r.text and "Northwind" not in r.text
        assert "content-security-policy" in r.headers
    r = anon.get("/api/status")
    assert r.status_code == 401 and "dotiphant open" in r.json()["error"]
    r = anon.post("/api/refresh", json={}, headers=SAME)
    assert r.status_code == 401
    assert anon.post("/api/shutdown", json={}, headers=SAME).status_code == 401
    assert anon.get("/static/app.css").status_code == 200  # the locked page needs its stylesheet


def test_key_exchange_sets_cookie_and_strips_key(anon):
    token = webapp.viewer_token()
    r = anon.get(f"/chat/{ACME}?at=5&key={token}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/chat/{ACME}?at=5"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("dotiphant_viewer_8765=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    r = anon.get(r.headers["location"])
    assert r.status_code == 200 and "Acme x Northwind integration" in r.text
    page_token = r.text.split('<meta name="dotiphant-token" content="', 1)[1].split('"', 1)[0]
    assert page_token == anon.app.state.csrf_token
    assert anon.get("/api/status", headers={"X-Dotiphant-Token": page_token}).status_code == 200


def test_cookie_alone_is_not_enough_for_api_and_posts(anon):
    """Browsers send 127.0.0.1 cookies to every port, so /api/ and POSTs also need the page token."""
    login(anon)
    del anon.headers["X-Dotiphant-Token"]
    assert anon.get("/").status_code == 200  # pages still open with the cookie
    assert anon.get("/api/status").status_code == 403
    r = anon.post("/api/favorite", json={"chat_id": ACME}, headers={**SAME, "X-Dotiphant-Token": "guess"})
    assert r.status_code == 403
    assert anon.post("/api/refresh", json={}, headers=SAME).status_code == 403
    assert anon.post("/api/setup/credentials", json={}, headers=SAME).status_code == 403
    # Stopping the viewer needs the viewer key itself; the cookie with the page token is not enough.
    page = {**SAME, "X-Dotiphant-Token": anon.app.state.csrf_token}
    assert anon.post("/api/shutdown", json={}, headers=page).status_code == 403
    # Scripts with the bearer key do not need it.
    assert anon.get("/api/status", headers={"Authorization": f"Bearer {webapp.viewer_token()}"}).status_code == 200


def test_redirect_never_leaves_the_viewer(anon):
    r = anon.get(f"//evil.example/x?key={webapp.viewer_token()}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/") and not r.headers["location"].startswith("//")


def test_wrong_key_or_cookie_is_rejected(anon):
    r = anon.get("/?key=nope", follow_redirects=False)
    assert r.status_code == 401 and "set-cookie" not in r.headers
    anon.cookies.set("dotiphant_viewer_8765", "nope")
    assert anon.get("/").status_code == 401
    assert anon.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_session_cookie_is_not_the_viewer_key(anon):
    """The cookie is a per-run session value, so capturing it never reveals the long-lived key."""
    token = webapp.viewer_token()
    login(anon)
    cookie = anon.cookies.get("dotiphant_viewer_8765")
    assert cookie and cookie == anon.app.state.session and cookie != token
    from dotiphant import viewer
    folder = anon.app.state.identity[viewer.FOLDER_HEADER]
    assert viewer.folder_id(config.data_dir(), cookie) != folder
    assert viewer.folder_id(config.data_dir(), token) == folder


def test_session_cookie_is_never_a_bearer_token(anon):
    login(anon)
    cookie = anon.cookies.get("dotiphant_viewer_8765")
    anon.cookies.clear()
    del anon.headers["X-Dotiphant-Token"]
    bearer = {"Authorization": f"Bearer {cookie}"}
    assert anon.get("/api/status", headers=bearer).status_code == 401
    r = anon.post("/api/ai-mode", json={"mode": "selected"}, headers={**SAME, **bearer})
    assert r.status_code == 401
    assert anon.post("/api/shutdown", json={}, headers={**SAME, **bearer}).status_code == 401
    r = anon.post("/api/setup/credentials", json={"api_id": "1", "api_hash": "0" * 32}, headers={**SAME, **bearer})
    assert r.status_code == 401


def test_viewer_key_as_cookie_is_rejected(anon):
    """Only the session value works as a cookie; the stored viewer key does not."""
    anon.cookies.set("dotiphant_viewer_8765", webapp.viewer_token())
    r = anon.get("/")
    assert r.status_code == 401 and "dotiphant-token" not in r.text


def test_session_cookie_ends_with_the_viewer(seeded):
    with TestClient(webapp.create_app(), base_url=BASE) as first:
        login(first)
        cookie = first.cookies.get("dotiphant_viewer_8765")
        assert first.get("/").status_code == 200
    with TestClient(webapp.create_app(), base_url=BASE) as restarted:
        restarted.cookies.set("dotiphant_viewer_8765", cookie)
        assert restarted.get("/").status_code == 401
        assert login(restarted).get("/").status_code == 200  # ?key= still opens a new session


def test_logout_forget_credentials_ends_a_viewer_on_any_port(seeded, monkeypatch):
    """The port scan in `logout` only covers the default ports; the viewer itself notices the key is gone."""
    from dotiphant import cli, telegram, viewer

    monkeypatch.setattr(webapp, "KEY_CHECK_SECONDS", 0)
    scanned = []
    monkeypatch.setattr(viewer, "stop_all", lambda ports, folders: (scanned.extend(ports),
                                                                    viewer.StopResult([], [], []))[1])
    async def offline_logout():
        config.delete_secret("session")
        return False

    monkeypatch.setattr(telegram, "logout", offline_logout)
    base = "http://127.0.0.1:19001"
    app = webapp.create_app()
    stopped = []
    app.state.shutdown = lambda: stopped.append(1)
    with TestClient(app, base_url=base) as c:
        old = webapp.viewer_token()
        assert c.get(f"/?key={old}", follow_redirects=False).status_code == 303
        page = {"X-Dotiphant-Token": app.state.csrf_token}
        assert c.get("/api/status", headers=page).status_code == 200
        assert cli.main(["logout", "--forget-credentials"]) == 0
        assert 19001 not in scanned
        assert c.get("/").status_code == 401  # the session cookie
        assert c.get("/api/status", headers=page).status_code == 401
        c.cookies.clear()
        assert c.get("/api/status", headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert c.get(f"/?key={old}", follow_redirects=False).status_code == 401
        assert stopped  # and the viewer stops itself


def test_replaced_viewer_key_ends_the_old_viewer(seeded, monkeypatch):
    monkeypatch.setattr(webapp, "KEY_CHECK_SECONDS", 0)
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        old = webapp.viewer_token()
        login(c)
        assert c.get("/").status_code == 200
        config.set_secret("viewer_token", "a-new-key")
        assert c.get("/").status_code == 401
        assert c.get("/", headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert c.get("/", headers={"Authorization": "Bearer a-new-key"}).status_code == 401


def test_bearer_token_works_for_scripts(anon):
    auth = {"Authorization": f"Bearer {webapp.viewer_token()}"}
    assert anon.get("/api/status", headers=auth).status_code == 200
    assert anon.get(f"/chat/{ACME}", headers=auth).status_code == 200


def test_token_is_stable_and_stored_as_secret(home):
    token = webapp.viewer_token()
    assert len(token) >= 32 and webapp.viewer_token() == token
    assert config.get_secret("viewer_token") == token


def test_serve_prints_private_link_without_browser(home, monkeypatch, capsys):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: seen.update(vars(self.config)))
    monkeypatch.setattr(webapp.webbrowser, "open", lambda url: pytest.fail("no browser"))
    webapp.serve(port=8899, open_browser=False)
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:8899/?key={webapp.viewer_token()}" in out
    assert seen["access_log"] is False and seen["host"] == "127.0.0.1"


# --- sync status and demo mode -----------------------------------------------------

def _write_status(home, **status):
    (home / "status.json").write_text(json.dumps(status))


def test_home_last_sync_ignores_failed_run(client, seeded, not_demo):
    _write_status(seeded, state="error", finished_at=query.now_iso(), message="Telegram said no.",
                  started_at=query.now_iso())
    r = client.get("/")
    assert "The last sync failed: Telegram said no." in r.text
    assert "min ago" in r.text  # from the last good per-chat sync, not the failed run


def test_home_warns_when_the_last_sync_missed_chats(client, seeded, not_demo):
    _write_status(seeded, state="idle", finished_at=query.now_iso(), started_at=query.now_iso(), errors=2,
                  message="2 chat(s) could not be read; they are retried next time.")
    text = client.get("/").text
    assert "The last sync could not read 2 chats." in text
    assert "The last sync failed" not in text
    _write_status(seeded, state="idle", finished_at=query.now_iso(), started_at=query.now_iso(), errors=0)
    assert "could not read" not in client.get("/").text


def test_demo_home_hides_sync_failures(client, seeded):
    _write_status(seeded, state="error", finished_at=query.now_iso(), message="Telegram said no.",
                  started_at=query.now_iso())
    assert "The last sync failed" not in client.get("/").text


def test_media_status_is_scoped_to_its_own_job(client, seeded, monkeypatch, not_demo):
    # A previous sync failed; a media download started now must not inherit that error.
    _write_status(seeded, state="error", message="old failure", pid=999999, started_at="2026-01-01T00:00:00+00:00")

    class FakeProc:
        pid = 4242

        def __init__(self, args, **kwargs):
            pass

        def poll(self):
            return 0

    monkeypatch.setattr(sync_status.subprocess, "Popen", FakeProc)
    r = client.post("/api/media", json={"chat_id": ACME}, headers=SAME)
    job = r.json()["job"]
    assert job["id"] and job["pid"] == 4242 and job["started_at"]
    last = client.get("/api/status").json()["last_job"]
    assert last["id"] == job["id"] and last["done"] and last["ok"] is True and last["message"] is None


def test_sync_job_reads_only_its_own_status(client, seeded, monkeypatch, not_demo):
    code = {"value": None}

    class FakeProc:
        pid = 5151

        def __init__(self, args, **kwargs):
            pass

        def poll(self):
            return code["value"]

    monkeypatch.setattr(sync_status.subprocess, "Popen", FakeProc)
    assert client.post("/api/refresh", json={}, headers=SAME).status_code == 200
    code["value"] = 1
    _write_status(seeded, state="error", message="Login expired.", pid=5151, new_messages=0)
    last = client.get("/api/status").json()["last_job"]
    assert last["ok"] is False and last["message"] == "Login expired."
    _write_status(seeded, state="error", message="someone else's run", pid=1)
    last = client.get("/api/status").json()["last_job"]
    assert last["ok"] is False and "someone else" not in last["message"]


def test_sync_job_with_unreadable_chats_finishes_with_a_warning(client, seeded, monkeypatch, not_demo):
    class FakeProc:
        pid = 6161

        def __init__(self, args, **kwargs):
            pass

        def poll(self):
            return 3  # EXIT_PARTIAL

    monkeypatch.setattr(sync_status.subprocess, "Popen", FakeProc)
    assert client.post("/api/refresh", json={}, headers=SAME).status_code == 200
    _write_status(seeded, state="idle", pid=6161, new_messages=4, errors=1,
                  message="1 chat(s) could not be read; they are retried next time.")
    last = client.get("/api/status").json()["last_job"]
    assert last["ok"] is True and last["partial"] is True
    assert last["message"] == "1 chat(s) could not be read; they are retried next time."


def test_demo_hides_refresh_and_download(client, monkeypatch):
    monkeypatch.setattr(sync_status.subprocess, "Popen", lambda *a, **k: pytest.fail("should not start"))
    assert webapp.is_demo()
    r = client.get("/")
    assert "data-refresh=" not in r.text and "refresh not available" in r.text
    r = client.get(f"/chat/{ACME}")
    # The attachment chip itself explains why: there is nothing to download in the demo.
    assert "data-media=" not in r.text and 'title="Not available in the demo."' in r.text
    for url, body in [("/api/refresh", {}), ("/api/media", {"chat_id": ACME})]:
        r = client.post(url, json=body, headers=SAME)
        assert r.status_code == 409 and r.json()["demo"] is True


def test_pages_have_accessibility_basics(client):
    """Language, skip link, one live region, and names for icon buttons and checkboxes."""
    for url in ("/", f"/chat/{ACME}", "/search?q=webhook", "/connect"):
        r = client.get(url)
        assert '<html lang="en">' in r.text
        assert 'href="#content">Skip to content' in r.text
        assert r.text.count('role="status" aria-live="polite"') == 1  # #announce, always present
        assert "<title>" in r.text and "<title></title>" not in r.text
    home = client.get("/").text
    assert 'aria-label="Add Acme x Northwind integration to favorites"' in home
    assert 'aria-label="Filter chats by name"' in home
    chat = client.get(f"/chat/{ACME}").text
    assert 'class="pick" aria-label="Select message from' in chat
    assert 'role="heading" aria-level="2"><button type="button" class="daypill"' in chat
    assert 'alt="Photo: staging-dashboard.png from' in chat
    assert '<span class="sr"> messages</span>' in chat  # timeline counts read as numbers of messages
    assert 'role="dialog" aria-modal="true"' in chat  # lightbox


def test_unknown_page_uses_the_viewer_error_page(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404
    assert '<html lang="en">' in r.text and "This page is not here." in r.text
    assert "content-security-policy" in r.headers
    r = client.get("/api/no-such-thing", headers={"X-Dotiphant-Token": client.app.state.csrf_token})
    assert r.status_code == 404 and r.json()["error"] == "Not found."


def test_server_error_page_keeps_the_security_headers(seeded, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(webapp.query, "overview", boom)
    with TestClient(webapp.create_app(), base_url=BASE, raise_server_exceptions=False) as c:
        r = login(c).get("/")
    assert r.status_code == 500
    assert "kaboom" not in r.text and "logs/" in r.text
    assert "content-security-policy" in r.headers


def test_whatsapp_chats_are_labelled_and_not_refreshable(client, seeded, not_demo):
    import sqlite3

    conn = sqlite3.connect(seeded / "dotiphant.db")
    conn.execute("UPDATE chats SET raw_json = ? WHERE chat_id = 1003", ('{"source": "whatsapp"}',))
    conn.commit()
    conn.close()
    home = client.get("/").text
    assert '<span class="src">WhatsApp</span>' in home
    chat = client.get("/chat/1003").text
    assert "Imported from a WhatsApp export" in chat
    assert "data-refresh=" not in chat and "data-media=" not in chat


# --- footer and theme switch -----------------------------------------------------------

STATIC = webapp.HERE / "static"


def assert_theme_footer(html: str) -> None:
    """One quiet footer with a System / Light / Dark radio group, and theme.js before the stylesheets."""
    assert html.count('<footer class="foot">') == 1
    footer = html.split('<footer class="foot">', 1)[1].split("</footer>", 1)[0]
    assert '<fieldset class="theme" data-theme-switch' in footer
    assert '<legend class="sr">Theme</legend>' in footer
    radios = re.findall(r'<input type="radio" name="dotiphant-theme" value="(\w+)"', footer)
    assert radios == ["system", "light", "dark"]
    assert f"Dotiphant {dotiphant.__version__}" in footer
    assert "<a " not in footer
    head = html.split("</head>", 1)[0]
    assert '<script src="/static/theme.js"></script>' in head  # synchronous: no defer, no async
    assert head.index("/static/theme.js") < head.index('rel="stylesheet"')
    assert_no_inline_code(html)


def assert_no_inline_code(html: str) -> None:
    """The CSP allows no inline script or style, so the markup must not need any."""
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert "<style" not in html
    assert not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+=", html)


def assert_strict_csp(headers) -> None:
    csp = headers["content-security-policy"]
    assert csp == webapp.CSP
    assert "script-src 'self';" in csp and "style-src 'self';" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_theme_footer_on_every_viewer_page(client):
    for path in ["/", f"/chat/{ACME}", "/search?q=launch", "/connect", "/chat/999999", "/no-such-page"]:
        r = client.get(path)
        assert r.status_code in (200, 404), path
        assert_theme_footer(r.text)
        assert_strict_csp(r.headers)


def test_locked_page_applies_the_saved_theme(anon):
    r = anon.get("/")
    assert r.status_code == 401
    assert '<script src="/static/theme.js"></script>' in r.text
    assert_no_inline_code(r.text)
    assert_strict_csp(r.headers)
    js = anon.get("/static/theme.js")  # served before login, like the stylesheets
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_theme_script_is_self_contained():
    js = (STATIC / "theme.js").read_text()
    assert "'dotiphant-theme'" in js and "localStorage" in js and "try {" in js
    assert "removeItem" in js  # System forgets the choice
    assert "dataset.theme" in js
    assert "DOMContentLoaded" in js  # wires the switch on pages without app.js


def test_theme_tokens_follow_the_attribute():
    css = (STATIC / "tokens.css").read_text()
    forced = re.search(r':root\[data-theme="dark"\] \{(.*?)\n\}', css, re.S).group(1)
    system = re.search(
        r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\n  \}', css, re.S
    ).group(1)
    norm = lambda block: [line.strip() for line in block.strip().splitlines()]  # noqa: E731
    assert norm(forced) == norm(system)  # the two dark copies must not drift apart
    assert "color-scheme: dark;" in forced
    assert re.search(r":root \{\s*color-scheme: light;", css)
    for name in ["app.css", "setup.css"]:
        sheet = (STATIC / name).read_text()
        for block in re.findall(r"@media \(prefers-color-scheme: dark\) \{(.*?)\n\}", sheet, re.S):
            assert ':root:not([data-theme="light"])' in block, name


def test_brand_art_is_inline_and_follows_the_theme():
    """The lens mark and the lens drawing are inline with the theme's --lens-* colors, inside the CSP."""
    for name in ["mark", "lens"]:
        svg = str(webapp.brand_art(name, "x"))
        assert svg.startswith('<svg class="x" aria-hidden="true"'), name
        assert "var(--lens-ink" in svg and "var(--lens-dot" in svg, name
        assert_no_inline_code(svg)
    assert not list((STATIC / "brand").glob("doti-*"))  # the mascot is gone from the app
    # Anything a page can't safely inline falls back to an <img> of the same file.
    assert webapp._inline_svg('<svg><script>alert(1)</script></svg>', "p") is None
    assert webapp._inline_svg('<svg><style>svg { fill: red }</style></svg>', "p") is None
    assert webapp._inline_svg('<svg><use href="https://example.com/x.svg#a"/></svg>', "p") is None
    inlined = webapp._inline_svg('<svg><style>.a{fill:var(--lens-ink,#000)}</style><path id="q" class="a"/>'
                                 '<g mask="url(#q)"/></svg>', "p")
    assert inlined == '<svg><path id="p-q" fill="var(--lens-ink,#000)"/><g mask="url(#p-q)"/></svg>'
    # A class without a color rule stays, as a hook for the page's motion CSS.
    kept = webapp._inline_svg('<svg><style>.a{fill:red}</style><g class="a lens-drift"/></svg>', "p")
    assert kept == '<svg><g fill="red" class="lens-drift"/></svg>'


def test_idle_pages_show_the_lens(client):
    for path in ["/search?q=zzzqqqxx", "/no-such-page"]:  # nothing found, 404
        html = client.get(path).text
        assert "idle-state" in html and 'class="lens-art' in html, path


# --- AI access ---------------------------------------------------------------------

def test_ai_access_switch_hides_and_shows_a_chat(client):
    page = client.get(f"/chat/{ACME}").text
    assert 'role="switch" aria-checked="true"' in page and 'aria-label="Visible to AI apps"' in page
    assert "Hidden from AI" not in client.get("/").text
    r = client.post("/api/ai-access", json={"chat_id": ACME, "visible": False}, headers=SAME)
    assert r.status_code == 200 and r.json() == {"chat_id": ACME, "visible": False, "mode": "all"}
    assert config.Settings.load().ai_hidden_chats == [ACME]
    assert 'aria-checked="false"' in client.get(f"/chat/{ACME}").text
    assert client.get("/").text.count("Hidden from AI</span>") == 1
    # The viewer is the owner's tool: the chat stays readable and searchable here.
    assert "Acme x Northwind integration" in client.get("/search?q=staging").text
    r = client.post("/api/ai-access", json={"chat_id": ACME, "visible": True}, headers=SAME)
    assert r.json()["visible"] is True and config.Settings.load().ai_hidden_chats == []


def test_ai_access_switch_in_selected_mode(client):
    client.post("/api/ai-mode", json={"mode": "selected"}, headers=SAME)
    assert 'aria-checked="false"' in client.get(f"/chat/{ACME}").text
    r = client.post("/api/ai-access", json={"chat_id": ACME, "visible": True}, headers=SAME)
    assert r.json() == {"chat_id": ACME, "visible": True, "mode": "selected"}
    assert config.Settings.load().ai_allowed_chats == [ACME]


def test_ai_access_rejects_bad_input(client):
    assert client.post("/api/ai-access", json={"chat_id": ACME}, headers=SAME).status_code == 400
    assert client.post("/api/ai-access", json={"chat_id": ACME, "visible": "no"}, headers=SAME).status_code == 400
    assert client.post("/api/ai-access", json={"chat_id": "x", "visible": False}, headers=SAME).status_code == 400
    assert client.post("/api/ai-access", json={"chat_id": 12345, "visible": False}, headers=SAME).status_code == 404
    assert client.post("/api/ai-mode", json={"mode": "some"}, headers=SAME).status_code == 400
    assert config.Settings.load().ai_hidden_chats == [] and config.Settings.load().ai_access == "all"


def test_ai_access_needs_origin_cookie_and_page_token(client, anon):
    body = {"chat_id": ACME, "visible": False}
    for url, data in (("/api/ai-access", body), ("/api/ai-mode", {"mode": "selected"})):
        assert client.post(url, json=data).status_code == 403  # no Origin
        assert client.post(url, json=data, headers={"Origin": "http://evil.example"}).status_code == 403
        assert anon.post(url, json=data, headers=SAME).status_code == 401  # no viewer key
        token = client.headers.pop("X-Dotiphant-Token")
        assert client.post(url, json=data, headers=SAME).status_code == 403  # cookie alone
        client.headers["X-Dotiphant-Token"] = token
    s = config.Settings.load()
    assert s.ai_hidden_chats == [] and s.ai_access == "all"


def test_connect_page_explains_ai_access(client):
    page = client.get("/connect").text
    assert "All chats, except the ones you hide." in page and 'data-ai-mode="selected"' in page
    client.post("/api/ai-access", json={"chat_id": ACME, "visible": False}, headers=SAME)
    total = len(query.list_chats(db.connect(readonly=True)))
    assert f"{total - 1} of {total} chats are visible" in client.get("/connect").text
    assert client.post("/api/ai-mode", json={"mode": "selected"}, headers=SAME).json() == {"mode": "selected"}
    page = client.get("/connect").text
    assert "Only the chats you choose." in page and "0 of" in page and 'data-ai-mode="all"' in page


# --- profile photos -----------------------------------------------------------------

def test_avatar_route_serves_the_stored_photo(client, seeded):
    row = db.connect(readonly=True).execute("SELECT avatar_path, photo_id FROM chats WHERE chat_id=?", (ACME,)).fetchone()
    assert row["avatar_path"] == f"avatars/{ACME}-{row['photo_id']}.png"
    r = client.get(f"/avatar/{ACME}?v={row['photo_id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
    assert "immutable" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    stale = client.get(f"/avatar/{ACME}?v=0")
    assert stale.status_code == 200 and "immutable" not in stale.headers["cache-control"]

    jpg = seeded / "media" / "avatars" / "5-7.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    conn = db.connect()
    with conn:
        conn.execute("UPDATE chats SET avatar_path='avatars/5-7.jpg', photo_id=7 WHERE chat_id=?", (demo.TOMAS,))
    conn.close()
    assert client.get(f"/avatar/{demo.TOMAS}?v=7").headers["content-type"] == "image/jpeg"


def test_avatar_route_404s_and_path_safety(client, seeded):
    assert client.get(f"/avatar/{demo.HELIOS}").status_code == 404, "a chat without a photo"
    assert client.get("/avatar/123456").status_code == 404, "an unknown chat"
    assert client.get("/avatar/abc").status_code == 404
    (seeded / "secret.png").write_bytes(b"\x89PNGsecret")
    (seeded / "media" / str(ACME)).mkdir(exist_ok=True)
    (seeded / "media" / str(ACME) / "x.png").write_bytes(b"\x89PNG")
    (seeded / "media" / "avatars" / "page.html").write_text("<script>")
    conn = db.connect()
    for rel in ("../secret.png", str(seeded / "secret.png"), f"{ACME}/x.png", "avatars/../../secret.png",
                "avatars/page.html", "avatars/missing.png"):
        with conn:
            conn.execute("UPDATE chats SET avatar_path=? WHERE chat_id=?", (rel, demo.HELIOS))
        assert client.get(f"/avatar/{demo.HELIOS}").status_code == 404, rel
    conn.close()


def test_avatar_route_needs_the_viewer_key(anon):
    r = anon.get(f"/avatar/{ACME}")
    assert r.status_code == 401 and b"PNG" not in r.content


def test_pages_show_photos_or_initials(client):
    home = client.get("/").text
    acme = re.search(r'<a href="/chat/%d">(.*?)</a>' % ACME, home, re.S).group(1)
    assert re.search(r'<img class="avatar mono" src="/avatar/%d\?v=\d+" alt="" width="32" height="32" loading="lazy"' % ACME, acme)
    helios = re.search(r'<a href="/chat/%d">(.*?)</a>' % demo.HELIOS, home, re.S).group(1)
    assert re.search(r'<span class="avatar tone[1-4] mono" aria-hidden="true">HL</span>', helios) and "<img" not in helios

    client.post("/api/favorite", json={"chat_id": ACME}, headers=SAME)
    home = client.get("/").text
    favs = home.split('<h2 class="section">All chats</h2>')[0]
    assert f'src="/avatar/{ACME}?v=' in favs, "favorites show the photo too"

    chat = client.get(f"/chat/{ACME}").text
    assert re.search(r'<img class="avatar chat-avatar" src="/avatar/%d\?v=\d+" alt="" width="36" height="36"' % ACME, chat)
    assert re.search(r'<span class="avatar tone[1-4] chat-avatar" aria-hidden="true">HL</span>', client.get(f"/chat/{demo.HELIOS}").text)

    found = client.get("/search?q=the").text
    assert re.search(r'<h2 class="hits-chat"><img class="avatar" src="/avatar/-?\d+\?v=\d+" alt="" width="24"', found)
    assert re.search(r'<h2 class="hits-chat"><span class="avatar tone[1-4]" aria-hidden="true">\w{1,2}</span>', found)


def test_avatar_route_keeps_the_viewer_headers(client, seeded):
    from dotiphant import viewer

    r = client.get(f"/avatar/{ACME}")
    assert r.status_code == 200
    assert r.headers[viewer.OWNER_HEADER] == "dotiphant" and r.headers[viewer.BUILD_HEADER]


def test_initials_and_stable_tone():
    from dotiphant.web.app import initials, tone

    assert initials("Birchwood <> Quillfeather Contracts") == "BQ"
    assert initials("Acme x Northwind integration") == "AN"
    assert initials("Rosalind McQuarrie") == "RM"
    assert initials("") == "?"
    assert tone(-1001234) == tone(-1001234) and 1 <= tone(42) <= 4


def test_messages_show_the_sender_photo_or_initials(client):
    page = client.get(f"/chat/{ACME}").text
    assert re.search(r'<img class="avatar who-avatar" src="/avatar/%d\?v=\d+" alt="" width="20" height="20"' % demo.MAYA, page)
    assert re.search(r'<span class="avatar tone[1-4] who-avatar" aria-hidden="true">\w{1,2}</span><span class="who', page)
    assert page.count("who-avatar") == len(re.findall(r'<span class="who ', page)), "one per sender run, not per message"


def test_search_opens_a_message_by_its_id(client):
    conn = db.connect(readonly=True)
    unique = conn.execute("SELECT chat_id, message_id FROM messages GROUP BY message_id HAVING count(*) = 1 LIMIT 1").fetchone()
    shared = conn.execute("SELECT message_id, count(*) AS n FROM messages GROUP BY message_id HAVING n > 1 LIMIT 1").fetchone()
    conn.close()

    r = client.get("/search", params={"q": f"#{unique['message_id']}"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/chat/{unique['chat_id']}?at={unique['message_id']}#m{unique['chat_id']}_{unique['message_id']}"

    page = client.get("/search", params={"q": f"#{shared['message_id']}"}).text
    assert page.count('<a class="hit"') == shared["n"], "an id used in several chats lists them all"
    assert client.get("/search", params={"q": "#999999999"}).status_code == 200


def test_a_message_id_in_place_of_a_chat_id_finds_the_message(client):
    conn = db.connect(readonly=True)
    mid = conn.execute("SELECT max(message_id) FROM messages").fetchone()[0]
    conn.close()
    r = client.get(f"/chat/{mid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/search?q=%23{mid}"
    assert client.get("/chat/999999999").status_code == 404


def test_message_refs_open_the_message(client):
    from dotiphant import query

    conn = db.connect(readonly=True)
    m = dict(conn.execute("SELECT row_id, chat_id, message_id FROM messages ORDER BY row_id DESC LIMIT 1").fetchone())
    ref = query.message_ref(m)
    assert re.fullmatch(r"dp-[0-9a-z]{2,6}", ref)
    assert query.find_ref(conn, ref)["message_id"] == m["message_id"]
    assert query.find_ref(conn, ref[3:].upper())["row_id"] == m["row_id"], "prefix and case are optional"
    typo = ref[:-1] + ("0" if ref[-1] != "0" else "1")
    assert query.find_ref(conn, typo) is None, "the check character catches a typo"
    conn.close()

    target = f"/chat/{m['chat_id']}?at={m['message_id']}#m{m['chat_id']}_{m['message_id']}"
    r = client.get(f"/m/{ref}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == target
    r = client.get("/search", params={"q": ref}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == target
    assert client.get(f"/m/{typo}").status_code == 404
    assert client.get("/m/<script>").status_code == 404


def test_a_bare_number_searches_text_first_then_message_ids(client):
    conn = db.connect(readonly=True)
    mid = conn.execute("SELECT message_id FROM messages GROUP BY message_id HAVING count(*) = 1 ORDER BY message_id DESC LIMIT 1").fetchone()[0]
    conn.close()
    page = client.get("/search", params={"q": str(mid)}).text
    assert page.count('<a class="hit"') >= 1
