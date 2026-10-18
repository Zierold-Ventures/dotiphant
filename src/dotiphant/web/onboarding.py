"""Browser setup at /setup: the same steps as `dotiphant setup`, without a terminal.

1. Telegram API id and hash (saved with config.set_secret, never sent back to the page).
2. Login by QR code (default) or phone code. A background task in the viewer's event loop
   holds the Telethon client; the page polls GET /api/setup/login and posts the password or
   code when asked. One login runs at a time, survives page reloads and stops after 5 minutes.
3. What to archive, and which chats AI apps may read (Settings).
4. First sync, the same detached `dotiphant sync --quiet` the refresh button starts.
5. Connect AI apps and turn on the background refresh.

All POSTs go through the viewer key (ViewerAuth) and the same-origin check, and all of it is
off in the demo. Passwords, codes, phone numbers and the API hash are never logged or echoed.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import time
from collections import deque
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .. import config, db
from .. import status as sync_status
from . import app as web

REMOTE_DOCS = "docs/remote-access.md in the Dotiphant repository on GitHub"
LOGIN_TIMEOUT = 300  # seconds a login task may run
QR_LIFETIME = 30  # Telegram login tokens last about this long; the task replaces them itself
START_LIMIT = (8, 600)  # at most 8 login starts per 10 minutes
ANSWER_LIMIT = (10, 600)  # at most 10 passwords or codes per 10 minutes
ACTIVE = ("starting", "waiting_scan", "need_password", "need_code", "checking")
STEPS = ("credentials", "login", "archive", "sync", "connect")


def credentials_from_env() -> bool:
    return bool(os.environ.get("DOTIPHANT_API_ID") and os.environ.get("DOTIPHANT_API_HASH"))


def needs_setup() -> bool:
    """True when Telegram is not connected yet (no credentials or no login)."""
    return not config.has_credentials() or not config.has_session()


def qr_svg(url: str) -> bytes:
    """The QR code as SVG markup we generate ourselves (black modules on white)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage, border=2)
    out = io.BytesIO()
    img.save(out)
    svg = out.getvalue()
    return svg[svg.index(b"<svg"):]  # drop the XML declaration


# --- rate limits -------------------------------------------------------------------

class Limiter:
    def __init__(self, count: int, seconds: float):
        self.count, self.seconds = count, seconds
        self.hits: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self.hits and now - self.hits[0] > self.seconds:
            self.hits.popleft()
        if len(self.hits) >= self.count:
            return False
        self.hits.append(now)
        return True


# --- the login task ----------------------------------------------------------------

def _error_text(exc: BaseException) -> tuple[str, str]:
    """A kind message for a login failure, and which step to go back to. Never str(exc)."""
    name = type(exc).__name__
    if config.credentials_rejected(exc):
        if credentials_from_env():
            return ("Telegram did not accept the API id and hash in DOTIPHANT_API_ID and DOTIPHANT_API_HASH. "
                    "Fix those variables and restart `dotiphant open`.", "credentials")
        return ("Telegram did not accept the API id and hash, so Dotiphant removed them. Copy both again "
                "from my.telegram.org in step 1.", "credentials")
    if isinstance(exc, config.SecretsUnavailable):
        return "Save your API id and hash first (step 1).", "credentials"
    if "PasswordHashInvalid" in name:
        return "That password was not right, and there were too many tries. Start again.", "login"
    if "PhoneCodeInvalid" in name:
        return "That code was not right, and there were too many tries. Start again.", "login"
    if "PhoneCodeExpired" in name:
        return "The code expired. Start again to get a new one.", "login"
    if "PhoneNumberInvalid" in name or "PhoneNumberUnoccupied" in name:
        return "Telegram does not know that phone number. Use the international format, like +4915112345678.", "login"
    if "PhoneNumberBanned" in name:
        return "Telegram says this phone number is banned.", "login"
    if "FloodWait" in name or "Flood" in name:
        seconds = getattr(exc, "seconds", None)
        wait = f" for {seconds} seconds" if seconds else " for a while"
        return f"Telegram asks to wait{wait} before trying again.", "login"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "The login timed out after 5 minutes. Start again when you are ready.", "login"
    if isinstance(exc, (ConnectionError, OSError)):
        return "Could not reach Telegram. Check your internet connection and start again.", "login"
    return f"The login failed ({name}). Start again, or try the other login method.", "login"


class LoginSession:
    """One login attempt, run as an asyncio task on the viewer's event loop."""

    def __init__(self, method: str, phone: str | None = None):
        self.method = method
        self._phone = phone
        self.state = "starting"
        self.qr_url: str | None = None
        self.qr_version = 0
        self.expires_at: float | None = None
        self.name: str | None = None
        self.error: str | None = None
        self.back_to: str | None = None
        self.hint: str | None = None  # "wrong password" and similar, while still waiting
        self.started = time.time()
        self._answer: asyncio.Future | None = None
        self._asked = {"password": 0, "code": 0}
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.get_running_loop().create_task(self._run())

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    def view(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state, "method": self.method, "hint": self.hint}
        if self.state == "waiting_scan" and self.qr_url:
            out["qr_svg_url"] = f"/api/setup/login/qr.svg?v={self.qr_version}"
            out["expires_at"] = int(self.expires_at or 0)
        if self.state == "done":
            out["name"] = self.name
        if self.state == "error":
            out["error"] = self.error
            out["back_to"] = self.back_to
        return out

    def answer(self, kind: str, value: str) -> bool:
        """Hand the password or code to the waiting login. False when nothing asked for it."""
        want = "need_password" if kind == "password" else "need_code"
        fut = self._answer
        if self.state != want or fut is None or fut.done():
            return False
        fut.set_result(value)
        self.state = "checking"
        self.hint = None
        return True

    async def _ask(self, kind: str) -> str:
        self._asked[kind] += 1
        if self._asked[kind] > 1:
            self.hint = "That password was not right. Try again." if kind == "password" else \
                "That code was not right. Try again."
        self._answer = asyncio.get_running_loop().create_future()
        self.state = "need_password" if kind == "password" else "need_code"
        try:
            return await self._answer
        finally:
            self._answer = None

    def _show_url(self, url: str) -> None:
        self.qr_url = url
        self.qr_version += 1
        self.expires_at = time.time() + QR_LIFETIME
        self.state = "waiting_scan"
        self.hint = None

    async def _run(self) -> None:
        from .. import telegram as tg

        client = None
        try:
            client = tg.make_client(receive_updates=True)
            if self.method == "phone":
                phone, self._phone = self._phone, None
                coro = tg.login_phone(client, phone, lambda: self._ask("code"), lambda: self._ask("password"))
            else:
                coro = tg.login_qr(client, self._show_url, lambda: self._ask("password"))
            name = await asyncio.wait_for(coro, LOGIN_TIMEOUT)
            tg.save_session(client)
            if await asyncio.to_thread(db.other_account, await tg.account_id(client)):
                # Undo this login, so the user can start again with the right account.
                with contextlib.suppress(Exception):
                    await client.log_out()
                await asyncio.to_thread(config.delete_secret, "session")
                self.error, self.back_to = db.OTHER_ACCOUNT, "login"
                self.state = "error"
                return
            self.name = name
            self.state = "done"
        except asyncio.CancelledError:
            self.state = "cancelled"
            raise
        except BaseException as exc:  # noqa: BLE001 - every failure becomes a kind message
            if config.credentials_rejected(exc):
                # The same rule as `dotiphant setup`: rejected credentials and their session are deleted.
                await asyncio.to_thread(config.forget_credentials)
            self.error, self.back_to = _error_text(exc)
            self.state = "error"
        finally:
            self.qr_url = None
            self.hint = None
            if client is not None:
                with contextlib.suppress(BaseException):
                    result = client.disconnect()
                    if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                        await result

    async def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task
        self.state = "cancelled"


class SetupState:
    """Kept on app.state: the current login and the rate limiters."""

    def __init__(self):
        self.login: LoginSession | None = None
        self.starts = Limiter(*START_LIMIT)
        self.answers = Limiter(*ANSWER_LIMIT)


def _state(request: Request) -> SetupState:
    st = getattr(request.app.state, "setup", None)
    if st is None:
        st = request.app.state.setup = SetupState()
    return st


# --- helpers -----------------------------------------------------------------------

def _refuse(request: Request) -> JSONResponse | None:
    if request.method == "POST" and not web.same_origin(request):
        return web.forbidden()
    if web.is_demo():
        return JSONResponse({"error": web.DEMO_NOTE, "demo": True}, status_code=409)
    return None


def _error(message: str, status: int = 400, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _settings_saved() -> bool:
    return (config.data_dir() / "settings.json").exists()


def _chat_count() -> int:
    try:
        with web.reader() as conn:
            return conn.execute("SELECT count(*) FROM chats").fetchone()[0]
    except Exception:
        return 0


def _client_labels() -> list[dict[str, Any]]:
    try:
        from .. import connect as connect_mod
    except Exception:
        return []
    try:
        found = set(connect_mod.detect())
    except Exception:
        found = set()
    return [{"key": k, "label": c.label, "found": k in found} for k, c in connect_mod.CLIENTS.items()]


def _schedule_status() -> dict[str, Any]:
    try:
        from .. import schedule

        return schedule.status()
    except Exception:
        return {"installed": None}


def overview() -> dict[str, Any]:
    """Which steps are done. Contains no secrets."""
    creds = config.has_credentials()
    session = config.has_session()
    status = sync_status.read_status()
    chats = _chat_count()
    s = config.Settings.load()
    return {
        "credentials": creds,
        "credentials_from_env": credentials_from_env(),
        "logged_in": creds and session,
        "settings_saved": _settings_saved(),
        "settings": {
            "people": s.include_private, "groups": s.include_groups, "channels": s.include_channels,
            "history_days": s.history_days, "ai_access": s.ai_access,
        },
        "synced": chats > 0 or bool(status.get("finished_at")),
        "chats": chats,
        "demo": web.is_demo(),
    }


def current_step(ov: dict[str, Any]) -> str:
    if not ov["credentials"]:
        return "credentials"
    if not ov["logged_in"]:
        return "login"
    if not ov["settings_saved"]:
        return "archive"
    if not ov["synced"] and not web.job_status()["running"]:
        return "sync"
    return "connect"


# --- page --------------------------------------------------------------------------

async def setup_page(request: Request) -> Response:
    if web.is_demo():
        return web.render(request, "error.html", status_code=409, title="Setup",
                          message="Setup is not available in the demo. The demo archive is made up; "
                                  "run `dotiphant setup --web` outside the demo to connect your own account.")
    ov = await run_in_threadpool(overview)
    current = current_step(ov)
    asked = request.query_params.get("step")
    step = asked if asked in STEPS else current
    # Steps after the login need a login first.
    if STEPS.index(step) > STEPS.index(current) and STEPS.index(current) < 2:
        step = current
    login = _state(request).login
    clients = await run_in_threadpool(_client_labels) if step == "connect" else []
    sched = await run_in_threadpool(_schedule_status) if step == "connect" else {}
    return web.render(
        request, "setup.html", title="Set up Dotiphant", ov=ov, step=step, current=current, steps=STEPS,
        api_page=config.API_PAGE, remote_docs=REMOTE_DOCS, clients=clients, schedule=sched,
        login=login.view() if login else {"state": "idle"},
        every=config.Settings.load().sync_interval_minutes, empty_archive=True,
    )


# --- API ---------------------------------------------------------------------------

async def api_overview(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    ov = await run_in_threadpool(overview)
    login = _state(request).login
    ov["step"] = current_step(ov)
    ov["login"] = login.view() if login else {"state": "idle"}
    return JSONResponse(ov)


async def api_credentials(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    body = await web.json_body(request)
    api_id = str(body.get("api_id") or "").strip()
    api_hash = body.get("api_hash")
    api_hash = api_hash.strip() if isinstance(api_hash, str) else ""
    if not config.valid_api_id(api_id):
        return _error(config.API_ID_RULE, field="api_id")
    if not config.valid_api_hash(api_hash):
        return _error(config.API_HASH_RULE, field="api_hash")
    st = _state(request)
    if st.login is not None:
        await st.login.cancel()
        st.login = None
    replaced = config.has_credentials()
    if replaced and config.has_session() and \
            (config.get_secret("api_id"), config.get_secret("api_hash")) != (api_id, api_hash.lower()):
        # Best effort: end the old login on Telegram first, so it does not linger under
        # Settings > Devices. telegram.logout() deletes the local session either way.
        from .. import telegram

        with contextlib.suppress(Exception):
            await asyncio.wait_for(telegram.logout(), 20)
    # New credentials mean a fresh login: save_credentials deletes the old app's session.
    where = await run_in_threadpool(config.save_credentials, api_id, api_hash)
    return JSONResponse({
        "saved": True, "replaced": replaced,
        "where": "your system keychain" if where != "file" else "a private file in the Dotiphant folder",
        "env_override": credentials_from_env(),
    })


async def api_login_get(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    login = _state(request).login
    return JSONResponse(login.view() if login else {"state": "idle"})


async def api_login_post(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    body = await web.json_body(request)
    action = body.get("action")
    st = _state(request)
    login = st.login

    if action == "cancel":
        if login is not None:
            await login.cancel()
        st.login = None
        return JSONResponse({"state": "idle"})

    if action in ("password", "code"):
        value = body.get(action)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            return _error("Please type the password." if action == "password" else "Please type the code.")
        if not st.answers.allow():
            return _error("Too many tries. Wait a few minutes and start again.", 429)
        if action == "code":
            value = re.sub(r"[\s-]", "", value)
        ok = login is not None and login.answer(action, value)
        del value, body  # the login task holds the only reference now
        if not ok:
            return _error("The login is not waiting for that right now. Start again.", 409,
                          **(login.view() if login else {"state": "idle"}))
        return JSONResponse(login.view())

    if action != "start":
        return _error("Unknown action.")
    if not config.has_credentials():
        return _error("Save your API id and hash first (step 1).", 409, back_to="credentials")
    method = body.get("method") or "qr"
    if method not in ("qr", "phone"):
        return _error("Unknown login method.")
    phone = None
    if method == "phone":
        raw = body.get("phone")
        phone = re.sub(r"[\s().-]", "", raw) if isinstance(raw, str) else ""
        if not re.fullmatch(r"\+?\d{6,16}", phone):
            return _error("Type the phone number with its country code, like +4915112345678.", field="phone")
        if not phone.startswith("+"):
            phone = "+" + phone
    if login is not None and login.active and not body.get("restart") and login.method == method == "qr":
        return JSONResponse(login.view())  # a reload or a second tab: keep the running login
    if not st.starts.allow():
        return _error("Too many login attempts. Wait a few minutes and try again.", 429)
    if login is not None:
        await login.cancel()
    st.login = LoginSession(method, phone)
    st.login.start()
    await asyncio.sleep(0)  # let the task take its first step
    return JSONResponse(st.login.view())


async def api_login_qr(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    login = _state(request).login
    if login is None or login.state != "waiting_scan" or not login.qr_url:
        return Response(status_code=404)
    svg = await run_in_threadpool(qr_svg, login.qr_url)
    return Response(svg, media_type="image/svg+xml", headers={
        "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


async def api_settings(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    body = await web.json_body(request)
    days = body.get("history_days")
    if days in ("all", None, ""):
        history = None
    else:
        try:
            history = int(days)
        except (TypeError, ValueError):
            return _error("Choose a number of days, like 365, or all history.", field="history_days")
        if history < 1 or history > 100_000:
            return _error("Choose a number of days, like 365, or all history.", field="history_days")
    ai_access = body.get("ai_access")
    if ai_access is not None and ai_access not in config.AI_ACCESS:
        return _error("Choose all chats or only the ones you choose.", field="ai_access")
    def choose(s: config.Settings) -> None:
        s.include_private = bool(body.get("people"))
        s.include_groups = bool(body.get("groups"))
        s.include_channels = bool(body.get("channels"))
        s.history_days = history
        if ai_access is not None:
            s.ai_access = ai_access

    s = config.update_settings(choose)
    if not (s.include_private or s.include_groups or s.include_channels):
        return JSONResponse({"saved": True, "warning": "Nothing is selected, so nothing will be archived."})
    return JSONResponse({"saved": True})


async def api_sync(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    if not config.has_credentials() or not config.has_session():
        return _error("Log in to Telegram first (step 2).", 409, back_to="login")
    started, text = web.start_job("sync", ["sync", "--quiet"])
    return web.job_response(started, text)


async def api_connect(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    body = await web.json_body(request)
    name = body.get("name")
    from .. import connect as connect_mod

    if not isinstance(name, str) or name not in connect_mod.CLIENTS:
        return _error("Unknown app.")
    try:
        text = await run_in_threadpool(connect_mod.connect, name)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": f"Could not update {connect_mod.CLIENTS[name].label} "
                             f"({type(exc).__name__}). Run `dotiphant connect {name} --print` to set it up by hand."})
    return JSONResponse({"ok": True, "message": text})


async def api_schedule(request: Request) -> Response:
    if refused := _refuse(request):
        return refused
    body = await web.json_body(request)
    from .. import schedule

    try:
        if body.get("on"):
            text = await run_in_threadpool(schedule.install)
        else:
            text = await run_in_threadpool(schedule.remove)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": f"Could not change the background refresh ({type(exc).__name__})."})
    return JSONResponse({"ok": True, "message": text, "status": await run_in_threadpool(_schedule_status)})


def routes() -> list[Route]:
    return [
        Route("/setup", setup_page),
        Route("/api/setup", api_overview),
        Route("/api/setup/credentials", api_credentials, methods=["POST"]),
        Route("/api/setup/login", api_login_get, methods=["GET"]),
        Route("/api/setup/login", api_login_post, methods=["POST"]),
        Route("/api/setup/login/qr.svg", api_login_qr),
        Route("/api/setup/settings", api_settings, methods=["POST"]),
        Route("/api/setup/sync", api_sync, methods=["POST"]),
        Route("/api/setup/connect", api_connect, methods=["POST"]),
        Route("/api/setup/schedule", api_schedule, methods=["POST"]),
    ]


def redirect_home_to_setup() -> Response | None:
    """Send a fresh install to /setup. Never in the demo or when the archive already has chats."""
    if web.is_demo() or _chat_count() > 0 or not needs_setup():
        return None
    return RedirectResponse("/setup", status_code=303)
