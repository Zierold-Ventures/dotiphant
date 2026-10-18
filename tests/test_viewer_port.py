"""Who holds the viewer port: build identity, the shutdown endpoint, `dotiphant open` reuse and
replacement, moving off busy ports, the stale-code page and `dotiphant stop`.

Real viewers run with uvicorn in a thread on ports the OS reports free; nothing here touches
the default ports 8764 and 8765 or opens a browser.
"""
from __future__ import annotations

import contextlib
import http.server
import random
import sys
import threading
import time
import types
import urllib.request

import pytest
import uvicorn
from starlette.testclient import TestClient

from dotiphant import cli, config, demo, viewer
from dotiphant.web import app as webapp

BASE = "http://127.0.0.1:8765"
SAME = {"Origin": BASE}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    monkeypatch.setattr(cli.webbrowser, "open", lambda *a, **k: pytest.fail("no browser in tests"))
    return config.data_dir()


@pytest.fixture
def seeded(home):
    demo.seed_demo(home)
    return home


def free_ports(n: int = 2) -> int:
    """A port p such that p .. p+n-1 are all free right now."""
    for _ in range(500):
        p = random.randint(20000, 60000)
        if all(viewer.port_is_free(p + i) for i in range(n)):
            return p
    raise RuntimeError("no free ports")


@contextlib.contextmanager
def running_viewer(port: int):
    """A real viewer for the current DOTIPHANT_HOME, like `dotiphant open` runs it."""
    app = webapp.create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    app.state.shutdown = lambda: setattr(server, "should_exit", True)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        assert time.monotonic() < deadline, "viewer did not start"
        time.sleep(0.02)
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(5)


@contextlib.contextmanager
def other_program(port: int, status: int = 200, body: bytes = b"hello", headers: dict | None = None):
    """Some other HTTP program on the port."""
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self):
            seen.append(f"{self.command} {self.path} {self.headers.get('Authorization') or ''}")
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _reply

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield seen
    finally:
        server.shutdown()
        server.server_close()


def key() -> str:
    return webapp.viewer_token()


# --- identity headers ------------------------------------------------------------------

def test_every_response_carries_the_build_and_owner(seeded):
    with TestClient(webapp.create_app(), base_url=BASE) as c:
        r = c.get("/")
        assert r.status_code == 401  # the locked page
        assert r.headers["x-dotiphant-port-owner"] == "dotiphant"
        assert r.headers["x-dotiphant-build"] == viewer.BUILD_ID
        assert r.headers["x-dotiphant-folder"] == viewer.folder_id(seeded, key())
        assert str(seeded) not in str(r.headers)  # the folder is an HMAC, never the path
        r = c.get("/", headers={"Authorization": f"Bearer {key()}"})
        assert r.status_code == 200 and r.headers["x-dotiphant-build"] == viewer.BUILD_ID


def test_build_id_follows_the_files_on_disk(tmp_path, monkeypatch):
    (tmp_path / "mod.py").write_text("x = 1\n")
    monkeypatch.setattr(viewer, "PACKAGE_DIR", tmp_path)
    loaded = viewer.compute_build_id()
    monkeypatch.setattr(viewer, "BUILD_ID", loaded)
    monkeypatch.setattr(viewer, "_disk", {"sig": viewer._stat_signature(), "build": loaded, "checked": 0.0})
    assert viewer.disk_build_id(now=100.0) == loaded
    (tmp_path / "mod.py").write_text("x = 22\n")  # a reinstall puts new files on disk
    assert viewer.disk_build_id(now=101.0) == loaded  # checked at most every few seconds
    assert viewer.disk_build_id(now=200.0) != loaded


# --- the shutdown endpoint ---------------------------------------------------------------

def test_shutdown_needs_the_viewer_key_and_the_origin(seeded):
    calls = []
    app = webapp.create_app()
    app.state.shutdown = lambda: calls.append(1)
    bearer = {"Authorization": f"Bearer {key()}"}
    with TestClient(app, base_url=BASE) as c:
        assert c.post("/api/shutdown", headers=SAME).status_code == 401  # no key
        assert c.post("/api/shutdown", headers={**SAME, "Authorization": "Bearer nope"}).status_code == 401
        assert c.post("/api/shutdown", headers=bearer).status_code == 403  # no Origin
        assert c.post("/api/shutdown", headers={**bearer, "Origin": "http://evil.example"}).status_code == 403
        # A browser session with the cookie and the page token may not stop the viewer.
        assert c.get(f"/?key={key()}", follow_redirects=False).status_code == 303
        page = {**SAME, "X-Dotiphant-Token": app.state.csrf_token}
        assert c.post("/api/shutdown", headers=page).status_code == 403
        assert calls == []
        c.cookies.clear()
        r = c.post("/api/shutdown", headers={**bearer, **SAME})
        assert r.status_code == 200 and r.json() == {"stopping": True}
        assert calls == [1]
        assert c.get("/api/shutdown", headers=bearer).status_code == 405


# --- the stale-code page -------------------------------------------------------------------

def test_stale_viewer_explains_instead_of_crashing(seeded, monkeypatch):
    monkeypatch.setattr(viewer, "is_stale", lambda: True)
    app = webapp.create_app()
    app.state.shutdown = lambda: None
    auth = {"Authorization": f"Bearer {key()}"}
    with TestClient(app, base_url=BASE) as c:
        for path in ("/", "/connect"):
            r = c.get(path, headers=auth)
            assert r.status_code == 503
            assert "updated while this viewer was running" in r.text and "dotiphant open" in r.text
            assert r.headers["x-dotiphant-build"] == viewer.BUILD_ID
            assert "content-security-policy" in r.headers
        r = c.get("/api/status", headers=auth)
        assert r.status_code == 503 and r.json()["stale"] is True
        assert c.get("/static/app.css").status_code == 200
        assert c.post("/api/shutdown", headers={**auth, **SAME}).status_code == 200


def test_crash_in_a_stale_viewer_shows_the_update_page(seeded, monkeypatch):
    def boom(*a, **k):
        raise KeyError("template wants something the old code does not provide")

    monkeypatch.setattr(webapp.query, "overview", boom)
    stale = iter([False, True])  # fresh when the request starts, stale by the time it fails
    monkeypatch.setattr(viewer, "is_stale", lambda: next(stale, True))
    with TestClient(webapp.create_app(), base_url=BASE, raise_server_exceptions=False) as c:
        r = c.get("/", headers={"Authorization": f"Bearer {key()}"})
    assert r.status_code == 503 and "Ctrl+C" in r.text
    assert r.headers["x-dotiphant-port-owner"] == "dotiphant"


# --- `dotiphant open`: what holds the port -----------------------------------------------------

def no_serve(monkeypatch):
    web = types.ModuleType("dotiphant.web.app")
    web.serve = lambda **kw: pytest.fail(f"must not start a second viewer: {kw}")
    monkeypatch.setitem(sys.modules, "dotiphant.web.app", web)


def record_serve(monkeypatch) -> list:
    served: list = []
    web = types.ModuleType("dotiphant.web.app")
    web.serve = lambda port, open_browser=True, **kw: served.append(port)
    monkeypatch.setitem(sys.modules, "dotiphant.web.app", web)
    return served


def test_same_build_viewer_is_reused(seeded, monkeypatch, capsys):
    port = free_ports(1)
    with running_viewer(port):
        no_serve(monkeypatch)
        assert cli.main(["open", "--port", str(port), "--no-browser"]) == 0
        out = capsys.readouterr().out
        assert f"Dotiphant is already running at http://127.0.0.1:{port}/?key={key()}" in out
        opened = []
        monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
        assert cli.launch_viewer(types.SimpleNamespace(port=None, no_browser=False), port) == 0
        assert opened == [f"http://127.0.0.1:{port}/?key={key()}"]
        assert key() not in capsys.readouterr().out  # with a browser, the key stays out of the terminal


def test_stale_viewer_is_stopped_and_replaced(seeded, monkeypatch, capsys):
    port = free_ports(1)
    with running_viewer(port) as server:
        monkeypatch.setattr(viewer, "BUILD_ID", "0123456789abcdef")  # this CLI is a newer build
        served = record_serve(monkeypatch)
        assert cli.main(["open", "--no-browser", "--port", str(port)]) == 0
        assert "Stopped an older Dotiphant viewer and started the updated one." in capsys.readouterr().out
        assert served == [port] and server.should_exit and viewer.port_is_free(port)


def test_stale_viewer_that_does_not_stop_gives_instructions(seeded, monkeypatch, capsys):
    port = free_ports(1)
    with running_viewer(port):
        monkeypatch.setattr(viewer, "BUILD_ID", "0123456789abcdef")
        monkeypatch.setattr(viewer, "request_shutdown", lambda port, key: False)
        no_serve(monkeypatch)
        with pytest.raises(viewer.PortError) as exc:
            viewer.plan(port, seeded, key(), log=lambda m: None)
        assert f"lsof -i :{port}" in str(exc.value) and "--port" in str(exc.value)
        assert cli.main(["open", "--no-browser", "--port", str(port)]) == 1
        assert f"lsof -i :{port}" in capsys.readouterr().err


def test_viewer_for_another_folder_is_left_alone(seeded, tmp_path, monkeypatch):
    port = free_ports(2)
    with running_viewer(port) as server:
        logs: list[str] = []
        plan = viewer.plan(port, tmp_path / "other", key(), log=logs.append)
        assert plan == viewer.Plan("start", port + 1)
        assert any("another data folder" in m for m in logs)
        assert not server.should_exit
        with pytest.raises(viewer.PortError, match="another data folder"):
            viewer.plan(port, tmp_path / "other", key(), explicit=True)


def test_busy_port_moves_to_the_next_free_one(home, monkeypatch, capsys):
    port = free_ports(2)
    with other_program(port) as seen:
        served = record_serve(monkeypatch)
        assert cli.launch_viewer(types.SimpleNamespace(port=None, no_browser=True), port) == 0
        assert served == [port + 1]
        assert f"Port {port} is busy, using {port + 1}." in capsys.readouterr().out
        assert all("Bearer" not in s for s in seen)  # the key never goes to another program


def test_explicit_busy_port_fails_clearly(home, monkeypatch, capsys):
    port = free_ports(1)
    with other_program(port):
        no_serve(monkeypatch)
        assert cli.main(["open", "--no-browser", "--port", str(port)]) == 1
        err = capsys.readouterr().err
        assert f"Port {port} is in use by another program" in err and "--port" in err
        assert f"lsof -i :{port}" in err


def test_viewer_from_before_the_headers_is_recognized(home):
    port = free_ports(2)
    locked = {"Content-Security-Policy": "default-src 'self'"}
    with other_program(port, status=401, body=b"<p>Run dotiphant open</p>", headers=locked):
        assert viewer.probe(port).kind == "legacy"
        logs: list[str] = []
        assert viewer.plan(port, home, "k", log=logs.append) == viewer.Plan("start", port + 1)
        assert any("cannot be stopped from here" in m for m in logs)


def test_demo_uses_the_same_logic_for_its_folder(home, tmp_path, monkeypatch, capsys):
    demo_home = tmp_path / "demo"
    fake = types.ModuleType("dotiphant.demo")
    fake.demo_home = lambda: demo_home

    def seed_demo(h):
        demo.seed_demo(h)

    fake.seed_demo = seed_demo
    monkeypatch.setitem(sys.modules, "dotiphant.demo", fake)
    port = free_ports(2)
    with other_program(port):
        served = record_serve(monkeypatch)
        monkeypatch.setattr(cli, "DEMO_PORT", port)
        assert cli.main(["demo", "--no-browser"]) == 0
    assert served == [port + 1]


# --- `dotiphant stop` --------------------------------------------------------------------------

@pytest.fixture
def stop_ports(monkeypatch):
    """Point `dotiphant stop` at test ports instead of 8765 and 8764."""
    port = free_ports(1)
    demo_port = free_ports(1)
    monkeypatch.setattr(cli, "VIEWER_PORT", port)
    monkeypatch.setattr(cli, "DEMO_PORT", demo_port)
    monkeypatch.setattr(viewer, "PORT_RANGE", 0)
    return port


def test_stop_with_nothing_running(home, stop_ports, capsys):
    assert cli.main(["stop"]) == 0
    assert "No Dotiphant viewer is running." in capsys.readouterr().out


@pytest.mark.parametrize("stale", [False, True])
def test_stop_a_current_or_stale_viewer(seeded, stop_ports, monkeypatch, capsys, stale):
    with running_viewer(stop_ports) as server:
        if stale:
            monkeypatch.setattr(viewer, "BUILD_ID", "0123456789abcdef")
        assert cli.main(["stop"]) == 0
        assert f"Stopped the Dotiphant viewer on port {stop_ports}." in capsys.readouterr().out
        assert server.should_exit and viewer.port_is_free(stop_ports)


def test_stop_leaves_other_programs_alone(home, stop_ports, capsys):
    with other_program(stop_ports) as seen:
        assert cli.main(["stop"]) == 0
        assert "No Dotiphant viewer is running." in capsys.readouterr().out
        assert all(s.startswith("GET ") and "Bearer" not in s for s in seen)
        with urllib.request.urlopen(f"http://127.0.0.1:{stop_ports}/", timeout=2) as r:
            assert r.read() == b"hello"
