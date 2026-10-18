"""The light status module, and reads that must not create files."""
import json
import os

import pytest

from dotiphant import config, db, status


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    return tmp_path / "home"


def test_pid_alive(monkeypatch):
    assert status.pid_alive(os.getpid())
    assert not status.pid_alive(None) and not status.pid_alive(0) and not status.pid_alive(True)

    def kill(err):
        def raise_(pid, sig):
            raise err
        return raise_

    monkeypatch.setattr(status.os, "kill", kill(PermissionError()))
    assert status.pid_alive(12345)  # exists, owned by someone else
    monkeypatch.setattr(status.os, "kill", kill(ProcessLookupError()))
    assert not status.pid_alive(12345)
    monkeypatch.setattr(status.os, "kill", kill(OSError()))
    assert not status.pid_alive(12345)


class FakeKernel32:
    """kernel32 as _windows_pid_alive uses it, for processes in `running` (id -> exit code)."""

    class Fn:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    def __init__(self, running: dict[int, int], denied: set[int]):
        self.last_error, self.closed = 0, []

        def open_process(access, inherit, pid):
            if pid in running:
                return 1000 + pid
            self.last_error = 5 if pid in denied else 87
            return None

        def exit_code(handle, code):
            code._obj.value = running[handle - 1000]
            return 1

        self.OpenProcess, self.GetExitCodeProcess = self.Fn(open_process), self.Fn(exit_code)
        self.CloseHandle = self.Fn(self.closed.append)


def test_pid_alive_never_calls_os_kill_on_windows(monkeypatch):
    import ctypes

    kernel32 = FakeKernel32(running={100: 259, 101: 0}, denied={200})
    monkeypatch.setattr(status.os, "name", "nt")
    monkeypatch.setattr(status.os, "kill", lambda pid, sig: pytest.fail("os.kill ends the process on Windows"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: kernel32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: kernel32.last_error, raising=False)
    assert status.pid_alive(100)  # still running
    assert not status.pid_alive(101)  # exited, its handle still open somewhere
    assert status.pid_alive(200)  # exists, owned by someone else
    assert not status.pid_alive(300)  # no such process
    assert not status.pid_alive(2**40) and not status.pid_alive(None)
    assert kernel32.closed == [1100, 1101]


def test_read_status_defaults_and_stale_runs(home):
    st = status.read_status()
    assert st["state"] == "idle" and st["pid"] is None and not home.exists()
    config.write_private(config.status_path(), json.dumps({"state": "running", "pid": os.getpid()}))
    st = status.read_status()  # alive, but not holding the lock: a crashed run
    assert st["state"] == "idle" and st["stale"] and "stopped before it finished" in st["message"]
    with open(config.lock_path(), "a+") as lock:
        assert status.try_lock(lock)
        assert status.read_status()["state"] == "running"
        assert status.is_running(status.read_status())
        status.unlock(lock)
    config.write_private(config.status_path(), "[1, 2]")
    assert status.read_status()["state"] == "idle"


def test_readonly_connect_creates_nothing(home):
    conn = db.connect(readonly=True)
    try:
        assert conn.execute("SELECT count(*) FROM chats").fetchone()[0] == 0
        with pytest.raises(Exception):
            conn.execute("INSERT INTO meta VALUES('a','b')")
    finally:
        conn.close()
    assert not (home / "dotiphant.db").exists()


def test_reading_a_secret_creates_nothing(home):
    assert config.get_secret("session") is None
    config.delete_secret("session")
    assert not (home / "secrets").exists()
    config.set_secret("session", "s")
    assert (home / "secrets" / "session").read_text() == "s"


def test_secret_store_setting(monkeypatch, capsys):
    assert config.secret_store() == "file" and config.secrets_backend_name() == "file (0600)"
    monkeypatch.setenv("DOTIPHANT_SECRETS", " Keyring ")
    assert config.secret_store() == "keyring"
    monkeypatch.delenv("DOTIPHANT_SECRETS")
    assert config.secret_store() == "keyring"  # the default
    monkeypatch.setenv("DOTIPHANT_SECRETS", "0")
    with pytest.raises(ValueError, match="'keyring' or 'file', not '0'"):
        config.get_secret("session")
    from dotiphant import cli
    assert cli.main(["status"]) != 0
    assert "DOTIPHANT_SECRETS must be" in capsys.readouterr().err


def test_save_credentials(home):
    with pytest.raises(ValueError, match="3 to 12 digits"):
        config.save_credentials("12", "a" * 32)
    with pytest.raises(ValueError, match="32 characters"):
        config.save_credentials("12345", "xyz")
    config.save_credentials(" 12345 ", "A" * 32)
    config.set_secret("session", "s")
    config.save_credentials("12345", "a" * 32)  # unchanged: the login stays
    assert config.get_secret("session") == "s"
    config.save_credentials("54321", "a" * 32)  # another app: its session is gone
    assert config.get_secret("session") is None
    assert config.api_credentials() == (54321, "a" * 32)


def test_start_background_logs_to_the_data_folder(home, monkeypatch):
    seen = {}

    def popen(args, **kw):
        seen.update(args=args, **kw)
        return "proc"

    monkeypatch.setattr(status.subprocess, "Popen", popen)
    assert status.start_background(["media", "42"], log_name="media") == "proc"
    assert seen["args"][1:] == ["-m", "dotiphant", "media", "42"] and seen["start_new_session"] is True
    assert (home / "logs" / "media.log").exists()
