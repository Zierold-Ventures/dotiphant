"""Test-wide guards: no test may reach the network, a real keychain or a real browser."""
import socket

import pytest

_real_connect = socket.socket.connect


def _local_only(sock, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and (host in ("localhost", "::1") or host.startswith("127.") or sock.family == socket.AF_UNIX):
        return _real_connect(sock, address)
    raise RuntimeError(f"Tests must not open network connections (tried {host})")


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path_factory):
    monkeypatch.setattr(socket.socket, "connect", _local_only)
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    monkeypatch.setenv("BROWSER", "true")
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: False)
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path_factory.mktemp("dotiphant-home")))
