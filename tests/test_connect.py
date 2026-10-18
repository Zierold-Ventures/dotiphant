import json
import subprocess
import tomllib

import pytest

from dotiphant import connect as c

CMD = ["/opt/dotiphant/bin/dotiphant", "mcp"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "HOME", lambda: tmp_path)
    monkeypatch.setattr(c, "PLATFORM", "darwin")
    monkeypatch.setattr(c, "APPLICATIONS", tmp_path / "Applications")
    monkeypatch.setattr(c, "server_command", lambda: list(CMD))
    monkeypatch.setattr(c, "_which", lambda name: None)
    monkeypatch.setattr(c, "_run", lambda cmd: pytest.fail(f"unexpected command {cmd}"))
    monkeypatch.delenv("DOTIPHANT_HOME", raising=False)
    return tmp_path


def test_server_command_fallback(monkeypatch):
    monkeypatch.setattr(c, "_which", lambda name: None)
    cmd = c.server_command()
    assert cmd[1:] == ["-m", "dotiphant", "mcp"]


def test_dotiphant_home_passed_only_when_set(home, monkeypatch):
    assert "env" not in c.server_entry()
    monkeypatch.setenv("DOTIPHANT_HOME", str(home / "data"))
    assert c.server_entry()["env"] == {"DOTIPHANT_HOME": str((home / "data").resolve())}


@pytest.mark.parametrize("name,rel", [
    ("claude-desktop", "Library/Application Support/Claude/claude_desktop_config.json"),
    ("cursor", ".cursor/mcp.json"),
])
def test_json_merge_backup_idempotent(home, name, rel):
    path = home / rel
    path.parent.mkdir(parents=True)
    original = {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}
    path.write_text(json.dumps(original))

    preview = c.connect(name, dry_run=True)
    assert "Would add" in preview
    assert json.loads(path.read_text()) == original

    out = c.connect(name)
    assert "Added" in out and "Restart" in out
    data = json.loads(path.read_text())
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["dotiphant"] == {"command": CMD[0], "args": ["mcp"]}
    backups = list(path.parent.glob(path.name + ".bak-dotiphant-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original

    before = path.read_text()
    assert "Nothing changed" in c.connect(name)
    assert path.read_text() == before


def test_json_creates_missing_file_and_updates_stale_entry(home):
    path = home / ".cursor" / "mcp.json"
    assert "Added" in c.connect("cursor")
    assert json.loads(path.read_text())["mcpServers"]["dotiphant"]["command"] == CMD[0]
    data = json.loads(path.read_text())
    data["mcpServers"]["dotiphant"]["command"] = "/old/dotiphant"
    path.write_text(json.dumps(data))
    assert "Updated" in c.connect("cursor")
    assert json.loads(path.read_text())["mcpServers"]["dotiphant"]["command"] == CMD[0]


def test_json_invalid_file_left_alone(home):
    path = home / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text("{not json")
    out = c.connect("cursor")
    assert "not valid JSON" in out and "mcpServers" in out
    assert path.read_text() == "{not json"


def test_claude_desktop_paths(home, monkeypatch):
    monkeypatch.setattr(c, "PLATFORM", "linux")
    assert c.CLIENTS["claude-desktop"].path() == home / ".config/Claude/claude_desktop_config.json"
    monkeypatch.setattr(c, "PLATFORM", "win32")
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    assert c.CLIENTS["claude-desktop"].path() == home / "AppData/Claude/claude_desktop_config.json"


CODEX = '''model = "gpt-5"  # keep this comment

[mcp_servers.github]
command = "gh-mcp"
args = []

[mcp_servers.dotiphant]
command = "/old/dotiphant"
args = ["mcp"]

[mcp_servers.dotiphant.env]
DOTIPHANT_HOME = "/old"

[profiles.fast]
model = "small"
'''


def test_codex_toml_replace(home):
    path = home / ".codex" / "config.toml"
    path.parent.mkdir()
    path.write_text(CODEX)
    out = c.connect("codex")
    assert "Updated" in out
    text = path.read_text()
    data = tomllib.loads(text)
    assert data["mcp_servers"]["dotiphant"] == {"command": CMD[0], "args": ["mcp"]}
    assert data["mcp_servers"]["github"]["command"] == "gh-mcp"
    assert data["profiles"]["fast"]["model"] == "small"
    assert "# keep this comment" in text
    assert "/old" not in text
    assert [b.read_text() for b in path.parent.glob("config.toml.bak-dotiphant-*")] == [CODEX]
    assert "Nothing changed" in c.connect("codex")
    assert path.read_text() == text


def test_codex_toml_new_file_with_env(home, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", '/tmp/we"ird path')
    path = home / ".codex" / "config.toml"
    assert "Would write" in c.connect("codex", dry_run=True)
    assert not path.exists()
    c.connect("codex")
    data = tomllib.loads(path.read_text())
    assert data["mcp_servers"]["dotiphant"]["env"]["DOTIPHANT_HOME"].endswith('we"ird path')
    assert "Nothing changed" in c.connect("codex")


def test_codex_inline_definition_left_alone(home):
    path = home / ".codex" / "config.toml"
    path.parent.mkdir()
    original = 'mcp_servers = { dotiphant = { command = "x", args = [] } }\n'
    path.write_text(original)
    assert "Add this by hand" in c.connect("codex")
    assert path.read_text() == original


def test_claude_code_without_binary(home):
    out = c.connect("claude-code")
    assert "claude mcp add --scope user dotiphant -- /opt/dotiphant/bin/dotiphant mcp" in out


def test_claude_code_with_binary(home, monkeypatch):
    calls = []
    state = {"configured": False}

    def run(cmd):
        calls.append(cmd)
        if cmd[1:3] == ["mcp", "get"]:
            if state["configured"]:
                return subprocess.CompletedProcess(cmd, 0, "dotiphant:\n  Command: /opt/dotiphant/bin/dotiphant\n  Args: mcp\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "No MCP server found")
        if cmd[1:3] == ["mcp", "add"]:
            state["configured"] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(c, "_which", lambda name: "/usr/local/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(c, "_run", run)
    assert "Would run" in c.connect("claude-code", dry_run=True)
    assert "Added" in c.connect("claude-code")
    assert calls[-1] == ["/usr/local/bin/claude", "mcp", "add", "--scope", "user", "dotiphant", "--", *CMD]
    n = len(calls)
    assert "Nothing changed" in c.connect("claude-code")
    assert len(calls) == n + 1  # only the `get`


def test_snippets(home):
    assert json.loads(c.snippet("cursor"))["mcpServers"]["dotiphant"]["args"] == ["mcp"]
    assert tomllib.loads(c.snippet("codex"))["mcp_servers"]["dotiphant"]["command"] == CMD[0]
    assert c.snippet("claude-code").startswith("claude mcp add")
    with pytest.raises(ValueError):
        c.snippet("emacs")


def test_detect(home):
    assert c.detect() == []
    (home / ".cursor").mkdir()
    (home / ".codex").mkdir()
    (home / "Library/Application Support/Claude").mkdir(parents=True)
    found = c.detect()
    assert {"cursor", "codex", "claude-desktop"} <= set(found)


def test_symlinked_config_is_written_through(home):
    """Dotfiles setups symlink the config; connect must update the target and keep the link."""
    real = home / "dotfiles" / "mcp.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    link = home / ".cursor" / "mcp.json"
    link.parent.mkdir()
    link.symlink_to(real)
    assert "Added" in c.connect("cursor")
    assert link.is_symlink() and link.resolve() == real.resolve()
    data = json.loads(real.read_text())
    assert set(data["mcpServers"]) == {"other", "dotiphant"}
    assert not list(real.parent.glob("*.tmp-dotiphant"))


def test_backups_are_timestamped_and_pruned(home, monkeypatch):
    path = home / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text("{}")
    versions = []
    for i in range(c.KEEP_BACKUPS + 3):
        monkeypatch.setattr(c, "server_command", lambda i=i: [f"/opt/v{i}/dotiphant", "mcp"])
        assert "the 'dotiphant' server" in c.connect("cursor")
        versions.append(path.read_text())
    backups = sorted(path.parent.glob("mcp.json.bak-dotiphant-*"))
    assert len(backups) == c.KEEP_BACKUPS
    # the newest backups survive, each a different earlier version (nothing overwritten)
    assert [b.read_text() for b in backups] == versions[-c.KEEP_BACKUPS - 1:-1]


def test_is_connected(home, monkeypatch):
    for name in c.CLIENTS:
        assert c.is_connected(name) is False  # nothing installed, nothing configured
    cursor = home / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    cursor.write_text("not json")
    assert c.is_connected("cursor") is False
    cursor.write_text('{"mcpServers": {"other": {"command": "x"}}}')
    assert c.is_connected("cursor") is False
    c.connect("cursor")
    assert c.is_connected("cursor") is True
    c.connect("codex")
    assert c.is_connected("codex") is True
    monkeypatch.setattr(c, "_which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(c, "_run", lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "No MCP server found"))
    assert c.is_connected("claude-code") is False
    monkeypatch.setattr(c, "_run", lambda cmd: subprocess.CompletedProcess(cmd, 0, "dotiphant:\n", ""))
    assert c.is_connected("claude-code") is True
