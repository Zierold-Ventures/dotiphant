"""Sync status for every reader (CLI, viewer, MCP server), and background jobs.

This module stays light (no Telethon import), so the viewer and the MCP server load fast.
sync.py writes status.json during a run and holds sync.lock while it runs; everything
else reads the state through read_status().
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

from . import config

STALE_MESSAGE = "The last sync stopped before it finished. The next sync continues where it stopped."


def _idle() -> dict[str, Any]:
    return {"state": "idle", "stage": None, "chat": None, "chats_done": 0, "chats_total": 0,
            "new_messages": 0, "started_at": None, "finished_at": None, "message": None, "pid": None,
            "errors": 0, "failed_chats": []}


# --- the sync lock -------------------------------------------------------------------

if os.name == "nt":
    import msvcrt

    def try_lock(f) -> bool:
        """Non-blocking exclusive lock on the first byte of an open file."""
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def try_lock(f) -> bool:
        """Non-blocking exclusive lock on an open file."""
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def unlock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


class LockBusy(RuntimeError):
    """Another Dotiphant process holds sync.lock (a sync, a download, an import or a forget)."""


@contextlib.contextmanager
def holding_sync_lock(message: str = "A sync is running. Try again when it finishes.") -> Iterator[None]:
    """Hold sync.lock for the block, or raise LockBusy at once when another process has it."""
    with open(config.lock_path(), "a+") as f:
        if not try_lock(f):
            raise LockBusy(message)
        try:
            yield
        finally:
            unlock(f)


def lock_held() -> bool:
    """True while some process holds sync.lock, that is while a sync runs."""
    path = config.data_dir_path() / "sync.lock"
    if not path.exists():
        return False
    with open(path, "a+") as f:
        if not try_lock(f):
            return True
        unlock(f)
        return False


# --- reading the status ----------------------------------------------------------------

def pid_alive(pid: Any) -> bool:
    """True when a process with this id exists (also when it belongs to another user)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        # On Windows os.kill(pid, 0) does not probe: it terminates the process.
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_alive(pid: int) -> bool:
    """pid_alive on Windows: open the process for a status query and read its exit code."""
    import ctypes

    query_limited_information, still_active, access_denied = 0x1000, 259, 5
    if pid > 0xFFFFFFFF:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(query_limited_information, 0, pid)
    if not handle:
        return ctypes.get_last_error() == access_denied  # it exists, but we may not look at it
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def read_status() -> dict[str, Any]:
    """The current or last sync state from status.json, with every key present.

    A "running" or "waiting" state whose process is gone, or that no longer holds the
    lock, is reported as idle with `stale: True` (the sync crashed or was killed).
    """
    status = _idle()
    try:
        data = json.loads((config.data_dir_path() / "status.json").read_text())
    except (OSError, ValueError):
        return status
    if isinstance(data, dict):
        status.update(data)
    if is_running(status) and (not pid_alive(status.get("pid")) or not lock_held()):
        status.update(state="idle", stale=True, message=STALE_MESSAGE)
    return status


def is_running(status: dict[str, Any]) -> bool:
    return status.get("state") in ("running", "waiting")


# --- background jobs -----------------------------------------------------------------------

def start_background(args: list[str], log_name: str = "sync") -> subprocess.Popen:
    """Start `python -m dotiphant ARGS` detached from this process, output to logs/<log_name>.log."""
    log_dir = config.data_dir() / "logs"
    log_dir.mkdir(exist_ok=True)
    with open(log_dir / f"{log_name}.log", "ab") as log:
        return subprocess.Popen(
            [sys.executable, "-m", "dotiphant", *args],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )
