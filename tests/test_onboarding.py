"""Browser setup (/setup) against a fake Telegram login, a fake connect and a fake schedule."""
from __future__ import annotations

import asyncio
import logging
import time

import pytest
from starlette.testclient import TestClient
from telethon import errors

from dotiphant import cli, config, connect, db, demo, schedule, telegram, viewer
from dotiphant import status as sync_status
from dotiphant.web import app as webapp
from dotiphant.web import onboarding

BASE = "http://127.0.0.1:8765"
SAME = {"Origin": BASE}
HASH = "0123456789abcdef0123456789abcdef"
PASSWORD = "correct horse battery"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    monkeypatch.delenv("DOTIPHANT_API_ID", raising=False)
    monkeypatch.delenv("DOTIPHANT_API_HASH", raising=False)
    webapp._job.clear()
    return tmp_path


def open_client(c: TestClient) -> TestClient:
    r = c.get(f"/setup?key={webapp.viewer_token()}", follow_redirects=False)
    assert r.status_code == 303
    c.headers["X-Dotiphant-Token"] = c.app.state.csrf_token  # what app.js reads from the page
    return c


@pytest.fixture
def client(home):
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        yield open_client(c)


@pytest.fixture
def creds(home):
    config.set_secret("api_id", "1234567")
    config.set_secret("api_hash", HASH)


class Scenario:
    """What the fake Telegram does: scan the QR code, ask for a password, fail."""

    def __init__(self):
        self.scanned = False
        self.password = None  # the 2FA password the account has, or None
        self.fail: BaseException | None = None
        self.urls: list[str] = []
        self.saved = 0
        self.disconnected = 0
        self.phones: list[str] = []
        self.account_id = 1
        self.logged_out = 0


class FakeClient:
    def __init__(self, sc: Scenario):
        self.sc = sc

    async def disconnect(self):
        self.sc.disconnected += 1

    async def log_out(self):
        self.sc.logged_out += 1
        return True


@pytest.fixture
def tg(monkeypatch, creds):
    sc = Scenario()

    def make_client(receive_updates=False):
        assert receive_updates is True
        config.api_credentials()  # raises like the real one without credentials
        return FakeClient(sc)

    async def check_password(get_password):
        for _ in range(telegram.MAX_ATTEMPTS):
            if await get_password() == sc.password:
                return
        raise errors.PasswordHashInvalidError(request=None)

    async def login_qr(client, show_url, get_password):
        if sc.fail:
            raise sc.fail
        n = 0
        while not sc.scanned:
            show_url(f"tg://login?token=fake{n}")
            sc.urls.append(f"tg://login?token=fake{n}")
            n += 1
            await asyncio.sleep(0.02)
        if sc.password:
            await check_password(get_password)
        return "Elowen Q"

    async def login_phone(client, phone, get_code, get_password):
        sc.phones.append(phone)
        if sc.fail:
            raise sc.fail
        for _ in range(telegram.MAX_ATTEMPTS):
            if await get_code() == "12345":
                break
        else:
            raise errors.PhoneCodeInvalidError(request=None)
        if sc.password:
            await check_password(get_password)
        return "Elowen Q"

    def save_session(client):
        sc.saved += 1
        config.set_secret("session", "fake-session")

    monkeypatch.setattr(telegram, "make_client", make_client)
    monkeypatch.setattr(telegram, "login_qr", login_qr)
    monkeypatch.setattr(telegram, "login_phone", login_phone)
    async def account_id(client):
        return sc.account_id

    monkeypatch.setattr(telegram, "save_session", save_session)
    monkeypatch.setattr(telegram, "account_id", account_id)
    return sc


def wait_for(c: TestClient, *states: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        s = c.get("/api/setup/login").json()
        if s["state"] in states:
            return s
        if time.monotonic() > deadline:
            raise AssertionError(f"login stayed {s}")
        time.sleep(0.02)


def start(c: TestClient, **body) -> dict:
    r = c.post("/api/setup/login", json={"action": "start", **body}, headers=SAME)
    assert r.status_code == 200, r.text
    return r.json()


# --- access ------------------------------------------------------------------------

def test_setup_needs_the_viewer_key(home):
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        assert c.get("/setup").status_code == 401
        assert c.get("/api/setup/login").status_code == 401
        r = c.post("/api/setup/credentials", json={"api_id": "1234567", "api_hash": HASH}, headers=SAME)
        assert r.status_code == 401
    assert config.get_secret("api_id") is None


def test_posts_need_same_origin(client):
    for url, body in (("/api/setup/credentials", {"api_id": "1234567", "api_hash": HASH}),
                      ("/api/setup/login", {"action": "start"}), ("/api/setup/settings", {}),
                      ("/api/setup/sync", {}), ("/api/setup/connect", {"name": "cursor"}),
                      ("/api/setup/schedule", {"on": True})):
        assert client.post(url, json=body).status_code == 403
        assert client.post(url, json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert config.get_secret("api_id") is None


def test_setup_is_off_in_the_demo(home, monkeypatch):
    demo.seed_demo(home)
    monkeypatch.setattr(schedule, "install", lambda *a, **k: pytest.fail("no schedule in the demo"))
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        open_client(c)
        r = c.get("/setup")
        assert r.status_code == 409 and "not available in the demo" in r.text
        assert c.get("/").status_code == 200  # no redirect to setup
        assert c.get("/api/setup/login").status_code == 409
        for url, body in (("/api/setup/credentials", {"api_id": "1234567", "api_hash": HASH}),
                          ("/api/setup/login", {"action": "start"}), ("/api/setup/sync", {}),
                          ("/api/setup/schedule", {"on": True})):
            r = c.post(url, json=body, headers=SAME)
            assert r.status_code == 409 and r.json()["demo"] is True
    assert config.get_secret("api_id") is None


# --- home redirect -----------------------------------------------------------------

def test_fresh_install_goes_to_setup(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_logged_in_install_stays_home(client, creds):
    config.set_secret("session", "s")
    assert client.get("/", follow_redirects=False).status_code == 200


def test_archive_with_chats_stays_home_without_login(home, monkeypatch):
    demo.seed_demo(home)  # like an imported archive
    monkeypatch.setattr(webapp, "is_demo", lambda: False)
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        open_client(c)
        assert c.get("/", follow_redirects=False).status_code == 200
        assert c.get("/setup").status_code == 200  # still reachable by hand


# --- step 1: credentials -----------------------------------------------------------

def test_credentials_are_validated_and_saved(client):
    page = client.get("/setup")
    assert page.status_code == 200 and "my.telegram.org" in page.text and 'target="_blank"' in page.text
    for body, field in (({"api_id": "abc", "api_hash": HASH}, "api_id"),
                        ({"api_id": "1234567", "api_hash": "xyz"}, "api_hash"),
                        ({"api_id": "1234567", "api_hash": HASH + "0"}, "api_hash"),
                        ({"api_id": ["1"], "api_hash": HASH}, "api_id")):
        r = client.post("/api/setup/credentials", json=body, headers=SAME)
        assert r.status_code == 400 and r.json()["field"] == field
    assert config.get_secret("api_id") is None
    r = client.post("/api/setup/credentials", json={"api_id": " 9876543 ", "api_hash": HASH.upper()}, headers=SAME)
    assert r.status_code == 200 and r.json()["saved"] is True and r.json()["replaced"] is False
    assert HASH not in r.text.lower()
    assert config.api_credentials() == (9876543, HASH)
    page = client.get("/setup?step=credentials")
    assert "Replace them" in page.text and HASH not in page.text.lower() and "9876543" not in page.text
    assert client.get("/setup").text.count('aria-current="step"') == 1
    assert 'data-step="login"' in client.get("/setup").text


def test_replacing_credentials_forgets_the_login(client, creds, monkeypatch):
    config.set_secret("session", "old-session")
    ended = []

    async def logout():  # the old login is ended on Telegram first (best effort)
        ended.append(config.api_credentials())
        config.delete_secret("session")
        return True

    monkeypatch.setattr(telegram, "logout", logout)
    # The same values again keep the login.
    r = client.post("/api/setup/credentials", json={"api_id": "1234567", "api_hash": HASH}, headers=SAME)
    assert r.status_code == 200 and not ended and config.get_secret("session") == "old-session"
    new = "f" * 32
    r = client.post("/api/setup/credentials", json={"api_id": "7654321", "api_hash": new}, headers=SAME)
    assert r.json()["replaced"] is True and new not in r.text
    assert ended == [(1234567, HASH)]  # with the old app's credentials, before they were replaced
    assert config.api_credentials() == (7654321, new)
    assert config.get_secret("session") is None
    overview = client.get("/api/setup").json()
    assert overview["credentials"] is True and overview["logged_in"] is False and overview["step"] == "login"
    assert new not in str(overview) and "7654321" not in str(overview)


# --- step 2: login -----------------------------------------------------------------

def test_qr_login(client, tg):
    page = client.get("/setup")
    assert 'data-step="login"' in page.text and "Link Desktop Device" in page.text
    assert client.get("/api/setup/login").json() == {"state": "idle"}
    start(client, method="qr")
    s = wait_for(client, "waiting_scan")
    assert s["qr_svg_url"].startswith("/api/setup/login/qr.svg") and s["expires_at"] > time.time()
    assert "tg://" not in str(s)
    svg = client.get(s["qr_svg_url"])
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.text.startswith("<svg") and "<script" not in svg.text and "<path" in svg.text
    # A reload (or second tab) keeps the running login instead of starting another one.
    again = start(client, method="qr")
    assert again["state"] == "waiting_scan" and len(client.app.state.setup.starts.hits) == 1
    tg.scanned = True
    done = wait_for(client, "done")
    assert done["name"] == "Elowen Q"
    assert tg.saved >= 1 and tg.disconnected == 1
    assert config.get_secret("session") == "fake-session"
    assert client.get("/api/setup/login/qr.svg").status_code == 404
    assert client.get("/api/setup").json()["step"] == "archive"


def test_qr_login_with_password(client, tg, caplog):
    caplog.set_level(logging.DEBUG)
    tg.password = PASSWORD
    tg.scanned = True
    start(client)
    wait_for(client, "need_password")
    r = client.post("/api/setup/login", json={"action": "password", "password": "wrong"}, headers=SAME)
    assert r.status_code == 200
    s = wait_for(client, "need_password")
    assert "not right" in (s["hint"] or "")
    r = client.post("/api/setup/login", json={"action": "password", "password": PASSWORD}, headers=SAME)
    assert PASSWORD not in r.text
    assert wait_for(client, "done")["name"] == "Elowen Q"
    assert PASSWORD not in caplog.text and "wrong" not in caplog.text
    # Nothing is waiting for a password any more.
    r = client.post("/api/setup/login", json={"action": "password", "password": PASSWORD}, headers=SAME)
    assert r.status_code == 409 and PASSWORD not in r.text


def test_too_many_wrong_passwords_is_a_kind_error(client, tg):
    tg.password = PASSWORD
    tg.scanned = True
    start(client)
    for _ in range(telegram.MAX_ATTEMPTS):
        wait_for(client, "need_password")
        client.post("/api/setup/login", json={"action": "password", "password": "nope"}, headers=SAME)
    s = wait_for(client, "error")
    assert "password" in s["error"] and s["back_to"] == "login"


def test_phone_login_with_code_and_password(client, tg):
    tg.password = PASSWORD
    r = client.post("/api/setup/login", json={"action": "start", "method": "phone", "phone": "abc"}, headers=SAME)
    assert r.status_code == 400 and r.json()["field"] == "phone"
    s = start(client, method="phone", phone="+49 151 1234-5678")
    assert "151" not in str(s)
    wait_for(client, "need_code")
    assert tg.phones == ["+4915112345678"]
    client.post("/api/setup/login", json={"action": "code", "code": "11111"}, headers=SAME)
    assert "not right" in wait_for(client, "need_code")["hint"]
    client.post("/api/setup/login", json={"action": "code", "code": "12 345"}, headers=SAME)
    wait_for(client, "need_password")
    client.post("/api/setup/login", json={"action": "password", "password": PASSWORD}, headers=SAME)
    assert wait_for(client, "done")["name"] == "Elowen Q"


def test_invalid_api_id_sends_back_to_step_one(client, tg):
    tg.fail = errors.ApiIdInvalidError(request=None)
    start(client)
    s = wait_for(client, "error")
    assert s["back_to"] == "credentials" and "API id and hash" in s["error"]
    assert "caused by" not in s["error"]
    # The same rule as `dotiphant setup`: rejected credentials and their session are deleted.
    assert "removed them" in s["error"]
    assert config.get_secret("api_id") is None and config.get_secret("api_hash") is None
    assert config.get_secret("session") is None
    assert client.get("/api/setup").json()["step"] == "credentials"


def test_login_as_another_account_is_undone(client, tg):
    db.init()
    conn = db.open_rw()
    db.set_meta(conn, "owner_id", "5")  # the archive was synced by account 5; the fake is account 1
    conn.commit()
    conn.close()
    tg.scanned = True
    start(client, method="qr")
    s = wait_for(client, "error")
    assert s["error"] == db.OTHER_ACCOUNT and s["back_to"] == "login"
    assert tg.logged_out == 1 and config.get_secret("session") is None
    assert client.get("/api/setup").json()["step"] == "login"


def test_login_without_credentials(client):
    r = client.post("/api/setup/login", json={"action": "start"}, headers=SAME)
    assert r.status_code == 409 and r.json()["back_to"] == "credentials"


def test_cancel_and_restart(client, tg):
    start(client)
    wait_for(client, "waiting_scan")
    first = client.app.state.setup.login
    r = client.post("/api/setup/login", json={"action": "cancel"}, headers=SAME)
    assert r.json() == {"state": "idle"}
    assert first.task.done() and tg.disconnected == 1
    assert client.get("/api/setup/login").json() == {"state": "idle"}
    start(client, restart=True)
    wait_for(client, "waiting_scan")
    assert client.app.state.setup.login is not first


def test_login_times_out(client, tg, monkeypatch):
    monkeypatch.setattr(onboarding, "LOGIN_TIMEOUT", 0.1)
    start(client)
    s = wait_for(client, "error")
    assert "timed out" in s["error"] and tg.disconnected == 1


def test_login_starts_are_rate_limited(client, tg):
    count = onboarding.START_LIMIT[0]
    for _ in range(count):
        start(client, restart=True)
    r = client.post("/api/setup/login", json={"action": "start", "restart": True}, headers=SAME)
    assert r.status_code == 429


# --- step 3: what to archive -------------------------------------------------------

def test_archive_settings(client, creds):
    config.set_secret("session", "s")
    page = client.get("/setup")
    assert 'data-step="archive"' in page.text and "Channels" in page.text
    r = client.post("/api/setup/settings", json={"people": True, "groups": False, "channels": True,
                                                 "history_days": 30}, headers=SAME)
    assert r.status_code == 200
    s = config.Settings.load()
    assert (s.include_private, s.include_groups, s.include_channels, s.history_days) == (True, False, True, 30)
    client.post("/api/setup/settings", json={"people": True, "history_days": "all"}, headers=SAME)
    assert config.Settings.load().history_days is None
    for bad in ("soon", 0, -5):
        r = client.post("/api/setup/settings", json={"history_days": bad}, headers=SAME)
        assert r.status_code == 400
    assert 'data-step="sync"' in client.get("/setup").text
    assert config.Settings.load().ai_access == "all"  # not sent: unchanged


def test_setup_asks_which_chats_ai_apps_may_read(client, creds):
    config.set_secret("session", "s")
    page = client.get("/setup?step=archive").text
    assert "Which chats may AI apps read?" in page and 'value="selected"' in page and "dotiphant ai show" in page
    r = client.post("/api/setup/settings", json={"people": True, "history_days": 30, "ai_access": "selected"},
                    headers=SAME)
    assert r.status_code == 200 and config.Settings.load().ai_access == "selected"
    r = client.post("/api/setup/settings", json={"people": True, "history_days": 30, "ai_access": "some"},
                    headers=SAME)
    assert r.status_code == 400 and config.Settings.load().ai_access == "selected"


# --- step 4: first sync ------------------------------------------------------------

def test_first_sync_starts_the_detached_job(client, creds, monkeypatch):
    calls = []

    class FakeProc:
        pid = 4242

        def __init__(self, args, **kwargs):
            calls.append(args)

        def poll(self):
            return None

    monkeypatch.setattr(sync_status.subprocess, "Popen", FakeProc)
    r = client.post("/api/setup/sync", headers=SAME)
    assert r.status_code == 409 and r.json()["back_to"] == "login"  # not logged in yet
    config.set_secret("session", "s")
    config.Settings().save()
    r = client.post("/api/setup/sync", headers=SAME)
    assert r.status_code == 200 and r.json()["started"] is True
    assert calls[0][1:] == ["-m", "dotiphant", "sync", "--quiet"]
    assert client.get("/api/status").json()["running"] is True
    page = client.get("/setup?step=sync")
    assert "Continue while it runs" in page.text and "<progress" in page.text


# --- step 5: connect and background refresh -----------------------------------------

def test_connect_and_schedule(client, creds, monkeypatch):
    config.set_secret("session", "s")
    config.Settings().save()
    (config.data_dir() / "status.json").write_text('{"state": "done", "finished_at": "2026-10-01T00:00:00+00:00"}')
    monkeypatch.setattr(connect, "detect", lambda: ["cursor", "claude-desktop"])
    connected = []
    monkeypatch.setattr(connect, "connect", lambda name, dry_run=False: connected.append(name) or f"Added to {name}.")
    installed = {"on": False}
    monkeypatch.setattr(schedule, "status", lambda **k: {"installed": installed["on"], "every_minutes": 15})

    def install(every=None, **k):
        installed["on"] = True
        return "Background refresh every 15 minutes."

    def remove(**k):
        installed["on"] = False
        return "Background refresh removed."

    monkeypatch.setattr(schedule, "install", install)
    monkeypatch.setattr(schedule, "remove", remove)

    page = client.get("/setup")
    assert 'data-step="connect"' in page.text
    assert 'data-connect="cursor"' in page.text and 'data-connect="codex"' not in page.text
    assert "not found on this computer" in page.text and "every 15 minutes" in page.text
    assert "remote-access.md" in page.text and 'href="/connect"' in page.text
    r = client.post("/api/setup/connect", json={"name": "cursor"}, headers=SAME)
    assert r.json() == {"ok": True, "message": "Added to cursor."} and connected == ["cursor"]
    assert client.post("/api/setup/connect", json={"name": "../evil"}, headers=SAME).status_code == 400
    monkeypatch.setattr(connect, "connect", lambda name, dry_run=False: (_ for _ in ()).throw(OSError("disk")))
    r = client.post("/api/setup/connect", json={"name": "codex"}, headers=SAME)
    assert r.json()["ok"] is False and "--print" in r.json()["message"]

    r = client.post("/api/setup/schedule", json={"on": True}, headers=SAME)
    assert r.json()["ok"] is True and installed["on"] is True and r.json()["status"]["installed"] is True
    r = client.post("/api/setup/schedule", json={"on": False}, headers=SAME)
    assert r.json()["message"] == "Background refresh removed." and installed["on"] is False


# --- CLI ---------------------------------------------------------------------------

def test_cli_setup_web_opens_the_setup_page(home, monkeypatch, capsys):
    import uvicorn

    monkeypatch.setattr(uvicorn.Server, "run", lambda self: None)
    monkeypatch.setattr(viewer, "port_is_free", lambda port: True)
    monkeypatch.setattr(webapp.webbrowser, "open", lambda url: pytest.fail("no browser"))
    assert cli.main(["setup", "--web", "--no-browser", "--port", "8899"]) == 0
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:8899/setup?key={webapp.viewer_token()}" in out


def test_terminal_setup_mentions_the_browser(home, monkeypatch, capsys):
    monkeypatch.setattr(cli, "step_credentials", lambda args: (_ for _ in ()).throw(cli.CliError("stop")))
    cli.main(["setup", "--yes"])
    assert "Prefer a browser? Run `dotiphant open`." in capsys.readouterr().out


def test_setup_page_accessibility_and_api_notice(client, creds):
    r = client.get("/setup?step=login")
    assert r.status_code == 200
    assert "<title>Log in: Set up Dotiphant</title>" in r.text
    assert "Dotiphant is an unofficial app that uses the Telegram API. It is not made by Telegram." in r.text
    assert 'aria-current="step"' in r.text
    assert 'alt="Login QR code.' in r.text and 'data-login-method="phone"' in r.text  # QR has a text alternative
    assert '<input type="password" name="password"' in r.text and "<label>Password" in r.text
    assert 'class="form-error" data-error role="alert"' in r.text
    assert "lens-art" not in r.text  # no drawings on login and key screens
    assert r.text.count('role="status" aria-live="polite"') == 1


def test_setup_page_has_the_theme_footer(client):
    r = client.get("/setup")
    assert r.status_code == 200
    assert r.text.count('<footer class="foot">') == 1
    assert "data-theme-switch" in r.text and '<legend class="sr">Theme</legend>' in r.text
    assert '<script src="/static/theme.js"></script>' in r.text.split("</head>", 1)[0]
    assert "unsafe-inline" not in r.headers["content-security-policy"]
