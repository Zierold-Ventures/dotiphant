"""Background refresh: run `dotiphant sync --quiet` every few minutes.

macOS uses a LaunchAgent, Linux a systemd user timer, Windows a scheduled task (we print
the command instead of running it). Every path and the command runner can be overridden,
so tests never touch the real system.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from . import config

LABEL = "com.dotiphant.sync"
SYSTEMD_NAME = "dotiphant-sync"
EXIT_PARTIAL = 3  # `dotiphant sync` finished, but some chats could not be read (cli.EXIT_PARTIAL)
WINDOWS_TASK = "Dotiphant Sync"
MAX_MINUTES = 10

Runner = Callable[..., Any]


def _platform(platform: str | None) -> str:
    p = platform or sys.platform
    if p.startswith("darwin"):
        return "macos"
    if p.startswith("win"):
        return "windows"
    return "linux"


def sync_command(python: str | None = None, max_minutes: int = MAX_MINUTES) -> list[str]:
    return [python or sys.executable, "-m", "dotiphant", "sync", "--quiet", "--max-minutes", str(max_minutes)]


def _env() -> dict[str, str]:
    home = os.environ.get("DOTIPHANT_HOME")
    return {"DOTIPHANT_HOME": str(Path(home).expanduser())} if home else {}


def _log_dir(log_dir: Path | None) -> Path:
    path = log_dir or (config.data_dir() / "logs")
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- file content ------------------------------------------------------------------

def plist_content(every_minutes: int, python: str | None = None, log_dir: Path | None = None,
                  env: dict[str, str] | None = None) -> bytes:
    logs = _log_dir(log_dir)
    data: dict[str, Any] = {
        "Label": LABEL,
        "ProgramArguments": sync_command(python),
        "StartInterval": int(every_minutes) * 60,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "StandardOutPath": str(logs / "sync.log"),
        "StandardErrorPath": str(logs / "sync.log"),
    }
    env = _env() if env is None else env
    if env:
        data["EnvironmentVariables"] = env
    return plistlib.dumps(data)


def _systemd_quote(arg: str, exec_line: bool = False) -> str:
    """Quote one word for ExecStart= (exec_line) or Environment=.

    Both expand %-specifiers, so % becomes %%. ExecStart= also expands $VAR, so $ becomes $$.
    """
    if "\n" in arg or "\r" in arg:
        raise ValueError(f"Cannot put a line break into a systemd unit: {arg!r}")
    if exec_line:
        arg = arg.replace("$", "$$")
    if arg and not any(c in arg for c in ' \t"\\\'$%;'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def _systemd_path(path: Path) -> str:
    """A path for StandardOutput=append:PATH.

    systemd takes the rest of the line as the path, so spaces and quotes stay as they are
    (quoting would become part of the name). Only %-specifiers are expanded there.
    """
    text = str(path)
    if "\n" in text or "\r" in text or text != text.strip():
        raise ValueError(f"The log path cannot be used in a systemd unit: {text!r}")
    return text.replace("%", "%%")


def systemd_units(every_minutes: int, python: str | None = None, log_dir: Path | None = None,
                  env: dict[str, str] | None = None) -> tuple[str, str]:
    """Return the (service, timer) unit file texts."""
    logs = _log_dir(log_dir)
    env = _env() if env is None else env
    exec_start = " ".join(_systemd_quote(a, exec_line=True) for a in sync_command(python))
    log = _systemd_path(logs / "sync.log")
    lines = [
        "[Unit]",
        "Description=Dotiphant: refresh the local Telegram archive",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={exec_start}",
        f"SuccessExitStatus={EXIT_PARTIAL}",  # some chats could not be read: a finished run, not a failure
        "Nice=10",
        f"StandardOutput=append:{log}",
        f"StandardError=append:{log}",
    ]
    for key, value in env.items():
        lines.append(f"Environment={_systemd_quote(f'{key}={value}')}")
    service = "\n".join(lines) + "\n"
    timer = "\n".join([
        "[Unit]",
        "Description=Dotiphant: refresh the local Telegram archive every few minutes",
        "",
        "[Timer]",
        "OnBootSec=2min",
        f"OnUnitActiveSec={int(every_minutes)}min",
        f"Unit={SYSTEMD_NAME}.service",
        "",
        "[Install]",
        "WantedBy=timers.target",
    ]) + "\n"
    return service, timer


def windows_command(every_minutes: int, python: str | None = None) -> str:
    cmd = subprocess.list2cmdline(sync_command(python))
    return (f'schtasks /Create /F /SC MINUTE /MO {int(every_minutes)} /TN "{WINDOWS_TASK}" '
            f'/TR "{cmd.replace(chr(34), chr(92) + chr(34))}"')


# --- paths -------------------------------------------------------------------------

def plist_path(launch_agents_dir: Path | None = None) -> Path:
    return (launch_agents_dir or Path.home() / "Library" / "LaunchAgents") / f"{LABEL}.plist"


def systemd_dir(path: Path | None = None) -> Path:
    if path:
        return path
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def _run(runner: Runner, args: list[str]) -> bool:
    try:
        result = runner(args, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(result, "returncode", 1) == 0


def _linger_enabled(user: str | None = None, linger_dir: Path = Path("/var/lib/systemd/linger")) -> bool:
    """Whether systemd keeps this user's services running while they are logged out."""
    import getpass

    try:
        return (linger_dir / (user or getpass.getuser())).exists()
    except (OSError, KeyError):
        return False


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


# --- public API --------------------------------------------------------------------

def install(every_minutes: int | None = None, *, platform: str | None = None, python: str | None = None,
            launch_agents_dir: Path | None = None, systemd_user_dir: Path | None = None,
            log_dir: Path | None = None, runner: Runner = subprocess.run, uid: int | None = None) -> str:
    """Install (or replace) the background refresh. Returns a message for the user."""
    every = int(every_minutes or config.Settings.load().sync_interval_minutes)
    if every < 1:
        raise ValueError("The interval must be at least one minute.")
    plat = _platform(platform)

    if plat == "macos":
        path = plist_path(launch_agents_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{_uid() if uid is None else uid}"
        # Replace a loaded copy first, so a new interval takes effect.
        _run(runner, ["launchctl", "bootout", f"{domain}/{LABEL}"])
        path.write_bytes(plist_content(every, python, log_dir))
        if not _run(runner, ["launchctl", "bootstrap", domain, str(path)]):
            if not _run(runner, ["launchctl", "load", "-w", str(path)]):
                return (f"Wrote {path}, but launchctl could not load it. "
                        f"Try: launchctl bootstrap {domain} {shlex.quote(str(path))}")
        return f"Background refresh every {every} minutes (LaunchAgent {path})."

    if plat == "linux":
        unit_dir = systemd_dir(systemd_user_dir)
        unit_dir.mkdir(parents=True, exist_ok=True)
        service, timer = systemd_units(every, python, log_dir)
        (unit_dir / f"{SYSTEMD_NAME}.service").write_text(service)
        (unit_dir / f"{SYSTEMD_NAME}.timer").write_text(timer)
        ok = _run(runner, ["systemctl", "--user", "daemon-reload"])
        ok = _run(runner, ["systemctl", "--user", "enable", "--now", f"{SYSTEMD_NAME}.timer"]) and ok
        if not ok:
            return (f"Wrote the timer to {unit_dir}, but systemctl failed. "
                    f"Try: systemctl --user enable --now {SYSTEMD_NAME}.timer")
        msg = f"Background refresh every {every} minutes (systemd user timer {SYSTEMD_NAME}.timer)."
        if not _linger_enabled():
            msg += ("\nIt runs only while you are logged in. To keep it running after you log out, "
                    "run once: loginctl enable-linger $USER")
        return msg

    return ("Windows is not scheduled automatically. Run this once in a terminal to refresh "
            f"every {every} minutes:\n\n  {windows_command(every, python)}\n")


def remove(*, platform: str | None = None, launch_agents_dir: Path | None = None,
           systemd_user_dir: Path | None = None, runner: Runner = subprocess.run, uid: int | None = None) -> str:
    plat = _platform(platform)
    if plat == "macos":
        path = plist_path(launch_agents_dir)
        domain = f"gui/{_uid() if uid is None else uid}"
        if not _run(runner, ["launchctl", "bootout", f"{domain}/{LABEL}"]) and path.exists():
            _run(runner, ["launchctl", "unload", "-w", str(path)])
        existed = path.exists()
        path.unlink(missing_ok=True)
        return "Background refresh removed." if existed else "Background refresh was not installed."
    if plat == "linux":
        unit_dir = systemd_dir(systemd_user_dir)
        timer = unit_dir / f"{SYSTEMD_NAME}.timer"
        existed = timer.exists()
        _run(runner, ["systemctl", "--user", "disable", "--now", f"{SYSTEMD_NAME}.timer"])
        timer.unlink(missing_ok=True)
        (unit_dir / f"{SYSTEMD_NAME}.service").unlink(missing_ok=True)
        _run(runner, ["systemctl", "--user", "daemon-reload"])
        return "Background refresh removed." if existed else "Background refresh was not installed."
    return f'On Windows, remove the task with:\n\n  schtasks /Delete /F /TN "{WINDOWS_TASK}"\n'


def status(*, platform: str | None = None, launch_agents_dir: Path | None = None,
           systemd_user_dir: Path | None = None) -> dict[str, Any]:
    """Whether a background refresh is installed, and how often it runs (from the files)."""
    plat = _platform(platform)
    if plat == "macos":
        path = plist_path(launch_agents_dir)
        if not path.exists():
            return {"platform": plat, "installed": False, "path": str(path), "every_minutes": None}
        try:
            data = plistlib.loads(path.read_bytes())
            every = int(data.get("StartInterval", 0)) // 60 or None
        except Exception:
            every = None
        return {"platform": plat, "installed": True, "path": str(path), "every_minutes": every}
    if plat == "linux":
        path = systemd_dir(systemd_user_dir) / f"{SYSTEMD_NAME}.timer"
        every = None
        if path.exists():
            for line in path.read_text().splitlines():
                if line.startswith("OnUnitActiveSec="):
                    value = line.split("=", 1)[1].removesuffix("min")
                    every = int(value) if value.isdigit() else None
        return {"platform": plat, "installed": path.exists(), "path": str(path), "every_minutes": every}
    return {"platform": plat, "installed": None, "path": None, "every_minutes": None}
