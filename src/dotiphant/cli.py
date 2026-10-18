"""The `dotiphant` command.

Other Dotiphant modules (telegram, sync, web, MCP, connect) are imported inside the command
functions, so `dotiphant --help` and the read-only commands start fast and `dotiphant mcp`
prints nothing to stdout before the protocol starts.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import getpass
import importlib
import inspect
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import __version__, config, status

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2
EXIT_PARTIAL = 3  # the sync finished, but some chats could not be read (retried next time)
EXIT_BUSY = 75  # EX_TEMPFAIL: another sync is running, try again later
EXIT_INTERRUPTED = 130
VIEWER_PORT = 8765
DEMO_PORT = 8764  # next to the viewer's 8765, so `dotiphant demo` and `dotiphant open` can run together


class CliError(Exception):
    """A problem we can explain in one line. Carries the exit code."""

    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


# --- small helpers -----------------------------------------------------------------

def _mod(name: str):
    """Import a Dotiphant module lazily (tests replace them through sys.modules)."""
    return importlib.import_module(f"dotiphant.{name}")


def say(*parts: Any) -> None:
    print(*parts, flush=True)


def note(*parts: Any) -> None:
    print(*parts, file=sys.stderr, flush=True)


def _run(coro_or_value):
    if inspect.isawaitable(coro_or_value):
        return asyncio.run(coro_or_value)
    return coro_or_value


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        say()
        return default
    return answer or default


def _confirm(question: str, default: bool, assume_yes: bool = False) -> bool:
    """Ask a yes/no question. Without a terminal nobody can answer, so the answer is no."""
    if assume_yes:
        return default
    if not sys.stdin.isatty():
        return False
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = _ask(f"{question} {hint} ", "y" if default else "n").lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        say("Please answer y or n.")


def _short(iso: str | None) -> str:
    return iso[:16].replace("T", " ") if iso else "-"


def _json(data: Any) -> None:
    say(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _ro_conn() -> sqlite3.Connection:
    from . import db

    return db.connect(readonly=True)


def _resolve(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    from . import query

    try:
        return query.resolve_chat(conn, ref)
    except query.ChatNotFound as exc:
        raise CliError(str(exc)) from None


def _is_auth_error(exc: BaseException) -> bool:
    if isinstance(exc, config.SecretsUnavailable):
        return True
    names = [cls.__name__ for cls in type(exc).__mro__]
    return any(word in name for name in names
               for word in ("Unauthorized", "AuthKey", "NotAuthorized", "NotLoggedIn", "SessionRevoked",
                            "OtherAccount"))


def _auth_message(exc: BaseException) -> str:
    """What to tell the user about an auth error: the error itself, with a setup hint when it helps."""
    msg = str(exc) or "Not logged in to Telegram."
    if type(exc).__name__ == "OtherAccount" or "dotiphant setup" in msg:
        return msg
    return f"{msg} Run `dotiphant setup`."


# --- progress line -----------------------------------------------------------------

STAGE_LABELS = {
    "starting": "Starting",
    "connecting": "Connecting to Telegram",
    "inventory": "Listing your chats",
    "recent": "Fetching new messages",
    "history": "Loading older history",
    "avatars": "Fetching profile photos",
    "media": "Downloading media",
    "done": "Done",
}


def progress_line(st: dict[str, Any]) -> str:
    state = st.get("state") or ""
    stage = st.get("stage") or state or "working"
    label = STAGE_LABELS.get(stage, stage.replace("_", " ").capitalize())
    if state == "waiting":
        label = "Telegram asked us to slow down, waiting"
    parts = [label]
    total = st.get("chats_total") or 0
    if total:
        parts.append(f"{st.get('chats_done') or 0}/{total} chats")
    if st.get("new_messages"):
        parts.append(f"{st['new_messages']:,} new messages")
    line = ", ".join(parts)
    if st.get("chat"):
        line += f": {st['chat']}"
    if state == "waiting" and st.get("message"):
        line += f" ({st['message']})"
    return line


class Progress:
    """Live one-line progress on a terminal, occasional lines otherwise."""

    def __init__(self, enabled: bool = True, stream=None):
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.written = False
        self.last_plain = ("", 0.0)

    def __call__(self, st: dict[str, Any]) -> None:
        if not self.enabled:
            return
        line = progress_line(st)
        if self.tty:
            width = max(20, _term_width() - 1)
            self.stream.write("\r\033[K" + line[:width])
            self.stream.flush()
            self.written = True
        else:
            key = (st.get("stage") or "", time.monotonic())
            if key[0] != self.last_plain[0] or key[1] - self.last_plain[1] > 10:
                self.stream.write(line + "\n")
                self.stream.flush()
                self.last_plain = key

    def finish(self) -> None:
        if self.enabled and self.tty and self.written:
            self.stream.write("\r\033[K")
            self.stream.flush()


def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 100


# --- shared flows ------------------------------------------------------------------

def end_old_session() -> None:
    """Best effort: end the stored session on Telegram before the credentials it belongs to change.

    A login is tied to the API id that made it. Without this, every credential change would
    leave a stale entry under Telegram > Settings > Devices.
    """
    if not config.has_session():
        return
    try:
        ended = bool(_run(_mod("telegram").logout()))
    except Exception:
        ended = False
    if ended:
        say("Ended the previous Telegram session.")
    else:
        note("Could not reach Telegram to end the previous session. End it in Telegram > Settings > Devices "
             "if it is still listed.")


def _changes_credentials(api_id: str, api_hash: str) -> bool:
    """True when valid new values differ from the stored ones (so the stored login must go)."""
    api_id, api_hash = str(api_id).strip(), api_hash.strip().lower()
    if not (config.valid_api_id(api_id) and config.valid_api_hash(api_hash)):
        return False
    return (config.get_secret("api_id"), config.get_secret("api_hash")) != (api_id, api_hash)


def _store_credentials(api_id: str, api_hash: str) -> str:
    """Save the API id and hash. Different values than the stored ones also end the stored login."""
    if _changes_credentials(api_id, api_hash):
        end_old_session()
    try:
        return config.save_credentials(api_id, api_hash)
    except ValueError as exc:
        raise CliError(str(exc), EXIT_AUTH) from None


def step_credentials(args) -> None:
    api_id = args.api_id or os.environ.get("DOTIPHANT_API_ID")
    api_hash = args.api_hash or os.environ.get("DOTIPHANT_API_HASH")
    if api_id and api_hash:
        where = _store_credentials(str(api_id), api_hash)
        say(f"API credentials saved ({_where(where)}).")
        return
    if getattr(args, "new_credentials", False):
        end_old_session()
        config.forget_credentials()
    if config.get_secret("api_id") and config.get_secret("api_hash"):
        say("API credentials: already saved, skipping (replace them with `dotiphant setup --new-credentials`).")
        return
    if args.yes and not sys.stdin.isatty():
        raise CliError("No API credentials. Pass --api-id and --api-hash, or set DOTIPHANT_API_ID and "
                       "DOTIPHANT_API_HASH.", EXIT_AUTH)
    say("Dotiphant talks to Telegram as your own app, so it needs an API id and hash (free, takes a minute):")
    say(f"  1. Open {config.API_PAGE} and log in with your phone number.")
    say("  2. Click 'API development tools' and create an app (any name, platform 'Desktop').")
    say("  3. Copy the 'App api_id' and 'App api_hash' shown there.")
    if sys.stdin.isatty() and _confirm("Open the page in your browser now?", True):
        try:
            webbrowser.open(config.API_PAGE)
        except Exception:
            pass
    for _ in range(3):
        api_id = _ask("api_id: ")
        if config.valid_api_id(api_id):
            break
        say(f"That does not look right. {config.API_ID_RULE}")
    else:
        raise CliError("No valid api_id entered. Run `dotiphant setup` again when you have it.", EXIT_AUTH)
    for _ in range(3):
        api_hash = getpass.getpass("api_hash (hidden while you type or paste): ").strip()
        if config.valid_api_hash(api_hash):
            break
        say(f"That does not look right. {config.API_HASH_RULE}")
    else:
        raise CliError("No valid api_hash entered. Run `dotiphant setup` again when you have it.", EXIT_AUTH)
    where = _store_credentials(api_id, api_hash)
    say(f"Saved ({_where(where)}).")


def _where(backend: str) -> str:
    if backend.startswith("file"):
        return f"in a private file under {config.data_dir() / 'secrets'}, no system keychain was found"
    return "in your system keychain"


def _print_qr(url: str) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=2)
        qr.add_data(url)
        qr.make(fit=True)
        say()
        qr.print_ascii(out=sys.stdout, invert=True)
    except Exception:
        say("(Could not draw the QR code here. Use `dotiphant login --phone` instead.)")
        return
    say("Scan this with your phone: Telegram app > Settings > Devices > Link Desktop Device.")
    say("Waiting for you to scan... (the code refreshes by itself; enlarge the window if it does not scan)")


def _get_password() -> str:
    return getpass.getpass("Your account has a two-step verification password. Enter it (hidden): ")


def _get_code() -> str:
    return _ask("Telegram sent you a login code (in the Telegram app or by SMS). Code: ")


async def _login_async(phone: str | None) -> str | None:
    """Log in if needed. Returns the account name, or None when already logged in."""
    tg = _mod("telegram")
    # QR login finishes through an update, so the login client is built with updates on.
    client = tg.make_client(receive_updates=True)
    await _maybe_await(client.connect())
    try:
        if await tg.is_authorized(client):
            if _other_account(await tg.account_id(client)):
                raise CliError(f"{_other_account_text()} To switch accounts, run `dotiphant logout` first.", EXIT_AUTH)
            return None
        if phone is not None:
            if not phone:
                phone = _ask("Phone number with country code, like +4915112345678: ")
            if not phone:
                raise CliError("No phone number entered.", EXIT_AUTH)
            name = await tg.login_phone(client, phone, _get_code, _get_password)
        else:
            name = await tg.login_qr(client, _print_qr, _get_password)
        tg.save_session(client)
        if _other_account(await tg.account_id(client)):
            # Undo this login, so the next `dotiphant login` can use the right account.
            await _end_login(client)
            raise CliError(_other_account_text(), EXIT_AUTH)
        return name
    finally:
        try:
            await _maybe_await(client.disconnect())
        except Exception:
            pass


def _other_account_text() -> str:
    from . import db

    return db.OTHER_ACCOUNT


def _other_account(account_id: int | None) -> bool:
    from . import db

    return db.other_account(account_id)


async def _end_login(client) -> None:
    """Best effort: end a login on Telegram and forget its session here."""
    try:
        await _maybe_await(client.log_out())
    except Exception:
        pass
    config.delete_secret("session")


def do_login(phone: str | None) -> None:
    try:
        name = _run(_login_async(phone))
    except CliError:
        raise
    except config.SecretsUnavailable as exc:
        raise CliError(str(exc), EXIT_AUTH) from None
    except Exception as exc:
        if config.credentials_rejected(exc):
            # Wrong credentials would otherwise be reused forever, so forget them (and their session).
            config.forget_credentials()
            raise CliError("Telegram rejected your API id and hash, so Dotiphant forgot them. Run `dotiphant setup` "
                           f"again and copy both values exactly from {config.API_PAGE}.", EXIT_AUTH) from None
        hint = "" if phone is not None else " If the QR code does not work, try `dotiphant login --phone`."
        raise CliError(f"Login failed: {exc}.{hint}", EXIT_AUTH) from None
    if name is None:
        say("Telegram: already logged in, skipping.")
    else:
        say(f"Logged in as {name}. The session is stored {_where(config.secrets_backend_name())}.")


def step_settings(args) -> config.Settings:
    path = config.data_dir() / "settings.json"
    if path.exists() and not args.reconfigure:
        say("Archive choices: already set, skipping (change them with `dotiphant setup --reconfigure`).")
        return config.Settings.load()
    before = config.Settings.load()
    s = dataclasses.replace(before)
    if not args.yes and sys.stdin.isatty():
        say()
        say("What should Dotiphant archive? Press Enter to keep the suggestion.")
        s.include_private = _confirm("  Chats with people?", s.include_private)
        s.include_groups = _confirm("  Groups?", s.include_groups)
        s.include_channels = _confirm("  Channels you follow (broadcast only, often large)?", s.include_channels)
        default = "all" if s.history_days is None else str(s.history_days)
        while True:
            raw = _ask(f"  How many days of history for the first sync? A number or 'all' [{default}]: ", default)
            if raw.lower() == "all":
                s.history_days = None
                break
            if raw.isdigit() and int(raw) > 0:
                s.history_days = int(raw)
                break
            say("  Please enter a number of days, like 365, or 'all'.")
        s.ai_access = _ask_ai_access(s.ai_access)
    # The prompts can take minutes: write only the answers that changed, onto the file as it
    # is now, so a change made meanwhile (in the viewer, say) is kept.
    answers = {k: getattr(s, k) for k in ("include_private", "include_groups", "include_channels",
                                         "history_days", "ai_access") if getattr(s, k) != getattr(before, k)}

    def apply(now: config.Settings) -> None:
        for key, value in answers.items():
            setattr(now, key, value)

    s = config.update_settings(apply)
    kinds = [k for k, on in (("people", s.include_private), ("groups", s.include_groups),
                             ("channels", s.include_channels)) if on]
    span = "all history" if s.history_days is None else f"the last {s.history_days} days"
    say(f"Archiving {', '.join(kinds) or 'nothing'}, {span}. Saved to {path}.")
    return s


def _ask_ai_access(current: str) -> str:
    default = "choose" if current == "selected" else "all"
    hint = "all/Choose" if default == "choose" else "All/choose"
    while True:
        raw = _ask(f"  Which chats may AI apps read? All, or only the ones you choose? [{hint}] ", default).lower()
        if raw in ("all", "a"):
            return "all"
        if raw in ("choose", "c", "selected", "only"):
            say("  AI apps see no chat until you allow it. Allow one with `dotiphant ai show CHAT`,")
            say("  or with the 'Visible to AI apps' switch on the chat's page in `dotiphant open`.")
            return "selected"
        say("  Please answer all or choose.")


def run_sync_cli(chat_ids: list[int] | None = None, *, settings: config.Settings | None = None,
                 quiet: bool = False, max_seconds: float | None = None) -> int:
    sync = _mod("sync")
    progress = Progress(enabled=not quiet)
    try:
        result = _run(sync.run_sync(chat_ids, settings=settings, progress=progress, max_seconds=max_seconds))
    except sync.SyncBusy:
        progress.finish()
        if not quiet:
            note("A sync is already running. Try again when it is done.")
        return EXIT_BUSY
    except KeyboardInterrupt:
        progress.finish()
        note("Stopped. The next sync continues where this one left off.")
        return EXIT_INTERRUPTED
    except Exception as exc:
        progress.finish()
        if _is_auth_error(exc):
            note(_auth_message(exc))
            return EXIT_AUTH
        note(f"Sync failed: {exc}")
        return EXIT_ERROR
    progress.finish()
    if not quiet:
        say(_sync_summary(result or {}))
    return EXIT_PARTIAL if (result or {}).get("errors") else EXIT_OK


def _sync_summary(result: dict[str, Any]) -> str:
    chats = result.get("chats", result.get("chats_done"))
    parts = []
    if chats is not None:
        parts.append(f"{chats:,} chats checked")
    if result.get("new_messages") is not None:
        parts.append(f"{result['new_messages']:,} new messages")
    if result.get("edited_messages"):
        parts.append(f"{result['edited_messages']:,} edited")
    line = "Sync " + ("stopped" if result.get("stopped") else "done") + (": " + ", ".join(parts) if parts else "") + "."
    if result.get("message"):
        line += f" {result['message']}"
    if result.get("errors"):
        line += f" {len(result['errors'])} chat(s) could not be read; they are retried next time."
    if result.get("history_pending_chats"):
        line += (f" Older history is still loading for {result['history_pending_chats']:,} chats;"
                 " the next sync continues.")
    return line


def step_connect(args) -> None:
    connect = _mod("connect")
    clients = connect.detect()
    if not clients:
        say("No AI apps found to connect. Later: `dotiphant connect` lists the options.")
        return
    say()
    for name in clients:
        label = connect.CLIENTS[name].label
        if _confirm(f"Let {label} read your archive (adds Dotiphant to its MCP config)?", True, args.yes):
            try:
                say(f"  {connect.connect(name)}")
            except Exception as exc:
                say(f"  Could not update {name}: {exc}. Try `dotiphant connect {name} --print` to set it up by hand.")


def step_schedule(args) -> None:
    schedule = _mod("schedule")
    if schedule.status().get("installed"):
        say("Background refresh: already installed, skipping.")
        return
    every = config.Settings.load().sync_interval_minutes
    say()
    if _confirm(f"Keep the archive fresh in the background (every {every} minutes)?", True, args.yes):
        try:
            say(schedule.install(every))
        except Exception as exc:
            say(f"Could not install the background refresh: {exc}")


EXAMPLE_QUESTIONS = [
    "What did Clara and I agree on about the launch date?",
    "Summarize what happened in my groups since yesterday.",
    "Which messages am I still owing an answer to this week?",
    "Find the address someone sent me in March.",
]


def step_next() -> None:
    say()
    say("All set. Next:")
    say("  dotiphant open       browse and search your archive in the browser")
    say("  dotiphant status     see what is archived and when it last synced")
    say("Things to ask your AI assistant now:")
    for q in EXAMPLE_QUESTIONS:
        say(f'  "{q}"')


# --- commands ----------------------------------------------------------------------

def cmd_setup(args) -> int:
    if getattr(args, "web", False):
        return launch_viewer(args, VIEWER_PORT, path="/setup")
    say(f"Dotiphant {__version__} setup. Your data stays on this computer, in {config.data_dir()}.")
    say("Prefer a browser? Run `dotiphant open`.")
    say()
    say("Step 1 of 6: Telegram API credentials")
    step_credentials(args)
    say()
    say("Step 2 of 6: log in to Telegram")
    phone = args.phone
    if phone is None and not args.yes and not config.has_session():
        choice = _ask("Log in by scanning a QR code with your phone (recommended), or with a code sent to "
                      "your phone number? [QR/phone]: ", "qr").lower()
        if choice.startswith("p"):
            phone = ""
    do_login(phone)
    say()
    say("Step 3 of 6: what to archive")
    settings = step_settings(args)
    say()
    say("Step 4 of 6: first sync")
    if args.no_sync:
        say("Skipped (--no-sync). Run `dotiphant sync` when you are ready.")
    else:
        say("This can take a while for big accounts. You can stop with Ctrl-C and continue later.")
        code = run_sync_cli(settings=settings)
        if code == EXIT_AUTH:
            return code
    say()
    say("Step 5 of 6: connect your AI assistant")
    step_connect(args)
    say()
    say("Step 6 of 6: background refresh")
    step_schedule(args)
    step_next()
    return EXIT_OK


def cmd_login(args) -> int:
    if not config.has_credentials():
        raise CliError("Telegram API credentials are missing. Run `dotiphant setup` first.", EXIT_AUTH)
    do_login(args.phone)
    return EXIT_OK


FORGET_SECRETS = ("api_id", "api_hash", "viewer_token", "mcp_token")


def cmd_logout(args) -> int:
    had_session = config.has_session()
    try:
        ended = bool(_run(_mod("telegram").logout()))
    except Exception:
        ended = False
    config.delete_secret("session")  # gone from this computer whatever Telegram said
    if not had_session:
        say("No Telegram session is stored on this computer.")
    elif ended:
        say("Logged out. The Telegram session is ended and removed from this computer.")
    else:
        say("Could not reach Telegram to end the session. It is removed from this computer anyway. "
            "End it in Telegram > Settings > Devices if it is still listed.")
    if args.forget_credentials:
        # Fast path: stop this folder's viewers on the usual ports while their key still identifies them.
        # Viewers on other ports notice the deleted key themselves within a few seconds.
        if config.get_secret("viewer_token"):
            _stop_viewers([config.data_dir_path()])
        used_http_mcp = (config.get_secret("mcp_token") is not None
                         or (config.data_dir_path() / "oauth.json").exists())
        for name in FORGET_SECRETS:
            config.delete_secret(name)
        (config.data_dir_path() / "oauth.json").unlink(missing_ok=True)
        say("Also removed: the API id and hash, the viewer key, the MCP token and the approved remote apps.")
        if used_http_mcp:
            note("If `dotiphant mcp --http` is running, it stops accepting the old tokens within a few seconds. "
                 "A server started with --token keeps accepting that token until you stop it (Ctrl+C).")
    say(f"Your archive is still in {config.data_dir_path()}. Delete that folder to remove everything.")
    return EXIT_OK


def _overview() -> dict[str, Any]:
    from . import query

    conn = _ro_conn()
    try:
        return query.overview(conn)
    finally:
        conn.close()


async def _check_authorized() -> bool:
    tg = _mod("telegram")
    client = tg.make_client()
    await _maybe_await(client.connect())
    try:
        return bool(await tg.is_authorized(client))
    finally:
        try:
            await _maybe_await(client.disconnect())
        except Exception:
            pass


def _account_state(online: bool) -> str:
    if not config.has_credentials():
        return "not set up (run `dotiphant open` or `dotiphant setup`)"
    if not config.has_session():
        return "not logged in (run `dotiphant login`)"
    if not online:
        return "session stored"
    try:
        ok = asyncio.run(asyncio.wait_for(_check_authorized(), timeout=15))
    except Exception as exc:
        return f"session stored, could not verify ({type(exc).__name__})"
    return "logged in" if ok else "session expired (run `dotiphant login`)"


def _schedule_state() -> str:
    try:
        st = _mod("schedule").status()
    except Exception:
        return "unknown"
    if st.get("installed") is None:
        return "not managed on this platform"
    if st.get("installed"):
        return f"every {st.get('every_minutes') or '?'} minutes"
    return "off (run `dotiphant schedule install`)"


def cmd_status(args) -> int:
    ov = _overview()
    st = status.read_status()
    rows = [
        ("Telegram", _account_state(online=not args.offline)),
        ("Archive", f"{ov['chats']:,} chats, {ov['messages']:,} messages"
                    + (f", {_short(ov['first_message_at'])[:10]} to {_short(ov['last_message_at'])[:10]}"
                       if ov.get("messages") else "")),
        ("Last sync", _describe_sync(st, ov)),
        ("Background", _schedule_state()),
        ("Data folder", str(config.data_dir_path())),
        ("Secrets", config.secrets_backend_name()),
    ]
    if args.json:
        _json({"overview": ov, "sync": st, **{k.lower().replace(" ", "_"): v for k, v in rows}})
        return EXIT_OK
    for key, value in rows:
        say(f"{key + ':':<13}{value}")
    return EXIT_OK


def _describe_sync(st: dict[str, Any], ov: dict[str, Any]) -> str:
    state = st.get("state")
    if state in ("running", "waiting"):
        return "running now: " + progress_line(st)
    if state == "error":
        return f"failed at {_short(st.get('finished_at'))}: {st.get('message') or 'unknown error'}"
    when = st.get("finished_at") or ov.get("last_sync_at")
    if not when:
        return "never"
    extra = f", {st['new_messages']:,} new messages" if st.get("new_messages") else ""
    if isinstance(st.get("errors"), int) and st["errors"] > 0:
        extra += f", {st['errors']:,} chat(s) could not be read (retried next time)"
    return f"{_short(when)} UTC{extra}"


def cmd_doctor(args) -> int:
    from . import db

    problems = 0

    def check(ok: bool | None, label: str, hint: str = "") -> None:
        nonlocal problems
        mark = {True: "ok  ", False: "FAIL", None: "warn"}[ok]
        say(f"[{mark}] {label}")
        if ok is not True and hint:
            say(f"       {hint}")
        if ok is False:
            problems += 1

    v = sys.version_info
    check(v >= (3, 12), f"Python {v.major}.{v.minor}", "Dotiphant needs Python 3.12 or newer.")
    backend = config.secrets_backend_name()
    check(None if backend.startswith("file") else True, f"Secrets stored in: {backend}",
          "No system keychain found; secrets are in 0600 files in the data folder.")
    home = config.data_dir()
    try:
        with tempfile.NamedTemporaryFile(dir=home):
            pass
        check(True, f"Data folder is writable: {home}")
    except OSError as exc:
        check(False, f"Data folder is not writable: {home}", f"Fix the permissions ({exc}).")
    dbfile = config.db_path()
    if dbfile.exists():
        try:
            conn = sqlite3.connect(db.readonly_uri(dbfile), uri=True)
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            check(result == "ok", "Database integrity", f"quick_check said: {result}. Restore from a backup "
                  "or move dotiphant.db away and run `dotiphant sync` again.")
            conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'dotiphant'").fetchone()
            check(True, "Full-text search index")
            conn.close()
        except sqlite3.Error as exc:
            check(False, "Database could not be read", str(exc))
    else:
        check(None, "No archive yet", "Run `dotiphant open` or `dotiphant setup`.")
    has_credentials = config.has_credentials()
    check(has_credentials, "Telegram API credentials", "Run `dotiphant open` or `dotiphant setup`.")
    check(config.has_session(), "Telegram session",
          "Run `dotiphant login`." if has_credentials else "Run `dotiphant open` or `dotiphant setup`.")
    st = status.read_status()
    if st.get("state") == "error":
        check(None, f"Last sync failed: {st.get('message') or 'unknown error'}", "Run `dotiphant sync` to see details.")
    sched = _mod("schedule").status()
    if sched.get("installed") is not None:
        check(True if sched["installed"] else None, "Background refresh",
              "Off. Run `dotiphant schedule install` to keep the archive fresh.")
    connect = _mod("connect")
    clients = connect.detect()
    for name in clients:
        label = connect.CLIENTS[name].label
        if connect.is_connected(name):
            check(True, f"{label}: connected")
        else:
            check(None, f"{label}: found, not connected", f"Run `dotiphant connect {name}`.")
    if not clients:
        check(None, "No AI apps detected", "Run `dotiphant connect` to see the options.")
    say()
    say("Everything looks fine." if not problems else f"{problems} problem(s) found. See the hints above.")
    return EXIT_OK if not problems else EXIT_ERROR


def cmd_sync(args) -> int:
    conn = _ro_conn()
    try:
        demo = _is_demo_archive(conn)
    finally:
        conn.close()
    if demo:
        raise CliError("Sync is not available in the demo: its chats are made up.")
    chat_ids: list[int] | None = None
    if args.chats:
        names = [c for c in args.chats if not re.fullmatch(r"-?\d+", c.strip())]
        chat_ids = [int(c) for c in args.chats if re.fullmatch(r"-?\d+", c.strip())]
        if names:
            chat_ids += _resolve_names_for_sync(names, quiet=args.quiet)
    settings = None
    if args.all_history:
        settings = dataclasses.replace(config.Settings.load(), history_days=None)
    max_seconds = args.max_minutes * 60 if args.max_minutes else None
    return run_sync_cli(chat_ids, settings=settings, quiet=args.quiet, max_seconds=max_seconds)


def _resolve_names_for_sync(names: list[str], quiet: bool) -> list[int]:
    from . import query

    def attempt() -> tuple[list[int], list[str]]:
        found, missing = [], []
        conn = _ro_conn()
        try:
            for name in names:
                try:
                    found.append(query.resolve_chat(conn, name)["chat_id"])
                except query.ChatNotFound as exc:
                    if exc.candidates:
                        raise CliError(str(exc)) from None
                    missing.append(name)
        finally:
            conn.close()
        return found, missing

    found, missing = attempt()
    if missing:
        sync = _mod("sync")
        if not quiet:
            note("Looking up your chat list first...")
        try:
            _run(sync.run_inventory())
        except sync.SyncBusy:
            raise CliError("A sync is running right now. Try again when it finishes, or use the chat id.") from None
        except Exception as exc:
            if _is_auth_error(exc):
                raise CliError(_auth_message(exc), EXIT_AUTH) from None
            raise
        found, missing = attempt()
    if missing:
        raise CliError(f"No chat matches {', '.join(repr(m) for m in missing)}. "
                       "Run `dotiphant sync` once without names to load your chat list, or use the chat id.")
    return found


def avatars_summary(result: dict[str, Any]) -> str:
    line = f"Profile photos: {result.get('chats', 0):,} chats checked, {result.get('downloaded', 0):,} downloaded"
    if result.get("removed"):
        line += f", {result['removed']:,} removed"
    if result.get("failed"):
        line += f", {result['failed']:,} could not be read (tried again next time)"
    line += "."
    if result.get("missing"):
        line += f" Not in your chat list, or excluded by your settings: {', '.join(map(str, result['missing']))}."
    if result.get("message"):
        line += f" {result['message']}"
    return line


def cmd_avatars(args) -> int:
    conn = _ro_conn()
    try:
        demo = _is_demo_archive(conn)
    finally:
        conn.close()
    if demo:
        raise CliError("Profile photos are not available in the demo: its chats are made up.")
    chat_ids: list[int] | None = None
    if args.chats:
        chat_ids = [int(c) for c in args.chats if re.fullmatch(r"-?\d+", c.strip())]
        names = [c for c in args.chats if not re.fullmatch(r"-?\d+", c.strip())]
        if names:
            chat_ids += _resolve_names_for_sync(names, quiet=args.quiet)
    sync = _mod("sync")
    progress = Progress(enabled=not args.quiet)
    try:
        result = _run(sync.run_avatars(chat_ids, refresh=args.refresh, progress=progress))
    except sync.SyncBusy:
        progress.finish()
        note("A sync is running. Try again when it is done.")
        return EXIT_BUSY
    except KeyboardInterrupt:
        progress.finish()
        note("Stopped.")
        return EXIT_INTERRUPTED
    except Exception as exc:
        progress.finish()
        if _is_auth_error(exc):
            note(_auth_message(exc))
            return EXIT_AUTH
        note(f"Fetching profile photos failed: {exc}")
        return EXIT_ERROR
    progress.finish()
    if not args.quiet:
        say(avatars_summary(result or {}))
    return EXIT_OK


def _is_demo_archive(conn: sqlite3.Connection) -> bool:
    from . import db

    return db.get_meta(conn, "demo") == "1"


def media_summary(result: dict[str, Any]) -> str:
    parts = [f"{result.get('downloaded', 0):,} downloaded"]
    for key, label in (("already_present", "already here"), ("skipped_large", "skipped as too large (over 50 MB)"),
                       ("unavailable", "no longer available on Telegram"), ("failed", "failed")):
        if result.get(key):
            parts.append(f"{result[key]:,} {label}")
    return ", ".join(parts)


def cmd_media(args) -> int:
    conn = _ro_conn()
    try:
        chat = _resolve(conn, args.chat)
        demo = _is_demo_archive(conn)
    finally:
        conn.close()
    if demo:
        raise CliError("Media downloads are not available in the demo: its chats are made up.")
    sync = _mod("sync")
    try:
        result = _run(sync.download_media(chat["chat_id"], args.message_ids or None))
    except sync.SyncBusy:
        note("A sync is running. Try again in a moment.")
        return EXIT_BUSY
    except Exception as exc:
        if _is_auth_error(exc):
            note(_auth_message(exc))
            return EXIT_AUTH
        raise
    result = result or {}
    if args.quiet:
        return EXIT_OK
    say(f"Media for {chat['title']}: {media_summary(result)}.")
    if result.get("skipped_large"):
        say("Download a large file by naming its message id: `dotiphant media CHAT MESSAGE_ID`.")
    if result.get("downloaded") or result.get("already_present"):
        say(f"Files are in {config.media_dir() / str(chat['chat_id'])}.")
    return EXIT_OK


def launch_viewer(args, default_port: int, path: str = "/") -> int:
    """Start the viewer, or reuse one that already runs the same build for the same data folder.

    A viewer for this folder that runs older code is asked to stop first. A port held by another
    program is skipped, unless the user chose it with --port.
    """
    viewer = _mod("viewer")
    explicit = args.port is not None
    key = config.get_secret("viewer_token") or ""
    try:
        plan = viewer.plan(args.port if explicit else default_port, config.data_dir_path(), key,
                           explicit=explicit, log=say)
    except viewer.PortError as exc:
        raise CliError(str(exc)) from None
    if plan.action == "reuse":
        base = f"http://127.0.0.1:{plan.port}/{path.lstrip('/')}"
        link = f"{base}?key={key}"
        if args.no_browser:
            say(f"Dotiphant is already running at {link}")
            say("This link is private: it unlocks your archive.")
        else:
            say(f"Dotiphant is already running at {base}")
            if not webbrowser.open(link):
                say(f"Could not open a browser. Open this private link: {link}")
        return EXIT_OK
    extra = {"path": path} if path != "/" else {}
    _mod("web.app").serve(port=plan.port, open_browser=not args.no_browser, **extra)
    return EXIT_OK


def cmd_open(args) -> int:
    return launch_viewer(args, VIEWER_PORT)


@contextlib.contextmanager
def _data_folder(home: Path):
    """Read secrets as if DOTIPHANT_HOME pointed at `home` (secrets may live in its secrets/ folder)."""
    old = os.environ.get("DOTIPHANT_HOME")
    os.environ["DOTIPHANT_HOME"] = str(home)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DOTIPHANT_HOME", None)
        else:
            os.environ["DOTIPHANT_HOME"] = old


def _stop_viewers(homes: list[Path]):
    """Ask the viewers for these data folders to shut down, and say what happened.

    Viewers are found by a folder header keyed with each folder's stored viewer key, so this must
    run before that key is deleted. Never stops anything by pid.
    """
    viewer = _mod("viewer")
    folders: dict[str, str] = {}
    for home in homes:
        with _data_folder(home):
            key = config.get_secret("viewer_token")
        if key:
            folders[viewer.folder_id(home, key)] = key
    ports = sorted({p for base in (VIEWER_PORT, DEMO_PORT) for p in range(base, base + viewer.PORT_RANGE + 1)})
    result = viewer.stop_all(ports, folders)
    for port in result.stopped:
        say(f"Stopped the Dotiphant viewer on port {port}.")
    for port in result.failed:
        note(f"The Dotiphant viewer on port {port} did not stop. Press Ctrl+C in the terminal where it runs, "
             f"or find it: {viewer.find_it(port)}.")
    for port in result.legacy:
        note(f"An older Dotiphant viewer is running on port {port}, from a version that cannot be stopped "
             f"from here. Press Ctrl+C in the terminal where it runs, or find it: {viewer.find_it(port)}.")
    return result


def cmd_stop(args) -> int:
    """Ask the viewers for this data folder and for the demo to shut down. Never stops anything by pid."""
    homes = [config.data_dir_path()]
    with contextlib.suppress(Exception):
        homes.append(Path(_mod("demo").demo_home()))
    result = _stop_viewers(homes)
    if not (result.stopped or result.failed or result.legacy):
        say("No Dotiphant viewer is running.")
    return EXIT_ERROR if result.failed or result.legacy else EXIT_OK


def _printable(text: Any) -> str:
    """App-chosen text for the terminal: control characters (like escape sequences) become '?'."""
    return "".join(ch if ch.isprintable() else "?" for ch in str(text))


def mcp_approve(request_id: str) -> int:
    """Approve one waiting OAuth request in the terminal, after showing where its code goes."""
    oauth = _mod("oauth")
    req = oauth.find_pending(request_id)
    if req is None:
        raise CliError(f"No open approval request has the id {_printable(request_id)}. Requests expire after "
                       f"{oauth.PENDING_TTL // 60} minutes; start connecting again from the AI app.")
    if not sys.stdin.isatty():
        raise CliError("Approving needs an interactive terminal. Run this command yourself in a terminal.")
    uri = str(req.get("redirect_uri") or "")
    say(f"Approval request {req['id']}")
    say(f"  App name (chosen by the app itself): {_printable(req.get('client_name') or '(no name)')[:100]}")
    say(f"  Returns to:  {_printable(uri)}")
    say(f"  Host:        {_printable(urlparse(uri).netloc)}")
    say("Whoever runs that address gets read-only access to your whole Telegram archive.")
    say("Approve only if you started connecting this app just now and the host belongs to it.")
    if not _confirm("Approve?", False):
        say("Not approved. The request stays open until it expires; choose Deny on the page to close it.")
        return EXIT_OK
    if not oauth.approve_pending(request_id):
        raise CliError("The request expired in the meantime. Start connecting again from the AI app.")
    say("Approved. Choose Continue on the approval page in your browser.")
    return EXIT_OK


def cmd_mcp(args) -> int:
    server = _mod("mcp_server")
    if args.approve:
        return mcp_approve(args.approve)
    if args.pair or args.clients or args.revoke:  # OAuth housekeeping for remote access
        say(server.pairing_message() if args.pair else
            server.clients_text() if args.clients else server.revoke_clients(args.revoke))
        return EXIT_OK
    if args.show_token and not args.http:
        say(server.http_token())  # creates and stores one on first use
        return EXIT_OK
    if not args.http:
        server.run_stdio()  # stdout belongs to the MCP protocol: print nothing here
        return EXIT_OK
    host = args.host
    if args.allow_remote:
        note("WARNING: --allow-remote lets other machines reach this server. Anyone who has the bearer "
             "token can read your whole archive. Prefer 127.0.0.1 behind a tunnel you control.")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        raise CliError(f"Refusing to listen on {host} without --allow-remote.")
    if args.show_token:
        note(f"Bearer token: {server.http_token()}")
    server.run_http(host=host, port=args.port, token=args.token, allow_remote=args.allow_remote,
                    public_url=args.public_url)
    return EXIT_OK


def cmd_connect(args) -> int:
    connect = _mod("connect")
    known = list(connect.CLIENTS)
    if not args.client:
        detected = connect.detect()
        say("AI apps Dotiphant can connect to:")
        for name in known:
            say(f"  {name:<16}{'found on this computer' if name in detected else 'not found'}")
        say()
        say("Connect one with `dotiphant connect NAME`, or all found ones with `dotiphant connect all`.")
        say("`dotiphant connect NAME --print` shows the config to paste by hand.")
        return EXIT_OK
    names = connect.detect() if args.client == "all" else [args.client]
    if args.client == "all" and not names:
        say("No AI apps found on this computer. Use `dotiphant connect NAME --print` to configure one by hand.")
        return EXIT_ERROR
    for name in names:
        if name not in known:
            raise CliError(f"Unknown app '{name}'. Choose from: {', '.join(known)}.")
        if args.print:
            say(connect.snippet(name))
        else:
            say(connect.connect(name, dry_run=args.dry_run))
    return EXIT_OK


def cmd_chats(args) -> int:
    from . import query

    conn = _ro_conn()
    try:
        chats = query.list_chats(conn, query=args.filter, kind=args.kind, active_since=args.active, limit=args.limit)
    finally:
        conn.close()
    if args.json:
        _json(chats)
        return EXIT_OK
    if not chats:
        say("No chats archived yet." if not (args.kind or args.active or args.filter) else "No chats match.")
        return EXIT_OK
    say(f"{'ID':>15}  {'KIND':<10} {'MESSAGES':>8}  {'LAST ACTIVE':<16}  TITLE")
    for c in chats:
        title = c["title"] + (f" (@{c['username']})" if c.get("username") else "")
        say(f"{c['chat_id']:>15}  {c['kind']:<10} {c['message_count']:>8}  {_short(c['last_message_at']):<16}  {title}")
    return EXIT_OK


def cmd_search(args) -> int:
    from . import query

    conn = _ro_conn()
    try:
        chat_id = _resolve(conn, args.chat)["chat_id"] if args.chat else None
        hits = query.search(conn, args.query, chat_id=chat_id, sender=args.sender, since=args.since,
                            until=args.until, limit=args.limit)
    finally:
        conn.close()
    if args.json:
        _json(hits)
        return EXIT_OK
    if not hits:
        say("No messages found.")
        return EXIT_OK
    for h in hits:
        where = h["chat_title"] + (f" / {h['topic']}" if h.get("topic") else "")
        say(f"{_short(h['date'])}  {where}  {h['sender']}  (chat {h['chat_id']}, #{h['message_id']})")
        say(f"    {' '.join((h.get('snippet') or h.get('text') or '').split())}")
    return EXIT_OK


def cmd_export(args) -> int:
    from . import query

    conn = _ro_conn()
    try:
        chat = _resolve(conn, args.chat)
        messages = query.get_messages(conn, chat["chat_id"], since=args.since, until=args.until,
                                      limit=args.limit or 10**9)
    finally:
        conn.close()
    text = query.to_markdown(chat, messages)
    if args.out:
        out = Path(args.out).expanduser()
        config.write_private(out, text)
        note(f"Wrote {len(messages)} messages to {out}")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_recent(args) -> int:
    from . import query

    conn = _ro_conn()
    try:
        activity = query.recent_activity(conn, since=args.since)
    finally:
        conn.close()
    if args.json:
        _json(activity)
        return EXIT_OK
    if not activity:
        say(f"No new messages in the last {args.since}.")
        return EXIT_OK
    for chat in activity:
        mine = f", {chat['sent_by_me']} by you" if chat.get("sent_by_me") else ""
        say(f"{chat['title']} ({chat['kind']}): {chat['new_messages']} new{mine}, last {_short(chat['last_message_at'])}")
        for m in chat.get("latest", []):
            body = " ".join(query.message_line_text(m).split())
            say(f"    {_short(m['date'])[5:]}  {m['sender']}: {body[:110]}")
    return EXIT_OK


def cmd_schedule(args) -> int:
    schedule = _mod("schedule")
    if args.action == "install":
        if args.every:
            config.update_settings(lambda s: setattr(s, "sync_interval_minutes", args.every))
        say(schedule.install(args.every))
    elif args.action == "remove":
        say(schedule.remove())
    else:
        st = schedule.status()
        if st.get("installed") is None:
            say("Background refresh is not managed automatically on this platform. "
                "`dotiphant schedule install` prints the command to set it up.")
        elif st.get("installed"):
            say(f"Background refresh: every {st.get('every_minutes') or '?'} minutes ({st.get('path')}).")
        else:
            say("Background refresh: off. Turn it on with `dotiphant schedule install`.")
    return EXIT_OK


def cmd_demo(args) -> int:
    demo = _mod("demo")
    home = Path(demo.demo_home())
    os.environ["DOTIPHANT_HOME"] = str(home)
    from . import db

    dbfile = home / "dotiphant.db"
    empty = True
    if dbfile.exists():
        conn = db.connect(dbfile, readonly=True)
        try:
            empty = conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        finally:
            conn.close()
    if empty:
        say("Creating a made-up demo archive...")
        demo.seed_demo(home)
    say(f"Demo data lives in {home} and is separate from your own archive.")
    import shlex

    say(f"To try other commands on it: DOTIPHANT_HOME={shlex.quote(str(home))} dotiphant search launch")
    if args.no_open:
        return EXIT_OK
    return launch_viewer(args, DEMO_PORT)


def cmd_forget(args) -> int:
    from . import forget

    conn = _ro_conn()
    try:
        chat = _resolve(conn, args.chat)
    finally:
        conn.close()
    say(f"This deletes \"{chat['title']}\" ({chat['chat_id']}) from the archive: all its messages and downloaded")
    say("media. Telegram is not touched, and Dotiphant will not sync this chat again.")
    if not args.yes and not _confirm("Delete it from the archive?", False):
        say("Nothing deleted.")
        return EXIT_OK
    try:
        result = forget.forget_chat(chat["chat_id"])
    except forget.Busy as exc:
        raise CliError(str(exc), EXIT_BUSY) from None
    say(f"Deleted {result['messages_removed']} messages. The chat is excluded from future syncs.")
    for folder in result.get("media_left", []):
        note(f"Some downloaded files could not be deleted. Delete this folder yourself: {folder}")
    if not result["log_cleared"]:
        say("The viewer or an AI app was still reading the archive, so a copy of the deleted text may stay in")
        say("dotiphant.db-wal for now. It is cleared once they close and the next sync finishes.")
    return EXIT_OK


AI_MODE_TEXT = {
    "all": "AI apps can read every chat, except the ones you hide.",
    "selected": "AI apps can read only the chats you allow. New chats stay hidden until you allow them.",
}


def _ai_chats(conn: sqlite3.Connection, ids: list[int]) -> list[dict[str, Any]]:
    """Chats from the archive with these ids, newest activity first. Earlier basic groups are left out."""
    from . import query

    if not ids:
        return []
    return [c for c in query.list_chats(conn, limit=1_000_000) if c["chat_id"] in set(ids)]


def cmd_ai(args) -> int:
    from . import query

    action = args.ai_action or "list"
    if action == "mode":
        s = config.update_settings(lambda s: setattr(s, "ai_access", args.mode))
        say(f"AI access: {'all chats' if args.mode == 'all' else 'only chosen chats'}. {AI_MODE_TEXT[args.mode]}")
        if args.mode == "selected" and not s.ai_allowed_chats:
            say("No chat is allowed yet. Allow one with `dotiphant ai show CHAT`.")
        say("AI apps use the new setting from their next request.")
        return EXIT_OK
    conn = _ro_conn()
    try:
        if action in ("hide", "show"):
            visible = action == "show"
            chats = [_resolve(conn, ref) for ref in args.chats]
            linked = [query.linked_chat_ids(conn, [chat["chat_id"]]) for chat in chats]

            def change(s: config.Settings) -> None:
                for ids in linked:
                    s.set_ai_visible(ids, visible)

            s = config.update_settings(change)
            scope = query.ai_scope(conn, s)
            for chat in chats:
                state = "visible to AI apps" if scope.allows(chat["chat_id"]) else "hidden from AI apps"
                say(f"{_printable(chat['title'])} ({chat['chat_id']}): {state}.")
            return EXIT_OK
        s = config.Settings.load()
        scope = query.ai_scope(conn, s)
        total = conn.execute("SELECT count(*) FROM chats WHERE migrated_to IS NULL").fetchone()[0]
        if s.ai_access == "all":
            listed = _ai_chats(conn, sorted(scope.hidden))
            heading, empty = "Hidden from AI apps:", "No chats are hidden from AI apps."
        else:
            listed = _ai_chats(conn, sorted(scope.only or ()))
            heading, empty = "Visible to AI apps:", "No chats are visible to AI apps yet."
    finally:
        conn.close()
    if getattr(args, "json", False):
        _json({"mode": s.ai_access, "chats": [{"chat_id": c["chat_id"], "title": c["title"]} for c in listed]})
        return EXIT_OK
    mode = "all chats" if s.ai_access == "all" else "only chosen chats"
    say(f"AI access: {mode}. {AI_MODE_TEXT[s.ai_access]}")
    say()
    if listed:
        say(heading)
        for c in listed:
            say(f"  {c['chat_id']:>15}  {_printable(c['title'])}")
    else:
        say(empty)
    if s.ai_access == "all":
        say(f"{max(total - len(listed), 0):,} of {total:,} chats are visible to AI apps.")
    else:
        say(f"{max(total - len(listed), 0):,} of {total:,} chats are hidden from AI apps.")
    say()
    say("Change it with `dotiphant ai hide CHAT`, `dotiphant ai show CHAT` or `dotiphant ai mode all|selected`.")
    say("The viewer and `dotiphant search` always show every chat: they are yours.")
    return EXIT_OK


def cmd_import(args) -> int:
    whatsapp = _mod("importer_whatsapp")
    source = args.source or ("whatsapp" if whatsapp.detect(args.path) else "telegram")
    try:
        if source == "whatsapp":
            result = whatsapp.import_export(args.path, me=args.me, title=args.title, tz=args.tz,
                                            date_order=args.date_order, copy_media=args.copy_media,
                                            include_forgotten=args.include_forgotten)
        else:
            if args.me is not None and not re.fullmatch(r"-?[0-9]+", args.me.strip()):
                raise CliError("--me for a Telegram export is your numeric user id.")
            me = int(args.me) if args.me is not None else None
            result = _mod("importer").import_export(args.path, copy_media=args.copy_media, me=me,
                                                    include_forgotten=args.include_forgotten)
    except status.LockBusy as exc:
        raise CliError(str(exc), EXIT_BUSY) from None
    except _mod("db").OtherAccount as exc:
        raise CliError(str(exc), EXIT_AUTH) from None
    if args.json:
        _json(result)
        return EXIT_OK
    chats = result["chats"]
    for c in chats:
        if c.get("skipped"):
            say(f"{c['title']}: skipped, {c['skipped']}")
            continue
        extra = [f"{c['already_present']} already archived"] if c["already_present"] else []
        if c["media_copied"] or c["media_missing"]:
            extra.append(f"{c['media_copied']} files copied, {c['media_missing']} not in the export")
        if c["skipped_pre_upgrade"]:
            extra.append(f"{c['skipped_pre_upgrade']} from the pre-upgrade group left out")
        if c.get("skipped_invalid"):
            extra.append(f"{c['skipped_invalid']} malformed messages skipped")
        say(f"{c['title']} ({c['kind']}, {c['chat_id']}): {c['imported']} imported"
            + (f"; {'; '.join(extra)}" if extra else ""))
    total = sum(c.get("imported", 0) for c in chats)
    say(f"Imported {total:,} messages from {len(chats)} chat{'' if len(chats) == 1 else 's'}. Browse them with `dotiphant open`.")
    if source == "whatsapp":
        if any(c.get("date_order_guessed") for c in chats):
            note("The dates could be read day-first or month-first; day-first was used. "
                 "If dates look wrong, import again with --date-order mdy.")
        if result.get("owner_id") is None:
            note('Your own messages are not marked as yours: import again with --me "Your Name" as the export shows it.')
    elif result.get("owner_id") is None:
        note("Your own messages are not marked as yours: import again with --me YOUR_USER_ID to fix that.")
    return EXIT_OK


def cmd_home(args) -> int:
    say(f"Dotiphant {__version__}: your Telegram chats, searchable on your machine.")
    ov = _overview()
    connected = config.has_credentials() and config.has_session()
    if not ov["chats"] and not connected:
        say()
        say("Not set up yet. Start with:")
        say("  dotiphant open     set up in your browser (about 5 minutes)")
        say("  dotiphant setup    the same setup in the terminal")
        say("  dotiphant demo     look around with made-up chats first")
        say()
        say("All commands: dotiphant --help")
        return EXIT_OK
    say(f"Archive: {ov['chats']:,} chats, {ov['messages']:,} messages. "
        f"Last sync: {_describe_sync(status.read_status(), ov)}.")
    if not connected:
        say("Telegram is not connected, so no new messages are fetched. "
            "Connect it with `dotiphant open` (in the browser) or `dotiphant setup`.")
    say()
    say("Try:")
    say("  dotiphant open          browse and search in the browser")
    if connected:
        say("  dotiphant sync          fetch new messages now")
    say("  dotiphant connect       let your AI assistant read the archive")
    say("All commands: dotiphant --help")
    return EXIT_OK


# --- argument parsing --------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dotiphant",
        description="Archive your Telegram chats locally and let your AI assistant read them.",
        epilog="New here? Run `dotiphant open` to set up in your browser, or `dotiphant setup` in the terminal. "
               "Not affiliated with Telegram.",
    )
    p.add_argument("--version", action="version", version=f"dotiphant {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name: str, func: Callable, help: str, **kw) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help, description=help, **kw)
        sp.set_defaults(func=func)
        return sp

    s = add("setup", cmd_setup, "guided setup: credentials, login, first sync, AI apps, background refresh",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="Steps:\n"
                   "  1. Telegram API id and hash: create them free at https://my.telegram.org\n"
                   "  2. Log in by scanning a QR code in the Telegram app (or --phone)\n"
                   "  3. Choose what to archive (people, groups, channels, how far back) and what AI apps may read\n"
                   "  4. First sync (stop any time with Ctrl-C; the next sync continues)\n"
                   "  5. Connect the AI apps found on this computer\n"
                   "  6. Turn on background refresh\n"
                   "Safe to run again: finished steps are skipped. Just looking? Try `dotiphant demo`.\n"
                   "Prefer a browser? `dotiphant open` does the same setup there.")
    s.add_argument("--api-id", help="Telegram api_id (or set DOTIPHANT_API_ID)")
    s.add_argument("--api-hash", help="Telegram api_hash (or set DOTIPHANT_API_HASH)")
    s.add_argument("--phone", nargs="?", const="", default=None,
                   help="log in with a code sent to this phone number instead of a QR code")
    s.add_argument("--yes", "-y", action="store_true", help="accept the suggested answers without asking")
    s.add_argument("--no-sync", action="store_true", help="skip the first sync")
    s.add_argument("--reconfigure", action="store_true", help="ask the archive questions again")
    s.add_argument("--new-credentials", action="store_true",
                   help="forget the saved API id, hash and login, and ask for new ones")
    s.add_argument("--web", action="store_true", help="do the setup in your browser instead of the terminal")
    s.add_argument("--port", type=int, help="with --web: viewer port on 127.0.0.1 (default 8765)")
    s.add_argument("--no-browser", action="store_true", help="with --web: print the private setup link instead")

    s = add("login", cmd_login, "log in to Telegram (QR code by default)")
    s.add_argument("--phone", nargs="?", const="", default=None, help="log in with a code sent to your phone")

    s = add("logout", cmd_logout, "end the Telegram session and remove it from this computer")
    s.add_argument("--forget-credentials", action="store_true",
                   help="also remove the api_id and api_hash, the viewer key, the MCP token and approved remote apps")

    s = add("status", cmd_status, "account, archive size, last sync, background refresh")
    s.add_argument("--offline", action="store_true", help="do not contact Telegram to check the login")
    s.add_argument("--json", action="store_true", help="print JSON instead of text")

    add("doctor", cmd_doctor, "check the installation and print fixes")

    s = add("sync", cmd_sync, "fetch new messages (all chats, or the ones you name)",
            epilog="Exit codes: 0 done; 3 done, but some chats could not be read (they are retried next time); "
                   "2 not logged in, or the archive belongs to another Telegram account; "
                   "75 another sync is running; 1 the sync failed.")
    s.add_argument("chats", nargs="*", metavar="CHAT", help="chat id, @username or title")
    s.add_argument("--quiet", "-q", action="store_true", help="no progress output")
    s.add_argument("--max-minutes", type=float, help="stop after this many minutes (continues next time)")
    s.add_argument("--all-history", action="store_true", help="load the full history, ignoring the day limit")

    s = add("avatars", cmd_avatars, "fetch new or changed chat profile photos, without a full sync")
    s.add_argument("chats", nargs="*", metavar="CHAT", help="chat id, @username or title (default: all chats)")
    s.add_argument("--refresh", action="store_true", help="download every photo again, even unchanged ones")
    s.add_argument("--quiet", "-q", action="store_true", help="no progress output")

    s = add("media", cmd_media, "download photos, files and voice notes of a chat")
    s.add_argument("chat", metavar="CHAT")
    s.add_argument("message_ids", nargs="*", type=int, metavar="MESSAGE_ID")
    s.add_argument("--quiet", "-q", action="store_true")

    for name in ("open", "serve"):
        s = add(name, cmd_open, "open the archive viewer in your browser" if name == "open" else "same as open")
        s.add_argument("--port", type=int, help="viewer port on 127.0.0.1 (default 8765, or the next free one)")
        s.add_argument("--no-browser", action="store_true", help="print the private viewer link instead of opening a browser")

    add("stop", cmd_stop, "stop a running viewer (and the demo viewer), for example after an update")

    s = add("mcp", cmd_mcp, "run the MCP server for AI apps (stdio by default)")
    s.add_argument("--http", action="store_true", help="serve over HTTP on 127.0.0.1 with a bearer token")
    s.add_argument("--port", type=int, default=8766, help="HTTP port (default 8766)")
    s.add_argument("--token", help="bearer token to require (default: a random token stored in the keychain)")
    s.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    s.add_argument("--allow-remote", action="store_true",
                   help="allow a non-loopback --host (exposes your archive to anyone with the token)")
    s.add_argument("--show-token", action="store_true",
                   help="print the bearer token for the HTTP server (alone: print it and exit)")
    s.add_argument("--public-url", metavar="URL",
                   help="your HTTPS tunnel address (https://abc.ngrok.app) for ChatGPT and claude.ai; see docs/remote-access.md")
    s.add_argument("--pair", action="store_true", help="show the one-time pairing code that approves an app (a new one once used)")
    s.add_argument("--clients", action="store_true", help="list apps approved through OAuth")
    s.add_argument("--revoke", metavar="CLIENT_ID", help="remove an approved app, or 'all'")
    s.add_argument("--approve", metavar="REQUEST_ID",
                   help="approve an app waiting on the approval page, after checking where it returns to")

    s = add("connect", cmd_connect, "add Dotiphant to Claude Desktop, Claude Code, Codex or Cursor")
    s.add_argument("client", nargs="?", metavar="APP",
                   help="claude-desktop, claude-code, codex, cursor or all (omit to list)")
    s.add_argument("--dry-run", action="store_true", help="show what would change")
    s.add_argument("--print", action="store_true", help="print the config snippet instead of writing it")

    s = add("chats", cmd_chats, "list archived chats")
    s.add_argument("filter", nargs="?", help="only chats whose title or username contains this")
    s.add_argument("--kind", choices=["private", "bot", "group", "supergroup", "channel"])
    s.add_argument("--active", metavar="SINCE", help="only chats active since, like 7d or 2026-09-01")
    s.add_argument("--limit", type=int, default=50, help="at most this many chats (default 50)")
    s.add_argument("--json", action="store_true", help="print JSON instead of text")

    s = add("search", cmd_search, "full-text search across all chats")
    s.add_argument("query")
    s.add_argument("--chat", help="limit to one chat (id, @username or title)")
    s.add_argument("--sender", help="limit to a sender name or @username")
    s.add_argument("--since", help="like 24h, 7d or 2026-09-01")
    s.add_argument("--until")
    s.add_argument("--limit", type=int, default=20, help="at most this many results (default 20)")
    s.add_argument("--json", action="store_true", help="print JSON instead of text")

    s = add("export", cmd_export, "print a chat as Markdown (pipe it into an AI or save with --out)")
    s.add_argument("chat", metavar="CHAT")
    s.add_argument("--since", help="like 7d or 2026-09-01")
    s.add_argument("--until")
    s.add_argument("--limit", type=int, help="at most this many messages (newest)")
    s.add_argument("--out", "-o", metavar="FILE")

    s = add("recent", cmd_recent, "digest of what happened recently")
    s.add_argument("--since", default="24h", help="like 24h, 7d or 2026-09-01 (default 24h)")
    s.add_argument("--json", action="store_true", help="print JSON instead of text")

    s = add("schedule", cmd_schedule, "background refresh: install, remove or status")
    s.add_argument("action", choices=["install", "remove", "status"], nargs="?", default="status")
    s.add_argument("--every", type=int, metavar="MINUTES")

    s = add("demo", cmd_demo, "try the viewer with made-up chats, no Telegram login needed")
    s.add_argument("--port", type=int, help=f"viewer port on 127.0.0.1 (default {DEMO_PORT}, or the next free one)")
    s.add_argument("--no-browser", action="store_true", help="print the private viewer link instead of opening a browser")
    s.add_argument("--no-open", action="store_true", help="only create the demo data")

    s = add("import", cmd_import, "add a Telegram Desktop JSON export or a WhatsApp chat export to the archive (experimental)")
    s.add_argument("path", metavar="PATH", help="result.json or its folder, or a WhatsApp chat .txt, .zip or folder")
    s.add_argument("--source", choices=["telegram", "whatsapp"], help="the export's app (default: detected)")
    s.add_argument("--copy-media", action="store_true", help="copy exported photos and files into the archive")
    s.add_argument("--me", metavar="ME",
                   help="to mark your own messages: your Telegram user id, or your name as the WhatsApp export shows it")
    s.add_argument("--title", help="WhatsApp: the chat name (default: from the file name)")
    s.add_argument("--tz", metavar="ZONE", help="WhatsApp: time zone of the export's times, like Europe/Berlin (default: this computer's)")
    s.add_argument("--date-order", choices=["dmy", "mdy"], help="WhatsApp: day-first or month-first dates, when the file does not tell")
    s.add_argument("--include-forgotten", action="store_true",
                   help="also import chats removed with `dotiphant forget` or excluded in settings"
                        " (they stay excluded from syncs)")
    s.add_argument("--json", action="store_true", help="print JSON instead of text")

    s = add("ai", cmd_ai, "choose which chats AI apps may read",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="Examples:\n"
                   "  dotiphant ai                    show the mode and the hidden or visible chats\n"
                   "  dotiphant ai hide \"Family\"      keep a chat away from AI apps\n"
                   "  dotiphant ai show @dana         let AI apps read a chat again\n"
                   "  dotiphant ai mode selected      AI apps read only the chats you allow\n"
                   "Only AI apps (the MCP server) are limited. The viewer and the CLI show every chat.")
    ai = s.add_subparsers(dest="ai_action", metavar="ACTION")
    a = ai.add_parser("list", help="show the mode and the hidden or visible chats",
                      description="Show the mode and the hidden or visible chats.")
    a.add_argument("--json", action="store_true", help="print JSON instead of text")
    a = ai.add_parser("hide", help="hide chats from AI apps", description="Hide chats from AI apps.")
    a.add_argument("chats", nargs="+", metavar="CHAT", help="chat id, @username or part of the title")
    a = ai.add_parser("show", help="let AI apps read chats", description="Let AI apps read chats.")
    a.add_argument("chats", nargs="+", metavar="CHAT", help="chat id, @username or part of the title")
    a = ai.add_parser("mode", help="all: every chat except hidden ones; selected: only chats you allow",
                      description="all: AI apps read every chat except the hidden ones. "
                                  "selected: AI apps read only the chats you allow.")
    a.add_argument("mode", choices=["all", "selected"])

    s = add("forget", cmd_forget, "delete a chat, its messages and media from the archive, and stop syncing it")
    s.add_argument("chat", metavar="CHAT", help="chat id, @username or part of the title")
    s.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", cmd_home)
    try:
        return func(args) or EXIT_OK
    except CliError as exc:
        note(f"dotiphant: {exc}")
        return exc.code
    except KeyboardInterrupt:
        note("\nStopped.")
        return EXIT_INTERRUPTED
    except ValueError as exc:  # bad time ranges and similar input problems
        note(f"dotiphant: {exc}")
        return EXIT_ERROR
    except sqlite3.Error as exc:
        note(f"dotiphant: the archive at {config.db_path()} could not be read ({exc}). Run `dotiphant doctor`.")
        return EXIT_ERROR
    except BrokenPipeError:  # output piped into `head` and similar
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return EXIT_OK
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("dotiphant"):
            note(f"dotiphant: this command needs {exc.name}, which is missing from this install.")
            return EXIT_ERROR
        raise
