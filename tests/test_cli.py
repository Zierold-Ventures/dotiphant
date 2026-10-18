"""CLI wiring, the non-interactive setup path, read commands on a temp archive, and the schedule files.

Telegram, sync, connect, schedule and MCP modules are replaced with fakes through
sys.modules, so nothing here talks to Telegram or launchctl/systemctl.
"""
import json
import plistlib
import subprocess
import sys
import types
from importlib.metadata import version
from pathlib import Path

import pytest

from dotiphant import cli, config, db, schedule, viewer

HASH = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    monkeypatch.delenv("DOTIPHANT_API_ID", raising=False)
    monkeypatch.delenv("DOTIPHANT_API_HASH", raising=False)
    monkeypatch.setattr(cli.webbrowser, "open", lambda *a, **k: True)
    return config.data_dir()


@pytest.fixture(autouse=True)
def stops(monkeypatch):
    """Record viewer stop requests instead of probing real ports (a user's viewer may run there)."""
    calls: list[dict] = []

    def stop_all(ports, folders):
        calls.append({"ports": list(ports), "folders": dict(folders),
                      "viewer_token": config.get_secret("viewer_token")})
        return viewer.StopResult([8765], [], [])

    monkeypatch.setattr(viewer, "stop_all", stop_all)
    return calls


# --- fakes -------------------------------------------------------------------------

class FakeClient:
    def __init__(self):
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def log_out(self):
        FakeClient.logged_out += 1
        return True


@pytest.fixture
def fakes(monkeypatch):
    calls: dict[str, list] = {"login_qr": [], "login_phone": [], "run_sync": [], "connect": [], "install": []}
    FakeClient.logged_out = 0

    tg = types.ModuleType("dotiphant.telegram")
    tg.make_client = lambda **kw: (config.api_credentials(), FakeClient())[1]

    async def is_authorized(client):
        return bool(config.get_secret("session"))

    async def login_qr(client, show_url, get_password):
        calls["login_qr"].append(1)
        return "Test User"

    async def login_phone(client, phone, get_code, get_password):
        calls["login_phone"].append(phone)
        return "Test User"

    async def logout():
        config.delete_secret("session")
        return True

    async def account_id(client):
        return calls["account_id"]

    calls["account_id"] = 1
    tg.is_authorized, tg.login_qr, tg.login_phone, tg.logout = is_authorized, login_qr, login_phone, logout
    tg.account_id = account_id
    tg.save_session = lambda client: config.set_secret("session", "fake-session")

    sync = types.ModuleType("dotiphant.sync")

    class SyncBusy(RuntimeError):
        pass

    sync.SyncBusy = SyncBusy
    sync.behaviour = "ok"

    async def run_sync(chat_ids=None, *, client=None, settings=None, progress=None, max_seconds=None):
        calls["run_sync"].append({"chat_ids": chat_ids, "settings": settings, "max_seconds": max_seconds})
        if sync.behaviour == "busy":
            raise SyncBusy("busy")
        if sync.behaviour == "auth":
            raise config.SecretsUnavailable("no credentials")
        if sync.behaviour == "partial":
            return {"chats": 3, "new_messages": 5, "errors": [{"chat_id": 7, "error": "RuntimeError"}]}
        if progress:
            progress({"state": "running", "stage": "inventory", "chats_done": 1, "chats_total": 3,
                      "new_messages": 5, "chat": "Acme"})
        return {"state": "idle", "chats_done": 3, "new_messages": 5}

    async def download_media(chat_id, message_ids=None, *, client=None):
        calls.setdefault("media", []).append((chat_id, message_ids))
        return {"downloaded": len(message_ids or [1])}

    async def run_inventory(*, client=None, settings=None):
        calls.setdefault("inventory", []).append(1)
        return 0

    async def run_avatars(chat_ids=None, *, refresh=False, client=None, settings=None, progress=None, max_seconds=None):
        calls.setdefault("avatars", []).append({"chat_ids": chat_ids, "refresh": refresh})
        if sync.behaviour == "busy":
            raise SyncBusy("busy")
        if sync.behaviour == "auth":
            raise config.SecretsUnavailable("no credentials")
        if progress:
            progress({"state": "running", "stage": "avatars", "chats_done": 1, "chats_total": 2, "chat": "Acme"})
        return {"chats": 2, "downloaded": 1, "removed": 0, "failed": 1, "missing": [], "stopped": None, "message": None}

    sync.run_sync, sync.download_media, sync.run_inventory = run_sync, download_media, run_inventory
    sync.run_avatars = run_avatars

    conn_mod = types.ModuleType("dotiphant.connect")
    conn_mod.CLIENTS = {k: types.SimpleNamespace(label=k.title()) for k in ("claude-desktop", "claude-code", "codex", "cursor")}
    conn_mod.detect = lambda: ["claude-desktop", "codex"]
    conn_mod.is_connected = lambda name: name in [c for c, _ in calls["connect"]]

    def connect(name, dry_run=False):
        calls["connect"].append((name, dry_run))
        return f"Connected {name}."

    conn_mod.connect = connect
    conn_mod.snippet = lambda name: f"snippet for {name}"

    sched = types.ModuleType("dotiphant.schedule")
    sched.status = lambda: {"installed": bool(calls["install"]), "every_minutes": 15}
    sched.install = lambda every=None: calls["install"].append(every) or "installed"
    sched.remove = lambda: "removed"

    for name, mod in [("telegram", tg), ("sync", sync), ("connect", conn_mod), ("schedule", sched)]:
        monkeypatch.setitem(sys.modules, f"dotiphant.{name}", mod)
    calls["sync_module"] = sync
    return calls


@pytest.fixture
def archive(home):
    db.init()
    c = db.connect()
    now = "2026-10-01T10:00:00+00:00"
    c.execute("INSERT INTO chats(chat_id,kind,title,username,last_message_at,first_seen_at,updated_at)"
              " VALUES(-1001,'supergroup','Acme Launch','acme',?,?,?)", (now, now, now))
    c.execute("INSERT INTO chats(chat_id,kind,title,username,last_message_at,first_seen_at,updated_at)"
              " VALUES(42,'private','Dana Lee','dana','2026-09-30T08:00:00+00:00',?,?)", (now, now))
    c.execute("INSERT INTO senders VALUES(42,'Dana Lee','dana',0)")
    rows = [(-1001, 1, 42, 0, "2026-09-29T10:00:00+00:00", "Can we ship the launch page by Friday?"),
            (-1001, 2, None, 1, "2026-09-29T10:05:00+00:00", "Yes, Friday works"),
            (42, 1, 42, 0, "2026-09-30T08:00:00+00:00", "Coffee tomorrow?")]
    for row in rows:
        c.execute("INSERT INTO messages(chat_id,message_id,sender_id,out,date,text) VALUES(?,?,?,?,?,?)", row)
    c.commit()
    c.close()


# --- wiring ------------------------------------------------------------------------

@pytest.mark.parametrize("argv,func", [
    (["setup", "--yes", "--no-sync"], cli.cmd_setup),
    (["login", "--phone"], cli.cmd_login),
    (["logout"], cli.cmd_logout),
    (["status"], cli.cmd_status),
    (["doctor"], cli.cmd_doctor),
    (["sync", "Acme", "42", "--quiet", "--max-minutes", "10"], cli.cmd_sync),
    (["media", "Acme", "1", "2"], cli.cmd_media),
    (["open", "--no-browser"], cli.cmd_open),
    (["serve"], cli.cmd_open),
    (["mcp", "--http", "--port", "9000"], cli.cmd_mcp),
    (["connect", "codex", "--dry-run"], cli.cmd_connect),
    (["chats", "--kind", "private", "--active", "7d", "--json"], cli.cmd_chats),
    (["search", "launch", "--chat", "Acme", "--since", "7d"], cli.cmd_search),
    (["export", "Acme", "--since", "7d", "--out", "x.md"], cli.cmd_export),
    (["recent", "--since", "24h"], cli.cmd_recent),
    (["schedule", "install", "--every", "30"], cli.cmd_schedule),
    (["demo", "--no-browser"], cli.cmd_demo),
])
def test_parser_wiring(argv, func):
    args = cli.build_parser().parse_args(argv)
    assert args.func is func


def test_parser_details():
    p = cli.build_parser()
    a = p.parse_args(["sync", "Acme", "--max-minutes", "10", "--all-history"])
    assert a.chats == ["Acme"] and a.max_minutes == 10 and a.all_history
    assert p.parse_args(["setup"]).phone is None
    assert p.parse_args(["setup", "--phone"]).phone == ""
    assert p.parse_args(["setup", "--phone", "+491"]).phone == "+491"
    assert p.parse_args(["open"]).port is None  # 8765, or the next free port; an explicit --port never moves
    assert p.parse_args(["open", "--port", "8790"]).port == 8790
    assert p.parse_args(["mcp", "--http"]).port == 8766
    assert p.parse_args(["schedule"]).action == "status"


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert f"dotiphant {version('dotiphant')}" in capsys.readouterr().out


def test_python_dash_m_version():
    out = subprocess.run([sys.executable, "-m", "dotiphant", "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == f"dotiphant {version('dotiphant')}"


def test_no_args_suggests_setup(capsys, home):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Not set up yet" in out and "dotiphant open" in out and "dotiphant setup" in out
    assert not (Path(home) / "dotiphant.db").exists()


def test_no_args_with_an_imported_archive_shows_it(archive, capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Not set up yet" not in out
    assert "Archive: 2 chats, 3 messages" in out and "Telegram is not connected" in out and "dotiphant open" in out


def test_epilog_starts_with_open():
    assert "`dotiphant open`" in cli.build_parser().epilog


def test_read_commands_create_no_archive(home, capsys):
    """Reading an archive that does not exist yet shows empty states and writes nothing."""
    assert cli.main(["chats"]) == 0
    assert cli.main(["search", "launch"]) == 0
    assert cli.main(["recent"]) == 0
    assert cli.main(["status", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "No chats archived yet." in out and "0 chats, 0 messages" in out
    assert not (Path(home) / "dotiphant.db").exists()
    assert not (Path(home) / "secrets").exists()


# --- setup -------------------------------------------------------------------------

def test_setup_non_interactive(fakes, capsys, home):
    code = cli.main(["setup", "--api-id", "123456", "--api-hash", HASH.upper(), "--yes"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert config.api_credentials() == (123456, HASH)
    assert config.get_secret("session") == "fake-session"
    assert fakes["login_qr"] == [1]
    assert (home / "settings.json").exists()
    assert config.Settings.load().history_days == 365
    assert len(fakes["run_sync"]) == 1
    assert [c[0] for c in fakes["connect"]] == ["claude-desktop", "codex"]
    assert fakes["install"] == [15]
    assert "dotiphant open" in out
    assert "file" in out  # says where the secrets went

    # Re-running skips the finished steps.
    assert cli.main(["setup", "--yes", "--no-sync"]) == 0
    out = capsys.readouterr().out
    assert "already saved" in out and "already logged in" in out
    assert "already set" in out and "already installed" in out
    assert fakes["login_qr"] == [1]
    assert len(fakes["run_sync"]) == 1


def test_setup_reads_env_credentials_and_phone(fakes, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_API_ID", "777")
    monkeypatch.setenv("DOTIPHANT_API_HASH", HASH)
    assert cli.main(["setup", "--yes", "--no-sync", "--phone", "+4915100000"]) == 0
    assert config.get_secret("api_id") == "777"
    assert fakes["login_phone"] == ["+4915100000"]


def test_setup_without_credentials_fails_cleanly(fakes, capsys):
    assert cli.main(["setup", "--yes"]) == cli.EXIT_AUTH
    assert "--api-id" in capsys.readouterr().err


def _owned_by(account_id: int) -> None:
    db.init()
    conn = db.open_rw()
    db.set_meta(conn, "owner_id", str(account_id))
    conn.commit()
    conn.close()


def test_setup_stops_when_the_login_is_another_account(fakes, capsys):
    _owned_by(5)  # synced by account 5; the fake logs in as account 1
    code = cli.main(["setup", "--api-id", "123456", "--api-hash", HASH, "--yes"])
    err = capsys.readouterr().err
    assert code == cli.EXIT_AUTH
    assert db.OTHER_ACCOUNT in err
    assert fakes["run_sync"] == [], "no sync starts for the wrong account"
    # The login is undone, so the next login can use the right account.
    assert FakeClient.logged_out == 1 and config.get_secret("session") is None

    fakes["account_id"] = 5
    assert cli.main(["login"]) == 0
    assert config.get_secret("session") == "fake-session"


def test_login_warns_when_already_logged_in_as_another_account(fakes, capsys):
    config.save_credentials("123456", HASH)
    config.set_secret("session", "fake-session")
    _owned_by(5)
    assert cli.main(["login"]) == cli.EXIT_AUTH
    err = capsys.readouterr().err
    assert db.OTHER_ACCOUNT in err and "dotiphant logout" in err
    assert config.get_secret("session") == "fake-session" and FakeClient.logged_out == 0


def test_login_into_an_import_only_archive(fakes):
    db.init()  # no owner_id yet
    config.save_credentials("123456", HASH)
    assert cli.main(["login"]) == 0
    assert config.get_secret("session") == "fake-session"


def test_setup_rejects_bad_hash(fakes, capsys):
    assert cli.main(["setup", "--api-id", "123", "--api-hash", "nothex", "--yes"]) == cli.EXIT_AUTH
    assert "32 characters" in capsys.readouterr().err
    assert config.get_secret("api_hash") is None


def test_setup_interactive_settings(fakes, monkeypatch):
    answers = iter(["n", "", "y", "all", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    args = cli.build_parser().parse_args(["setup", "--reconfigure"])
    s = cli.step_settings(args)
    assert (s.include_private, s.include_groups, s.include_channels, s.history_days) == (False, True, True, None)
    assert config.Settings.load().history_days is None
    assert s.ai_access == "all"  # Enter keeps the default: AI apps read every chat


def test_setup_asks_which_chats_ai_apps_may_read(fakes, monkeypatch, capsys):
    answers = iter(["", "", "", "", "maybe", "choose"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    s = cli.step_settings(cli.build_parser().parse_args(["setup", "--reconfigure"]))
    assert s.ai_access == "selected" and config.Settings.load().ai_access == "selected"
    out = capsys.readouterr().out
    assert "Please answer all or choose." in out
    assert "dotiphant ai show CHAT" in out and "Visible to AI apps" in out


# --- sync --------------------------------------------------------------------------

def test_sync_exit_codes(fakes, archive, capsys):
    assert cli.main(["sync", "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    fakes["sync_module"].behaviour = "busy"
    assert cli.main(["sync", "--quiet"]) == cli.EXIT_BUSY
    fakes["sync_module"].behaviour = "auth"
    assert cli.main(["sync"]) == cli.EXIT_AUTH
    assert "dotiphant setup" in capsys.readouterr().err


def test_sync_resolves_chats_and_options(fakes, archive, capsys):
    assert cli.main(["sync", "Acme", "@dana", "-5", "--max-minutes", "2", "--all-history"]) == 0
    call = fakes["run_sync"][-1]
    assert sorted(call["chat_ids"]) == [-1001, -5, 42]
    assert call["max_seconds"] == 120
    assert call["settings"].history_days is None
    assert "5 new messages" in capsys.readouterr().out
    assert cli.main(["sync", "Nobody here"]) == 1


def test_sync_is_not_available_in_the_demo(fakes, archive, capsys):
    c = db.connect()
    with c:
        db.set_meta(c, "demo", "1")
    c.close()
    assert cli.main(["sync"]) == 1
    assert "not available in the demo" in capsys.readouterr().err
    assert not fakes["run_sync"]  # no failed-sync status lands in the demo archive


def test_progress_line():
    line = cli.progress_line({"state": "running", "stage": "history", "chats_done": 2, "chats_total": 9,
                              "new_messages": 1234, "chat": "Acme"})
    assert line == "Loading older history, 2/9 chats, 1,234 new messages: Acme"
    assert "slow down" in cli.progress_line({"state": "waiting", "stage": "recent", "message": "30s"})


def test_avatars_command(fakes, archive, capsys):
    assert cli.main(["avatars"]) == 0
    assert fakes["avatars"][-1] == {"chat_ids": None, "refresh": False}
    out = capsys.readouterr()
    assert "Profile photos: 2 chats checked, 1 downloaded, 1 could not be read" in out.out
    assert "Fetching profile photos" in out.err
    assert cli.main(["avatars", "Acme", "42", "--refresh", "--quiet"]) == 0
    assert fakes["avatars"][-1] == {"chat_ids": [42, -1001], "refresh": True}
    assert capsys.readouterr().out == ""
    fakes["sync_module"].behaviour = "busy"
    assert cli.main(["avatars"]) == cli.EXIT_BUSY
    fakes["sync_module"].behaviour = "auth"
    assert cli.main(["avatars"]) == cli.EXIT_AUTH
    assert "dotiphant setup" in capsys.readouterr().err
    c = db.connect()
    with c:
        db.set_meta(c, "demo", "1")
    c.close()
    fakes["sync_module"].behaviour = "ok"
    assert cli.main(["avatars"]) == 1 and "not available in the demo" in capsys.readouterr().err


def test_media(fakes, archive, capsys):
    assert cli.main(["media", "Acme", "1", "2"]) == 0
    assert fakes["media"] == [(-1001, [1, 2])]
    assert "Media for Acme Launch: 2 downloaded." in capsys.readouterr().out


def test_media_summary_reports_every_outcome():
    line = cli.media_summary({"downloaded": 3, "already_present": 2, "skipped_large": 1, "unavailable": 4, "failed": 5})
    assert line == ("3 downloaded, 2 already here, 1 skipped as too large (over 50 MB), "
                    "4 no longer available on Telegram, 5 failed")
    assert cli.media_summary({"downloaded": 0}) == "0 downloaded"


def test_media_is_not_available_in_the_demo(fakes, archive, capsys):
    c = db.connect()
    with c:
        db.set_meta(c, "demo", "1")
    c.close()
    assert cli.main(["media", "Acme"]) == 1
    assert "not available in the demo" in capsys.readouterr().err
    assert "media" not in fakes


def test_logout_reports_an_unreachable_telegram(fakes, capsys):
    config.set_secret("session", "s")

    async def offline():
        config.delete_secret("session")
        return False

    sys.modules["dotiphant.telegram"].logout = offline
    assert cli.main(["logout"]) == 0
    out = capsys.readouterr().out
    assert "Could not reach Telegram" in out and "Logged out" not in out
    assert config.get_secret("session") is None


def test_logout_forget_credentials_removes_every_secret(fakes, home, capsys):
    for name in ("api_id", "api_hash", "session", "viewer_token", "mcp_token"):
        config.set_secret(name, "x")
    (Path(home) / "oauth.json").write_text("{}")
    assert cli.main(["logout", "--forget-credentials"]) == 0
    assert "Logged out." in capsys.readouterr().out
    for name in ("api_id", "api_hash", "session", "viewer_token", "mcp_token"):
        assert config.get_secret(name) is None, name
    assert not (Path(home) / "oauth.json").exists()


def test_logout_forget_credentials_stops_viewers_before_deleting_the_key(fakes, home, stops, capsys):
    """Viewers are found by a header keyed with the viewer key, so they must stop while it exists."""
    config.set_secret("session", "s")
    config.set_secret("viewer_token", "old-key")
    config.set_secret("mcp_token", "old-mcp")
    assert cli.main(["logout", "--forget-credentials"]) == 0
    assert len(stops) == 1
    assert stops[0]["viewer_token"] == "old-key"
    assert stops[0]["folders"] == {viewer.folder_id(Path(home), "old-key"): "old-key"}
    out, err = capsys.readouterr()
    assert "Stopped the Dotiphant viewer on port 8765." in out
    assert "dotiphant mcp --http" in err and "--token" in err
    assert config.get_secret("viewer_token") is None


def test_logout_forget_credentials_without_keys_probes_nothing(fakes, stops, capsys):
    assert cli.main(["logout", "--forget-credentials"]) == 0
    assert stops == []
    assert "dotiphant mcp --http" not in capsys.readouterr().err


def test_plain_logout_leaves_viewers_running(fakes, stops):
    config.set_secret("viewer_token", "key")
    assert cli.main(["logout"]) == 0
    assert stops == [] and config.get_secret("viewer_token") == "key"


def test_stop_still_finds_this_folders_viewer(stops, home, capsys):
    config.set_secret("viewer_token", "key")
    assert cli.main(["stop"]) == 0
    assert viewer.folder_id(Path(home), "key") in stops[0]["folders"]
    assert "Stopped the Dotiphant viewer on port 8765." in capsys.readouterr().out


def test_new_api_credentials_end_the_old_login(fakes, monkeypatch):
    config.set_secret("api_id", "123456")
    config.set_secret("api_hash", HASH)
    config.set_secret("session", "old")
    # The same values again (setup is safe to re-run) keep the login.
    assert cli.main(["setup", "--api-id", "123456", "--api-hash", HASH, "--yes", "--no-sync"]) == 0
    assert config.get_secret("session") == "old"
    config.set_secret("session", "old")
    ended = []
    fake_logout = sys.modules["dotiphant.telegram"].logout

    async def logout():  # best effort: the old login is ended on Telegram with the old credentials
        ended.append(config.get_secret("api_id"))
        return await fake_logout()

    sys.modules["dotiphant.telegram"].logout = logout
    cli.step_credentials(cli.build_parser().parse_args(["setup", "--api-id", "654321", "--api-hash", HASH]))
    assert config.get_secret("session") is None
    assert ended == ["123456"]
    # --new-credentials ends the stored login on Telegram too.
    config.set_secret("session", "newer")
    args = cli.build_parser().parse_args(["setup", "--new-credentials"])
    args.yes = True
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(cli.CliError):  # no new values given: it asks, and without a terminal it stops
        cli.step_credentials(args)
    assert ended == ["123456", "654321"] and config.get_secret("session") is None


# --- read commands -----------------------------------------------------------------

def test_chats_output(archive, capsys):
    assert cli.main(["chats"]) == 0
    out = capsys.readouterr().out
    assert "Acme Launch (@acme)" in out and "Dana Lee" in out
    assert cli.main(["chats", "--kind", "private", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["chat_id"] for c in data] == [42]


def test_search_output(archive, capsys):
    assert cli.main(["search", "launch"]) == 0
    out = capsys.readouterr().out
    assert "Acme Launch" in out and "[launch]" in out
    assert cli.main(["search", "coffee", "--chat", "Dana", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["message_id"] == 1
    assert cli.main(["search", "launch", "--chat", "nobody"]) == 1


def test_ai_list_hide_show_and_mode(archive, capsys):
    assert cli.main(["ai"]) == 0
    out = capsys.readouterr().out
    assert "AI access: all chats." in out and "No chats are hidden" in out and "2 of 2 chats" in out
    assert cli.main(["ai", "hide", "Acme", "@dana"]) == 0
    out = capsys.readouterr().out
    assert "Acme Launch (-1001): hidden from AI apps." in out and "Dana Lee (42): hidden" in out
    assert config.Settings.load().ai_hidden_chats == [-1001, 42]
    assert cli.main(["ai", "show", "42"]) == 0
    assert "Dana Lee (42): visible to AI apps." in capsys.readouterr().out
    s = config.Settings.load()
    assert (s.ai_hidden_chats, s.ai_allowed_chats) == ([-1001], [42])
    assert cli.main(["ai", "list"]) == 0
    out = capsys.readouterr().out
    assert "Hidden from AI apps:" in out and "Acme Launch" in out and "1 of 2 chats are visible" in out
    assert cli.main(["ai", "mode", "selected"]) == 0
    out = capsys.readouterr().out
    assert "only chosen chats" in out and "New chats stay hidden" in out
    assert cli.main(["ai", "list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"mode": "selected", "chats": [{"chat_id": 42, "title": "Dana Lee"}]}
    assert cli.main(["ai", "mode", "all"]) == 0
    assert config.Settings.load().ai_access == "all"
    assert cli.main(["ai", "hide", "nobody"]) == 1
    assert "No archived chat matches" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["ai", "mode", "some"])
    # The owner's own search still finds hidden chats.
    assert cli.main(["search", "launch"]) == 0
    assert "Acme Launch" in capsys.readouterr().out


def test_export_stdout_and_file(archive, capsys, tmp_path):
    assert cli.main(["export", "Acme"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Acme Launch")
    assert "Can we ship the launch page by Friday?" in out
    target = tmp_path / "acme.md"
    assert cli.main(["export", "@acme", "--since", "2026-09-29T10:01:00", "--out", str(target)]) == 0
    text = target.read_text()
    assert "Yes, Friday works" in text and "Can we ship" not in text
    assert cli.main(["export", "Acme", "--since", "yesterday-ish"]) == 1


def test_recent(archive, capsys):
    assert cli.main(["recent", "--since", "3650d"]) == 0
    out = capsys.readouterr().out
    assert "Acme Launch (supergroup)" in out and "Coffee tomorrow?" in out


def test_status_offline(archive, capsys):
    assert cli.main(["status", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "2 chats, 3 messages" in out and "not set up" in out


def test_mcp_stdio_prints_nothing(monkeypatch, capsys):
    server = types.ModuleType("dotiphant.mcp_server")
    server.ran = False
    server.run_stdio = lambda: setattr(server, "ran", True)
    monkeypatch.setitem(sys.modules, "dotiphant.mcp_server", server)
    assert cli.main(["mcp"]) == 0
    assert server.ran
    assert capsys.readouterr().out == ""


def test_connect_list_and_print(fakes, capsys):
    assert cli.main(["connect"]) == 0
    out = capsys.readouterr().out
    assert "claude-desktop" in out and "found on this computer" in out and "not found" in out
    assert cli.main(["connect", "cursor", "--print"]) == 0
    assert "snippet for cursor" in capsys.readouterr().out
    assert cli.main(["connect", "all", "--dry-run"]) == 0
    assert fakes["connect"] == [("claude-desktop", True), ("codex", True)]
    assert cli.main(["connect", "notepad"]) == 1


def test_demo_seeds_and_serves(monkeypatch, tmp_path, capsys):
    demo_home = tmp_path / "demo"
    demo = types.ModuleType("dotiphant.demo")
    demo.demo_home = lambda: demo_home
    seeded = []

    def seed_demo(home):
        seeded.append(Path(home))
        Path(home).mkdir(parents=True, exist_ok=True)
        db.init(Path(home) / "dotiphant.db")

    demo.seed_demo = seed_demo
    web = types.ModuleType("dotiphant.web.app")
    served = []
    web.serve = lambda port=8765, open_browser=True: served.append((port, open_browser, config.data_dir()))
    monkeypatch.setattr(viewer, "port_is_free", lambda port: True)
    monkeypatch.setitem(sys.modules, "dotiphant.demo", demo)
    monkeypatch.setitem(sys.modules, "dotiphant.web.app", web)
    assert cli.main(["demo", "--no-browser"]) == 0
    assert seeded == [demo_home]
    assert served == [(8764, False, demo_home)]  # not 8765, so it can run next to `dotiphant open`


# --- schedule ----------------------------------------------------------------------

class Recorder:
    def __init__(self, fail: set[str] = frozenset()):
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        return types.SimpleNamespace(returncode=1 if args[1] in self.fail else 0, stdout="", stderr="")


def test_plist_content(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", "/tmp/dotiphant test")
    data = plistlib.loads(schedule.plist_content(20, python="/opt/py", log_dir=tmp_path / "logs"))
    assert data["Label"] == "com.dotiphant.sync"
    assert data["ProgramArguments"] == ["/opt/py", "-m", "dotiphant", "sync", "--quiet", "--max-minutes", "10"]
    assert data["StartInterval"] == 1200
    assert data["EnvironmentVariables"] == {"DOTIPHANT_HOME": "/tmp/dotiphant test"}
    assert data["StandardOutPath"] == str(tmp_path / "logs" / "sync.log")


def test_systemd_units(tmp_path):
    service, timer = schedule.systemd_units(15, python="/home/me/my env/python", log_dir=tmp_path,
                                            env={"DOTIPHANT_HOME": "/data"})
    assert 'ExecStart="/home/me/my env/python" -m dotiphant sync --quiet --max-minutes 10' in service
    assert "Environment=DOTIPHANT_HOME=/data" in service
    assert "OnUnitActiveSec=15min" in timer and "WantedBy=timers.target" in timer


def test_schedule_macos_install_remove(tmp_path):
    runner = Recorder()
    agents = tmp_path / "LaunchAgents"
    msg = schedule.install(5, platform="darwin", python="/opt/py", launch_agents_dir=agents,
                           log_dir=tmp_path / "logs", runner=runner, uid=501)
    path = agents / "com.dotiphant.sync.plist"
    assert path.exists() and "every 5 minutes" in msg
    assert ["launchctl", "bootstrap", "gui/501", str(path)] in runner.calls
    st = schedule.status(platform="darwin", launch_agents_dir=agents)
    assert st["installed"] and st["every_minutes"] == 5
    assert "removed" in schedule.remove(platform="darwin", launch_agents_dir=agents, runner=runner, uid=501)
    assert not path.exists()
    assert ["launchctl", "bootout", "gui/501/com.dotiphant.sync"] in runner.calls


def test_schedule_macos_falls_back_to_load(tmp_path):
    runner = Recorder(fail={"bootstrap", "bootout"})
    schedule.install(15, platform="darwin", launch_agents_dir=tmp_path, log_dir=tmp_path, runner=runner, uid=1)
    assert runner.calls[-1][:3] == ["launchctl", "load", "-w"]


def test_schedule_linux_install_remove(tmp_path):
    runner = Recorder()
    units = tmp_path / "systemd"
    schedule.install(30, platform="linux", python="/usr/bin/python3", systemd_user_dir=units,
                     log_dir=tmp_path, runner=runner)
    assert (units / "dotiphant-sync.service").exists()
    assert ["systemctl", "--user", "enable", "--now", "dotiphant-sync.timer"] in runner.calls
    assert schedule.status(platform="linux", systemd_user_dir=units)["every_minutes"] == 30
    schedule.remove(platform="linux", systemd_user_dir=units, runner=runner)
    assert not (units / "dotiphant-sync.timer").exists()


def test_schedule_windows_prints_command():
    msg = schedule.install(15, platform="win32", python="C:\\Py\\python.exe", runner=Recorder())
    assert "schtasks /Create" in msg and "/MO 15" in msg and "dotiphant sync --quiet" in msg
    assert schedule.status(platform="win32")["installed"] is None


def test_mcp_http_options(monkeypatch, capsys):
    server = types.ModuleType("dotiphant.mcp_server")
    runs = []
    server.http_token = lambda: "tok123"
    server.run_http = lambda host="127.0.0.1", port=8766, token=None, allow_remote=False, public_url=None: runs.append(
        (host, port, token, allow_remote))
    server.pairing_message = lambda: "Pairing code: 1234-5678"
    server.clients_text = lambda: "Authorized apps"
    server.revoke_clients = lambda cid: f"Revoked {cid}."
    monkeypatch.setitem(sys.modules, "dotiphant.mcp_server", server)
    assert cli.main(["mcp", "--show-token"]) == 0
    assert capsys.readouterr().out.strip() == "tok123"
    assert cli.main(["mcp", "--http", "--port", "9000"]) == 0
    assert runs[-1] == ("127.0.0.1", 9000, None, False)
    assert cli.main(["mcp", "--http", "--host", "0.0.0.0"]) == 1
    assert cli.main(["mcp", "--http", "--host", "0.0.0.0", "--allow-remote"]) == 0
    assert runs[-1] == ("0.0.0.0", 8766, None, True)
    assert "WARNING" in capsys.readouterr().err
    assert cli.main(["mcp", "--pair"]) == 0 and "1234-5678" in capsys.readouterr().out
    assert cli.main(["mcp", "--clients"]) == 0 and "Authorized apps" in capsys.readouterr().out
    assert cli.main(["mcp", "--revoke", "all"]) == 0 and "Revoked all." in capsys.readouterr().out
    assert len(runs) == 2


def test_unreadable_archive_is_explained(home, capsys):
    (Path(home) / "dotiphant.db").write_text("not a database")
    assert cli.main(["status", "--offline"]) == 1
    assert "could not be read" in capsys.readouterr().err


def test_log_paths_with_special_characters(tmp_path, monkeypatch):
    """Spaces, % and $ in the data folder must reach launchd and systemd unchanged."""
    logs = tmp_path / "My Data 100% & <stuff> $HOME"
    data = plistlib.loads(schedule.plist_content(15, python="/opt/py", log_dir=logs))
    assert data["StandardOutPath"] == str(logs / "sync.log")
    service, _ = schedule.systemd_units(15, python="/opt/my $py%/python", log_dir=logs, env={"DOTIPHANT_HOME": "/a b%"})
    # StandardOutput takes the rest of the line literally, except %-specifiers
    want = str(logs / "sync.log").replace("%", "%%")
    assert f"StandardOutput=append:{want}\n" in service
    assert f"StandardError=append:{want}\n" in service
    assert 'ExecStart="/opt/my $$py%%/python" -m dotiphant' in service
    assert 'Environment="DOTIPHANT_HOME=/a b%%"' in service
    with pytest.raises(ValueError):
        schedule.systemd_units(15, python="/opt/py", log_dir=tmp_path / "bad\nname")


def test_schedule_linux_mentions_linger(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "_linger_enabled", lambda: False)
    msg = schedule.install(30, platform="linux", systemd_user_dir=tmp_path / "u", log_dir=tmp_path, runner=Recorder())
    assert "loginctl enable-linger $USER" in msg
    monkeypatch.setattr(schedule, "_linger_enabled", lambda: True)
    msg = schedule.install(30, platform="linux", systemd_user_dir=tmp_path / "u", log_dir=tmp_path, runner=Recorder())
    assert "linger" not in msg


def test_linger_detection(tmp_path):
    (tmp_path / "me").touch()
    assert schedule._linger_enabled("me", linger_dir=tmp_path)
    assert not schedule._linger_enabled("someone", linger_dir=tmp_path)


def test_prompts_never_act_without_a_terminal(monkeypatch):
    """With no terminal attached, nobody answered, so side-effect prompts must say no."""
    from dotiphant import cli

    class NoTty:
        def isatty(self):
            return False

        def readline(self):
            return ""

    monkeypatch.setattr(cli.sys, "stdin", NoTty())
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(EOFError()))
    assert cli._confirm("Open the page in your browser now?", True) is False
    assert cli._confirm("Connect?", True, assume_yes=True) is True


def test_rejected_credentials_are_forgotten(monkeypatch, tmp_path):
    from dotiphant import cli, config

    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    config.set_secret("api_id", "123")
    config.set_secret("api_hash", "0" * 32)
    config.set_secret("session", "made-with-the-wrong-app")

    class ApiIdInvalidError(Exception):
        pass

    async def fail(phone):
        raise ApiIdInvalidError("The api_id/api_hash combination is invalid")

    monkeypatch.setattr(cli, "_login_async", fail)
    with pytest.raises(cli.CliError) as err:
        cli.do_login(None)
    assert "forgot them" in str(err.value)
    assert config.get_secret("api_id") is None and config.get_secret("api_hash") is None
    assert config.get_secret("session") is None


def test_new_credentials_flag_parses():
    from dotiphant import cli

    assert cli.build_parser().parse_args(["setup", "--new-credentials"]).new_credentials


def test_status_line_mentions_chats_a_sync_could_not_read():
    st = {"state": "idle", "finished_at": "2026-10-01T10:00:00+00:00", "new_messages": 4, "errors": 2}
    assert cli._describe_sync(st, {}) == ("2026-10-01 10:00 UTC, 4 new messages, "
                                          "2 chat(s) could not be read (retried next time)")


def test_sync_with_unreadable_chats_exits_with_the_partial_code(fakes, capsys):
    fakes["sync_module"].behaviour = "partial"
    assert cli.main(["sync"]) == cli.EXIT_PARTIAL == 3
    assert "1 chat(s) could not be read" in capsys.readouterr().out
    assert "Exit codes" in cli.build_parser()._subparsers._group_actions[0].choices["sync"].epilog


def test_systemd_counts_a_partial_sync_as_success(tmp_path):
    service, _ = schedule.systemd_units(15, python="/opt/py", log_dir=tmp_path, env={})
    assert f"SuccessExitStatus={cli.EXIT_PARTIAL}" in service
