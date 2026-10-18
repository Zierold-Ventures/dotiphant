"""Telegram client factory and login.

Dotiphant logs in as the user's own account over MTProto. The session is a Telethon
StringSession kept in the OS keychain (see config.py), never in a file next to the data.
Nothing here logs phone numbers, login codes, passwords or the session string.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import logging
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, errors, functions, types, utils
from telethon.sessions import StringSession

from . import config

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
QR_MIN_WAIT = 15  # seconds to wait for a scan, whatever the local clock says
QR_MAX_WAIT = 60
QR_MAX_CODES = 10  # fresh QR codes per login attempt
QR_TOTAL_SECONDS = 300  # then login_qr gives up with TimeoutError


def _app_version() -> str:
    try:
        import dotiphant

        return str(getattr(dotiphant, "__version__", None) or "0.1")
    except Exception:
        return "0.1"


def make_client(receive_updates: bool = False) -> TelegramClient:
    """A client with the stored session (empty when not logged in yet).

    Sync and media clients never need updates. The login client does: QR login completes
    through an UpdateLoginToken update, which Telegram only delivers on a connection that
    was not opened with InvokeWithoutUpdates. Updates must be chosen here, at construction:
    turning them on later (set_receive_updates) sends updates.getState, which fails with
    AUTH_KEY_UNREGISTERED before the account is logged in.
    """
    api_id, api_hash = config.api_credentials()
    session = StringSession(config.get_secret("session") or "")
    return TelegramClient(
        session,
        api_id,
        api_hash,
        device_model="Dotiphant",
        app_version=_app_version(),
        receive_updates=receive_updates,
        flood_sleep_threshold=60,
    )


async def is_authorized(client: TelegramClient) -> bool:
    if not client.is_connected():
        await client.connect()
    return bool(await client.is_user_authorized())


def save_session(client: TelegramClient) -> None:
    """Store the session string in the keychain (or the 0600 file fallback)."""
    value = client.session.save()
    if value:
        config.set_secret("session", value)


async def account_id(client: TelegramClient) -> int | None:
    """The logged-in user's id, or None when Telegram does not say."""
    me = await client.get_me()
    return utils.get_peer_id(me) if me is not None else None


def display_name(user: Any) -> str:
    if user is None:
        return "Unknown"
    name = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p)
    if name:
        return name
    if getattr(user, "username", None):
        return "@" + user.username
    return "Telegram user"


async def _ask(fn: Callable[[], str | Awaitable[str]]) -> str:
    value = fn()
    if inspect.isawaitable(value):
        value = await value
    return value


async def _finish_with_password(client: TelegramClient, get_password: Callable[[], str]) -> Any:
    """Two-step verification: ask for the cloud password, a few attempts at most."""
    for attempt in range(MAX_ATTEMPTS):
        password = await _ask(get_password)
        try:
            return await client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
    return None


async def _done(client: TelegramClient, user: Any | None) -> str:
    if user is None or not hasattr(user, "first_name"):
        user = await client.get_me()
    save_session(client)
    return display_name(user)


async def _qr_result(client: TelegramClient, resp: Any) -> Any | None:
    """The user for an auth.ExportLoginToken answer that means "scanned", else None.

    Mirrors QRLogin.wait(): LoginTokenMigrateTo means the account lives on another data
    center, so switch to it and import the token there.
    """
    if isinstance(resp, types.auth.LoginTokenMigrateTo):
        await client._switch_dc(resp.dc_id)
        resp = await client(functions.auth.ImportLoginTokenRequest(resp.token))
    if isinstance(resp, types.auth.LoginTokenSuccess):
        user = resp.authorization.user
        await client._on_login(user)
        return user
    if isinstance(resp, types.auth.LoginToken):
        return None
    raise TypeError(f"Unexpected QR login answer: {type(resp).__name__}")


def _qr_wait_seconds(expires: Any) -> float:
    """How long to wait for a scan before asking for a fresh code.

    QRLogin.wait() waits until the server's expiry by the local clock; a skewed clock
    makes that zero or negative, which would ask for new codes in a tight loop.
    """
    try:
        left = (expires - dt.datetime.now(dt.timezone.utc)).total_seconds()
    except Exception:
        left = QR_MAX_WAIT
    return max(QR_MIN_WAIT, min(QR_MAX_WAIT, left))


async def login_qr(
    client: TelegramClient,
    show_url: Callable[[str], None],
    get_password: Callable[[], str],
) -> str:
    """Log in by scanning a QR code with the Telegram app (Settings > Devices > Link Desktop Device).

    show_url is called with a tg://login URL each time a fresh code is issued; the caller
    renders it as a QR code. Returns the account's display name. Raises TimeoutError when
    no code was scanned within QR_TOTAL_SECONDS (at most QR_MAX_CODES codes are issued).
    The client must come from make_client(receive_updates=True), see there.

    A scan can land while no wait() is listening (between two codes). Asking for a fresh
    code then answers "logged in" (or "migrate to another data center", or raises
    SessionPasswordNeededError with two-step verification), so every request for a code
    goes through the same handling as wait().
    """
    if getattr(client, "_no_updates", False):
        raise ValueError("QR login needs a client made with make_client(receive_updates=True).")
    if not client.is_connected():
        await client.connect()
    if await client.is_user_authorized():
        return await _done(client, None)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QR_TOTAL_SECONDS
    user = None
    try:
        qr = await client.qr_login()
        for issued in range(1, QR_MAX_CODES + 1):
            user = await _qr_result(client, qr._resp)
            if user is not None:
                break
            show_url(qr.url)
            timeout = min(_qr_wait_seconds(qr.expires), max(0.0, deadline - loop.time()))
            try:
                user = await qr.wait(timeout=timeout)
                break
            except (asyncio.TimeoutError, TimeoutError):
                pass
            if issued == QR_MAX_CODES or loop.time() >= deadline:
                break
            await qr.recreate()
    except errors.SessionPasswordNeededError:
        user = await _finish_with_password(client, get_password)
    if user is None:
        raise TimeoutError("The QR code was not scanned in time. Start the login again to get a new code.")
    return await _done(client, user)


async def login_phone(
    client: TelegramClient,
    phone: str,
    get_code: Callable[[], str],
    get_password: Callable[[], str],
) -> str:
    """Log in with a code that Telegram sends to the app (or by SMS). Returns the display name."""
    if not client.is_connected():
        await client.connect()
    if await client.is_user_authorized():
        return await _done(client, None)
    sent = await client.send_code_request(phone)
    user = None
    for attempt in range(MAX_ATTEMPTS):
        code = (await _ask(get_code)).strip().replace(" ", "").replace("-", "")
        try:
            user = await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            break
        except errors.SessionPasswordNeededError:
            user = await _finish_with_password(client, get_password)
            break
        except errors.PhoneCodeInvalidError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
    return await _done(client, user)


async def logout() -> bool:
    """End the session on Telegram's side, then delete the stored session either way.

    Returns True when Telegram confirmed the logout or there was no live session to end,
    False when Telegram could not be reached (offline, missing credentials).
    """
    try:
        if not config.get_secret("session"):
            return True
        client = make_client()
        await client.connect()
        try:
            if await client.is_user_authorized():
                return bool(await client.log_out())
            return True  # Telegram already ended this session
        finally:
            if client.is_connected():
                await client.disconnect()
    except Exception as exc:  # offline, missing credentials
        log.warning("Remote logout failed (%s); removing the local session anyway.", type(exc).__name__)
        return False
    finally:
        config.delete_secret("session")
