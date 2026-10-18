"""Find out who holds a viewer port, and start, reuse or stop Dotiphant viewers safely.

Every viewer response carries these headers:
- X-Dotiphant-Port-Owner: dotiphant
- X-Dotiphant-Build: a short hash of the installed package (version and file contents). It
  changes when a reinstall puts new files on disk, so an older viewer still running with the
  old code in memory can be told apart from the current one.
- X-Dotiphant-Folder: an HMAC of the data folder path, keyed with the viewer key. Only someone
  who holds the key can match it to a folder, so it reveals nothing about the path.

This module uses only the standard library, so `dotiphant open` stays quick to start. It never
sends the viewer key to a program that did not identify itself as a Dotiphant viewer for the
same data folder, and it never stops a process by pid.
"""
from __future__ import annotations

import hashlib
import hmac
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

OWNER_HEADER = "X-Dotiphant-Port-Owner"
BUILD_HEADER = "X-Dotiphant-Build"
FOLDER_HEADER = "X-Dotiphant-Folder"
OWNER = "dotiphant"
PORT_RANGE = 20  # how many ports above the default to try
PROBE_TIMEOUT = 1.5
STOP_WAIT = 5.0

PACKAGE_DIR = Path(__file__).resolve().parent


# --- build identity ------------------------------------------------------------------

def _package_files() -> list[Path]:
    return sorted(p for p in PACKAGE_DIR.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo"))


def _stat_signature() -> tuple:
    """Cheap fingerprint of the files on disk: names, sizes and modification times."""
    out = []
    for p in _package_files():
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((str(p.relative_to(PACKAGE_DIR)), st.st_size, st.st_mtime_ns))
    return tuple(out)


def compute_build_id() -> str:
    """A short hash of the package version and the contents of its files, as they are on disk now."""
    h = hashlib.sha256(__version__.encode())
    for p in _package_files():
        try:
            data = p.read_bytes()
        except OSError:
            continue
        h.update(str(p.relative_to(PACKAGE_DIR)).encode() + b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()[:16]


BUILD_ID = compute_build_id()  # the code this process loaded
_disk = {"sig": _stat_signature(), "build": BUILD_ID, "checked": time.monotonic()}
CHECK_EVERY = 5.0


def disk_build_id(now: float | None = None) -> str:
    """The build id of the files on disk, checked at most every few seconds.

    Only a change in names, sizes or modification times leads to reading the files again.
    """
    now = time.monotonic() if now is None else now
    if now - _disk["checked"] >= CHECK_EVERY:
        _disk["checked"] = now
        sig = _stat_signature()
        if sig != _disk["sig"]:
            _disk["sig"] = sig
            _disk["build"] = compute_build_id()
    return _disk["build"]


def is_stale() -> bool:
    """True when the installed files changed after this process loaded its code."""
    return disk_build_id() != BUILD_ID


def folder_id(home: Path | str, key: str) -> str:
    """Identifies a data folder to holders of the viewer key, and to nobody else."""
    path = str(Path(home).expanduser().resolve())
    return hmac.new(key.encode(), path.encode(), hashlib.sha256).hexdigest()[:16]


def identity_headers(home: Path | str, key: str) -> dict[str, str]:
    return {OWNER_HEADER: OWNER, BUILD_HEADER: BUILD_ID, FOLDER_HEADER: folder_id(home, key)}


# --- probing a port ------------------------------------------------------------------

def port_is_free(port: int) -> bool:
    """True when a viewer could listen on 127.0.0.1:port right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # like uvicorn
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@dataclass
class PortOwner:
    kind: str  # "dotiphant", "legacy" (a Dotiphant viewer from before these headers) or "other"
    build: str = ""
    folder: str = ""


def probe(port: int, timeout: float = PROBE_TIMEOUT) -> PortOwner:
    """Ask whatever listens on the port who it is. Sends no secrets."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"User-Agent": "dotiphant"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback only)
            status, headers, body = resp.status, resp.headers, resp.read(4096)
    except urllib.error.HTTPError as exc:
        status, headers = exc.code, exc.headers
        try:
            body = exc.read(4096)
        except Exception:
            body = b""
    except Exception:
        return PortOwner("other")
    if (headers.get(OWNER_HEADER) or "").strip().lower() == OWNER:
        return PortOwner("dotiphant", build=headers.get(BUILD_HEADER) or "", folder=headers.get(FOLDER_HEADER) or "")
    if status == 401 and b"dotiphant open" in body and headers.get("content-security-policy"):
        return PortOwner("legacy")
    return PortOwner("other")


def request_shutdown(port: int, key: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """POST /api/shutdown with the viewer key. True when the viewer agreed to stop."""
    origin = f"http://127.0.0.1:{port}"
    req = urllib.request.Request(
        f"{origin}/api/shutdown", data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {key}", "Origin": origin, "Content-Type": "application/json",
                 "User-Agent": "dotiphant"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback only)
            return resp.status == 200
    except Exception:
        return False


def wait_until_free(port: int, seconds: float = STOP_WAIT) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if port_is_free(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def find_it(port: int) -> str:
    return f"`lsof -i :{port}` (macOS and Linux) shows the program that uses it"


# --- deciding what `dotiphant open` does -------------------------------------------------

class PortError(Exception):
    """The viewer cannot start on a port, with an explanation the user can act on."""


@dataclass
class Plan:
    action: str  # "start" a new viewer, or "reuse" the one that runs
    port: int


def plan(port: int, home: Path | str, key: str, explicit: bool = False,
         log: Callable[[str], None] = print) -> Plan:
    """Pick the port to run on, stopping an outdated viewer for the same data folder.

    `explicit` is True when the user chose the port with --port: then the viewer never moves
    to another port, it fails with an explanation instead.
    """
    mine = folder_id(home, key)
    last = port if explicit else port + PORT_RANGE
    for candidate in range(port, last + 1):
        if port_is_free(candidate):
            if candidate != port:
                log(f"Port {port} is busy, using {candidate}.")
            return Plan("start", candidate)
        owner = probe(candidate)
        if owner.kind == "dotiphant" and owner.folder == mine:
            if owner.build == BUILD_ID:
                return Plan("reuse", candidate)
            if request_shutdown(candidate, key) and wait_until_free(candidate):
                log("Stopped an older Dotiphant viewer and started the updated one.")
                return Plan("start", candidate)
            raise PortError(
                f"An older Dotiphant viewer is running on port {candidate} and did not stop. "
                f"Press Ctrl+C in the terminal where it runs, or find it: {find_it(candidate)}. "
                f"Or start this one on another port with --port.")
        if owner.kind == "dotiphant":
            msg = f"Port {candidate} has a Dotiphant viewer for another data folder."
        elif owner.kind == "legacy":
            msg = (f"An older Dotiphant viewer is running on port {candidate}, from a version that cannot be "
                   f"stopped from here. Press Ctrl+C in the terminal where it runs, or find it: {find_it(candidate)}.")
        else:
            msg = ""
        if explicit:
            if not msg:
                raise PortError(f"Port {candidate} is in use by another program. Choose another port with "
                                f"--port, or free it: {find_it(candidate)}.")
            raise PortError(f"{msg} Choose another port with --port.")
        if msg:
            log(msg + " Leaving it running.")
    raise PortError(f"Ports {port} to {last} are all in use. Choose a free port with --port.")


# --- `dotiphant stop` --------------------------------------------------------------------

@dataclass
class StopResult:
    stopped: list[int]
    failed: list[int]
    legacy: list[int]


def stop_all(ports: list[int], folders: dict[str, str]) -> StopResult:
    """Stop the Dotiphant viewers on these ports that serve one of `folders` (folder id -> key).

    Programs that are not Dotiphant viewers, and viewers for other data folders, are left alone.
    """
    result = StopResult([], [], [])
    for port in ports:
        if port_is_free(port):
            continue
        owner = probe(port)
        if owner.kind == "legacy":
            result.legacy.append(port)
            continue
        if owner.kind != "dotiphant" or owner.folder not in folders:
            continue
        if request_shutdown(port, folders[owner.folder]) and wait_until_free(port):
            result.stopped.append(port)
        else:
            result.failed.append(port)
    return result
