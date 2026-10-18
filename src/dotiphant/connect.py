"""Add Dotiphant's MCP server to AI clients: Claude Desktop, Claude Code, Codex and Cursor.

`connect(name)` merges a `dotiphant` entry into the client's config file, keeps every other
entry as it is, backs the file up to `<file>.bak-dotiphant-<time>` before changing it (keeping
the newest few backups), writes through symlinks to the real file, and does
nothing when the entry is already right. `snippet(name)` returns the text to paste by hand.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SERVER_NAME = "dotiphant"

# Indirections so tests never touch the real home directory, PATH or installed clients.
HOME: Callable[[], Path] = Path.home
PLATFORM: str = sys.platform
APPLICATIONS: Path = Path("/Applications")


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


# --- the server entry --------------------------------------------------------------

def server_command() -> list[str]:
    """The command that starts the stdio MCP server, as an absolute path where possible."""
    exe = _which("dotiphant")
    if exe:
        return [str(Path(exe).resolve()), "mcp"]
    return [sys.executable, "-m", "dotiphant", "mcp"]


def server_env() -> dict[str, str]:
    home = os.environ.get("DOTIPHANT_HOME")
    return {"DOTIPHANT_HOME": str(Path(home).expanduser().resolve())} if home else {}


def server_entry() -> dict:
    cmd = server_command()
    entry: dict = {"command": cmd[0], "args": cmd[1:]}
    if env := server_env():
        entry["env"] = env
    return entry


# --- clients -----------------------------------------------------------------------

@dataclass(frozen=True)
class Client:
    label: str
    kind: str  # json | toml | cli
    path: Callable[[], Path] | None = None


def _claude_desktop_path() -> Path:
    if PLATFORM == "darwin":
        return HOME() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if PLATFORM == "win32":
        base = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else HOME() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    return HOME() / ".config" / "Claude" / "claude_desktop_config.json"


CLIENTS: dict[str, Client] = {
    "claude-desktop": Client("Claude Desktop", "json", _claude_desktop_path),
    "claude-code": Client("Claude Code", "cli"),
    "codex": Client("Codex", "toml", lambda: HOME() / ".codex" / "config.toml"),
    "cursor": Client("Cursor", "json", lambda: HOME() / ".cursor" / "mcp.json"),
}


def _client(name: str) -> Client:
    if name not in CLIENTS:
        raise ValueError(f"Unknown client '{name}'. Choose one of: {', '.join(CLIENTS)}.")
    return CLIENTS[name]


def detect() -> list[str]:
    """Clients that look installed on this machine."""
    found = []
    if _claude_desktop_path().parent.exists() or (PLATFORM == "darwin" and (APPLICATIONS / "Claude.app").exists()):
        found.append("claude-desktop")
    if _which("claude") or (HOME() / ".claude.json").exists() or (HOME() / ".claude").is_dir():
        found.append("claude-code")
    if _which("codex") or (HOME() / ".codex").is_dir():
        found.append("codex")
    if _which("cursor") or (HOME() / ".cursor").is_dir():
        found.append("cursor")
    return found


def is_connected(name: str) -> bool:
    """True when the client's MCP config already has a `dotiphant` server entry."""
    client = _client(name)
    if client.kind == "cli":
        exe = _which("claude")
        if not exe:
            return False
        try:
            return _run([exe, "mcp", "get", SERVER_NAME]).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        text = client.path().read_text(encoding="utf-8")
        data = json.loads(text) if client.kind == "json" else tomllib.loads(text)
    except (OSError, ValueError):  # missing file, or not valid JSON or TOML
        return False
    servers = data.get("mcpServers" if client.kind == "json" else "mcp_servers") if isinstance(data, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


# --- snippets ----------------------------------------------------------------------

def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def _toml_block(entry: dict) -> str:
    lines = [f"[mcp_servers.{SERVER_NAME}]",
             f"command = {_toml_str(entry['command'])}",
             "args = [" + ", ".join(_toml_str(a) for a in entry["args"]) + "]"]
    if entry.get("env"):
        lines += ["", f"[mcp_servers.{SERVER_NAME}.env]"]
        lines += [f"{k} = {_toml_str(v)}" for k, v in entry["env"].items()]
    return "\n".join(lines) + "\n"


def _claude_code_add_cmd(entry: dict) -> list[str]:
    cmd = ["claude", "mcp", "add", "--scope", "user"]
    for k, v in entry.get("env", {}).items():
        cmd += ["-e", f"{k}={v}"]
    return cmd + [SERVER_NAME, "--", entry["command"], *entry["args"]]


def snippet(name: str) -> str:
    client = _client(name)
    entry = server_entry()
    if client.kind == "json":
        return json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2) + "\n"
    if client.kind == "toml":
        return _toml_block(entry)
    return shlex.join(_claude_code_add_cmd(entry)) + "\n"


# --- connect -----------------------------------------------------------------------

KEEP_BACKUPS = 5


def _backup(path: Path) -> Path:
    """Copy the file (the symlink target, if it is one) to a new timestamped backup next to it."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.bak-dotiphant-{stamp}")
    shutil.copy2(path, backup)
    # The stamp sorts by time; keep the newest few.
    old = sorted(path.parent.glob(f"{glob.escape(path.name)}.bak-dotiphant-*"))[:-KEEP_BACKUPS]
    for extra in old:
        extra.unlink(missing_ok=True)
    return backup


def _write(path: Path, text: str) -> None:
    """Atomic write. A symlinked config (dotfiles repos) stays a symlink: the target is replaced."""
    target = path.resolve() if path.is_symlink() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp-dotiphant")
    tmp.write_text(text, encoding="utf-8")
    if target.exists():
        shutil.copymode(target, tmp)
    tmp.replace(target)


def _restart_note(client: Client) -> str:
    return f"Restart {client.label} so it picks up the change."


def _connect_json(client: Client, dry_run: bool) -> str:
    path = client.path()
    entry = server_entry()
    data: dict = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        if raw.strip():
            try:
                data = json.loads(raw)
            except ValueError as e:
                return (f"{path} is not valid JSON ({e}); left it unchanged. Add this by hand:\n\n"
                        + json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2))
        if not isinstance(data, dict):
            return f"{path} does not contain a JSON object; left it unchanged."
    servers = data.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        return f"'mcpServers' in {path} is not an object; left it unchanged."
    if (servers or {}).get(SERVER_NAME) == entry:
        return f"{client.label} is already set up for Dotiphant ({path}). Nothing changed."
    action = "Updated" if SERVER_NAME in (servers or {}) else "Added"
    if dry_run:
        verb = "update" if action == "Updated" else "add"
        return f"Would {verb} the '{SERVER_NAME}' server in {path}:\n\n" + json.dumps(entry, indent=2)
    data.setdefault("mcpServers", {})[SERVER_NAME] = entry
    note = ""
    if path.exists():
        note = f" Backup: {_backup(path)}."
    _write(path, json.dumps(data, indent=2) + "\n")
    return f"{action} the '{SERVER_NAME}' server in {path}.{note} {_restart_note(client)}"


def _is_dotiphant_header(line: str) -> bool:
    s = line.strip()
    if not s.startswith("["):
        return False
    inner = s.strip("[]").split("#")[0].strip().replace(" ", "")
    for name in (SERVER_NAME, f'"{SERVER_NAME}"', f"'{SERVER_NAME}'"):
        prefix = f"mcp_servers.{name}"
        if inner == prefix or inner.startswith(prefix + "."):
            return True
    return False


def _strip_dotiphant_tables(text: str) -> str:
    """Remove [mcp_servers.dotiphant] and its sub-tables, keeping every other line as written."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            skipping = _is_dotiphant_header(line)
        if not skipping:
            out.append(line)
    return "".join(out)


def _connect_toml(client: Client, dry_run: bool) -> str:
    path = client.path()
    entry = server_entry()
    block = _toml_block(entry)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        current = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return f"{path} is not valid TOML ({e}); left it unchanged. Add this by hand:\n\n{block}"
    existing = current.get("mcp_servers", {}).get(SERVER_NAME) if isinstance(current.get("mcp_servers"), dict) else None
    wanted = {k: v for k, v in entry.items()}
    if existing == wanted:
        return f"{client.label} is already set up for Dotiphant ({path}). Nothing changed."
    rest = _strip_dotiphant_tables(text).rstrip()
    new_text = (rest + "\n\n" if rest else "") + block
    try:
        parsed = tomllib.loads(new_text)
        ok = parsed.get("mcp_servers", {}).get(SERVER_NAME) == wanted
    except tomllib.TOMLDecodeError:
        ok = False
    if not ok:
        return (f"Could not update {path} safely (the '{SERVER_NAME}' server may be defined inline). "
                f"Left it unchanged. Add this by hand:\n\n{block}")
    action = "Updated" if existing is not None else "Added"
    if dry_run:
        return f"Would write this to {path}:\n\n{block}"
    note = f" Backup: {_backup(path)}." if path.exists() else ""
    _write(path, new_text)
    return f"{action} the '{SERVER_NAME}' server in {path}.{note} {_restart_note(client)}"


def _connect_claude_code(client: Client, dry_run: bool) -> str:
    entry = server_entry()
    add = _claude_code_add_cmd(entry)
    exe = _which("claude")
    if not exe:
        return ("The `claude` command is not on PATH. Run this once Claude Code is installed:\n\n"
                + shlex.join(add))
    add[0] = exe
    current = _run([exe, "mcp", "get", SERVER_NAME])
    if current.returncode == 0:
        out = current.stdout
        wanted_cmd = shlex.join([entry["command"], *entry["args"]])
        env_ok = all(f"{k}={v}" in out for k, v in entry.get("env", {}).items())
        if (wanted_cmd in out or (entry["command"] in out and " ".join(entry["args"]) in out)) and env_ok:
            return "Claude Code is already set up for Dotiphant. Nothing changed."
    if dry_run:
        return "Would run:\n\n" + shlex.join(add)
    if current.returncode == 0:
        _run([exe, "mcp", "remove", "--scope", "user", SERVER_NAME])
    result = _run(add)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        return f"`claude mcp add` failed: {detail}\nRun it by hand:\n\n{shlex.join(add)}"
    return f"Added the '{SERVER_NAME}' server to Claude Code (user scope). Start a new Claude Code session to use it."


def connect(name: str, dry_run: bool = False) -> str:
    """Add Dotiphant to one client. Returns a human-readable description of what happened."""
    client = _client(name)
    if client.kind == "json":
        return _connect_json(client, dry_run)
    if client.kind == "toml":
        return _connect_toml(client, dry_run)
    return _connect_claude_code(client, dry_run)
