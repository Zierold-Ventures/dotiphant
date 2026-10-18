"""Login tests. The real telethon QRLogin class runs against a stub client; nothing talks to Telegram."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from telethon import TelegramClient, errors, functions, types
from telethon.tl.custom.qrlogin import QRLogin

from dotiphant import config, telegram
from tests import fakes


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    # Real waits would be 15 to 60 seconds; the logic is the same at this scale.
    monkeypatch.setattr(telegram, "QR_MIN_WAIT", 0.02)
    monkeypatch.setattr(telegram, "QR_MAX_WAIT", 0.05)
    return tmp_path


def past():
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)  # a skewed local clock


def token(n=0):
    return types.auth.LoginToken(expires=past(), token=b"tok%d" % n)


def success():
    return types.auth.LoginTokenSuccess(authorization=types.auth.Authorization(user=fakes.ME))


class StubClient:
    """Just what telethon's QRLogin and dotiphant.telegram touch.

    `exports` answers auth.exportLoginToken in order (an exception is raised); once it
    runs out, every further export gets a fresh, already expired token. A truthy entry in
    `scan_during_wait` makes the matching wait() receive UpdateLoginToken, the way the
    Telegram app scanning the code would.
    """

    qr_login = TelegramClient.qr_login  # the real method: builds QRLogin and calls recreate()

    def __init__(self, exports=(), scan_during_wait=(), import_answer=None):
        self.api_id, self.api_hash = 1, "hash"
        self._no_updates = False
        self.exports = list(exports)
        self.scans = list(scan_during_wait)
        self.import_answer = import_answer
        self.requests = []
        self.handlers = []
        self.logged_in = []
        self.switched = []
        self.passwords = []
        self.connected = False
        self.authorized = False
        self.session = type("S", (), {"save": lambda self: "SESSION-STRING"})()

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return fakes.ME

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.auth.ExportLoginTokenRequest):
            answer = self.exports.pop(0) if self.exports else token(len(self.requests))
        elif isinstance(request, functions.auth.ImportLoginTokenRequest):
            answer = self.import_answer
        else:
            raise AssertionError(f"unexpected request {type(request).__name__}")
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def add_event_handler(self, callback, event=None):
        self.handlers.append(callback)
        if self.scans and self.scans.pop(0):
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(callback(None)))

    def remove_event_handler(self, callback, event=None):
        self.handlers.remove(callback)
        return 1

    async def _switch_dc(self, dc_id):
        self.switched.append(dc_id)

    async def _on_login(self, user):
        self.logged_in.append(user.id)
        self.authorized = True
        return user

    async def sign_in(self, password=None, **kw):
        self.passwords.append(password)
        self.authorized = True
        return fakes.ME


def exports(client):
    return [r for r in client.requests if isinstance(r, functions.auth.ExportLoginTokenRequest)]


@pytest.fixture
def timeouts(monkeypatch):
    """The timeout of every real QRLogin.wait() call."""
    seen = []
    real = QRLogin.wait

    async def wait(self, timeout=None):
        seen.append(timeout)
        return await real(self, timeout)

    monkeypatch.setattr(QRLogin, "wait", wait)
    return seen


async def login(client):
    urls = []
    name = await telegram.login_qr(client, urls.append, lambda: "pw")
    return name, urls


async def test_scan_during_wait_logs_in_and_saves_the_session(timeouts):
    client = StubClient(exports=[token(), success()], scan_during_wait=[True])
    name, urls = await login(client)
    assert name == "Elowen Q" and urls == ["tg://login?token=dG9rMA"]
    assert client.logged_in == [1] and client.handlers == []
    assert config.get_secret("session") == "SESSION-STRING"


async def test_expired_codes_do_not_loop_tightly_and_give_up(monkeypatch, timeouts):
    """The server's expiry is in the past by the local clock: each wait still lasts QR_MIN_WAIT."""
    client = StubClient()
    with pytest.raises(TimeoutError, match="not scanned in time"):
        await login(client)
    assert len(exports(client)) == telegram.QR_MAX_CODES, "one code per attempt, bounded"
    assert timeouts == [telegram.QR_MIN_WAIT] * telegram.QR_MAX_CODES
    assert config.get_secret("session") is None


async def test_overall_time_limit_bounds_the_codes(monkeypatch, timeouts):
    monkeypatch.setattr(telegram, "QR_TOTAL_SECONDS", 0.07)
    client = StubClient()
    with pytest.raises(TimeoutError):
        await login(client)
    assert 2 <= len(exports(client)) <= 5
    assert all(t <= telegram.QR_MIN_WAIT for t in timeouts)


async def test_scan_between_waits_is_picked_up_by_the_fresh_code(timeouts):
    """recreate() answers LoginTokenSuccess when the code was scanned while nobody waited."""
    client = StubClient(exports=[token(), success()])
    name, urls = await login(client)
    assert name == "Elowen Q" and len(urls) == 1
    assert len(exports(client)) == 2 and client.logged_in == [1]
    assert config.get_secret("session") == "SESSION-STRING"


async def test_scan_between_waits_on_another_data_center(timeouts):
    client = StubClient(exports=[token(), types.auth.LoginTokenMigrateTo(dc_id=4, token=b"moved")],
                        import_answer=success())
    name, _ = await login(client)
    assert name == "Elowen Q" and client.switched == [4] and client.logged_in == [1]
    imported = [r for r in client.requests if isinstance(r, functions.auth.ImportLoginTokenRequest)]
    assert [r.token for r in imported] == [b"moved"]
    assert config.get_secret("session") == "SESSION-STRING"


async def test_two_step_verification_raised_by_the_fresh_code(timeouts):
    client = StubClient(exports=[token(), errors.SessionPasswordNeededError(request=None)])
    name, urls = await login(client)
    assert name == "Elowen Q" and client.passwords == ["pw"] and len(urls) == 1
    assert len(exports(client)) == 2
    assert config.get_secret("session") == "SESSION-STRING"


async def test_two_step_verification_raised_by_wait(timeouts):
    client = StubClient(exports=[token(), errors.SessionPasswordNeededError(request=None)],
                        scan_during_wait=[True])
    name, _ = await login(client)
    assert name == "Elowen Q" and client.passwords == ["pw"]
    assert config.get_secret("session") == "SESSION-STRING"


async def test_login_qr_refuses_a_client_without_updates():
    client = StubClient()
    client._no_updates = True  # what make_client() builds for sync
    with pytest.raises(ValueError, match="receive_updates=True"):
        await telegram.login_qr(client, lambda url: None, lambda: "pw")
    assert client.requests == []


async def test_cli_login_builds_the_client_with_updates(monkeypatch):
    from dotiphant import cli

    made = []

    def make_client(**kw):
        made.append(kw)
        return StubClient(exports=[token(), success()], scan_during_wait=[True])

    monkeypatch.setattr(telegram, "make_client", make_client)
    monkeypatch.setattr(cli, "_print_qr", lambda url: None)
    assert await cli._login_async(None) == "Elowen Q"
    assert made == [{"receive_updates": True}]


def test_make_client_uses_stored_credentials(monkeypatch):
    monkeypatch.setenv("DOTIPHANT_API_ID", "12345")
    monkeypatch.setenv("DOTIPHANT_API_HASH", "abc")
    client = telegram.make_client()
    assert client.api_id == 12345 and not client.is_connected()
    assert client.flood_sleep_threshold == 60
    assert client._no_updates, "sync clients never receive updates"
    assert not telegram.make_client(receive_updates=True)._no_updates


async def test_logout_forgets_the_session_even_offline(monkeypatch):
    config.set_secret("session", "x")
    monkeypatch.delenv("DOTIPHANT_API_ID", raising=False)
    monkeypatch.delenv("DOTIPHANT_API_HASH", raising=False)
    await telegram.logout()  # no API credentials: remote logout fails, local session is removed
    assert config.get_secret("session") is None
