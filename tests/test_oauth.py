"""OAuth for `dotiphant mcp --http`: metadata, registration, pairing-code consent, PKCE, refresh, revocation."""
import base64
import hashlib
import json
import os
import secrets
import stat
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from dotiphant import config, db, mcp_server as ms, oauth

PUBLIC = "https://abc.ngrok.app"
REDIRECT = "https://chatgpt.com/connector/oauth/callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    db.init()
    return tmp_path


@pytest.fixture
def client(home):
    events = []
    app = ms.http_app("static-tok", public_url=PUBLIC, log=events.append)
    with TestClient(app, base_url=PUBLIC) as tc:
        tc.events = events
        yield tc


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def register(tc, name="ChatGPT"):
    r = tc.post("/register", json={
        "client_name": name, "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def start_authorize(tc, client_id, challenge):
    r = tc.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "xyz", "resource": PUBLIC + "/mcp"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    consent = urlparse(r.headers["location"])
    assert f"{consent.scheme}://{consent.netloc}" == PUBLIC and consent.path == "/consent"
    return parse_qs(consent.query)["request"][0]


def approve(tc, request_id, code):
    return tc.post("/consent", data={"request": request_id, "code": code, "action": "approve"},
                   follow_redirects=False)


def full_authorization(tc):
    client_id = register(tc)
    verifier, challenge = pkce()
    request_id = start_authorize(tc, client_id, challenge)
    r = approve(tc, request_id, oauth.format_code(oauth.pairing_code()))
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    r = tc.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id,
                                "redirect_uri": REDIRECT, "code_verifier": verifier})
    assert r.status_code == 200, r.text
    return client_id, r.json()


def call_mcp(tc, token):
    headers = {**MCP_HEADERS, **({"Authorization": f"Bearer {token}"} if token else {})}
    return tc.post("/mcp", json=INIT, headers=headers)


def test_metadata(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == PUBLIC
    assert meta["authorization_endpoint"] == PUBLIC + "/authorize"
    assert meta["token_endpoint"] == PUBLIC + "/token"
    assert meta["registration_endpoint"] == PUBLIC + "/register"
    assert meta["revocation_endpoint"] == PUBLIC + "/revoke"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        res = client.get(path).json()
        assert res["resource"] == PUBLIC + "/mcp"
        assert res["authorization_servers"] == [PUBLIC]


def test_unauthenticated_gets_401_with_resource_metadata(client):
    r = call_mcp(client, None)
    assert r.status_code == 401
    assert f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
    assert call_mcp(client, "made-up").status_code == 401


def test_full_flow(client, home):
    client_id = register(client)
    verifier, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)

    page = client.get("/consent", params={"request": request_id})
    assert page.status_code == 200
    assert "ChatGPT" in page.text and "read-only access" in page.text and "chatgpt.com" in page.text
    assert page.headers["x-frame-options"] == "DENY"

    # without the pairing code nothing is issued
    code = oauth.pairing_code()
    r = approve(client, request_id, "")
    assert r.status_code == 403 and "location" not in r.headers
    r = approve(client, request_id, oauth.format_code(code))
    assert r.status_code == 302
    location = urlparse(r.headers["location"])
    assert location.netloc == "chatgpt.com"
    q = parse_qs(location.query)
    assert q["state"] == ["xyz"]
    assert any("Approved 'ChatGPT'" in e for e in client.events)

    # the pairing code is one-time
    request_2 = start_authorize(client, client_id, challenge)
    assert approve(client, request_2, code).status_code == 400

    # PKCE: a wrong verifier is refused, the right one works once
    form = {"grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id, "redirect_uri": REDIRECT}
    bad = client.post("/token", data={**form, "code_verifier": secrets.token_urlsafe(48)})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    r = client.post("/token", data={**form, "code_verifier": verifier})
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["token_type"] == "Bearer" and tokens["refresh_token"]
    assert client.post("/token", data={**form, "code_verifier": verifier}).status_code == 400  # code used up

    # the access token works on the MCP endpoint
    r = call_mcp(client, tokens["access_token"])
    assert r.status_code == 200, r.text
    assert "unofficial Telegram archive" in r.text

    # tokens are stored hashed in a private file, not in dotiphant.db
    path = home / "oauth.json"
    raw = path.read_text()
    assert tokens["access_token"] not in raw and tokens["refresh_token"] not in raw
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    # refresh rotates both tokens
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                    "client_id": client_id})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["access_token"] != tokens["access_token"]
    assert call_mcp(client, new["access_token"]).status_code == 200
    assert call_mcp(client, tokens["access_token"]).status_code == 401
    reuse = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                        "client_id": client_id})
    assert reuse.status_code == 400

    # revocation through the endpoint
    # (the SDK's revocation form needs client_secret present, even empty, for public clients)
    r = client.post("/revoke", data={"token": new["access_token"], "client_id": client_id, "client_secret": ""})
    assert r.status_code == 200, r.text
    assert call_mcp(client, new["access_token"]).status_code == 401
    assert client.post("/token", data={"grant_type": "refresh_token", "refresh_token": new["refresh_token"],
                                       "client_id": client_id}).status_code == 400


def test_wrong_codes_close_the_request_but_not_the_code(client):
    client_id = register(client)
    _, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)
    good = oauth.pairing_code()
    wrong = str((int(good) + 1) % 10**8).zfill(8)
    for _ in range(oauth.MAX_ATTEMPTS_PER_REQUEST - 1):
        r = approve(client, request_id, wrong)
        assert r.status_code == 403 and "wrong" in r.text
    r = approve(client, request_id, wrong)
    assert r.status_code == 403 and "request is closed" in r.text
    assert approve(client, request_id, good).status_code == 400  # this request is gone
    # A stranger cannot lock the owner out: the code still works on a fresh request.
    request_2 = start_authorize(client, client_id, challenge)
    assert approve(client, request_2, good).status_code == 302


def test_many_wrong_codes_pause_code_entry_for_a_while(client, monkeypatch):
    client_id = register(client)
    _, challenge = pkce()
    good = oauth.pairing_code()
    wrong = str((int(good) + 1) % 10**8).zfill(8)
    status = None
    for i in range(oauth.GLOBAL_MAX_FAILURES):
        if i % oauth.MAX_ATTEMPTS_PER_REQUEST == 0:
            request_id = start_authorize(client, client_id, challenge)
        status = approve(client, request_id, wrong).status_code
    assert status == 429
    request_id = start_authorize(client, client_id, challenge)
    r = approve(client, request_id, good)  # even the right code waits out the pause
    assert r.status_code == 429 and "paused" in r.text and "dotiphant mcp --approve" in r.text
    # Terminal approval still works during the pause.
    short = parse_short_id(client.get("/consent", params={"request": request_id}).text)
    assert oauth.approve_pending(short)
    r = client.post("/consent", data={"request": request_id, "action": "continue"}, follow_redirects=False)
    assert r.status_code == 302 and "code=" in r.headers["location"]
    # The pause ends by itself; the code was not thrown away.
    real_time = oauth.time.time
    monkeypatch.setattr(oauth.time, "time", lambda: real_time() + oauth.GLOBAL_PAUSE + 1)
    request_id = start_authorize(client, client_id, challenge)
    assert approve(client, request_id, good).status_code == 302


def parse_short_id(page: str) -> str:
    return page.split("dotiphant mcp --approve ", 1)[1][:9]


def test_terminal_approval(client, monkeypatch, capsys):
    from dotiphant import cli

    client_id = register(client)
    verifier, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)
    page = client.get("/consent", params={"request": request_id}).text
    short = parse_short_id(page)
    # Not approved yet: Continue does nothing.
    r = client.post("/consent", data={"request": request_id, "action": "continue"}, follow_redirects=False)
    assert r.status_code == 409 and "location" not in r.headers
    # The CLI needs a terminal, shows the exact redirect URI and asks y/N.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(cli.CliError, match="interactive terminal"):
        cli.mcp_approve(short)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_ask", lambda prompt, default="": "n")
    assert cli.main(["mcp", "--approve", short.lower()]) == 0
    out = capsys.readouterr().out
    assert REDIRECT in out and "chatgpt.com" in out and "ChatGPT" in out and "Not approved" in out
    assert client.post("/consent", data={"request": request_id, "action": "continue"},
                       follow_redirects=False).status_code == 409
    monkeypatch.setattr(cli, "_ask", lambda prompt, default="": "y")
    assert cli.main(["mcp", "--approve", short]) == 0
    assert "Approved" in capsys.readouterr().out
    r = client.post("/consent", data={"request": request_id, "action": "continue"}, follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["xyz"]
    r = client.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id,
                                    "redirect_uri": REDIRECT, "code_verifier": verifier})
    assert r.status_code == 200, r.text
    # The request is used up; an unknown id is refused.
    with pytest.raises(cli.CliError, match="No open approval request"):
        cli.mcp_approve(short)


def test_terminal_approval_hides_control_characters(client, monkeypatch, capsys):
    from dotiphant import cli

    client_id = register(client, name="Evil\x1b[2J\x1b[1;1HChatGPT")
    _, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)
    short = parse_short_id(client.get("/consent", params={"request": request_id}).text)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_ask", lambda prompt, default="": "n")
    cli.mcp_approve(short)
    assert "\x1b" not in capsys.readouterr().out


def test_deny_and_bad_requests(client):
    client_id = register(client)
    _, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)
    r = client.post("/consent", data={"request": request_id, "action": "deny"}, follow_redirects=False)
    assert r.status_code == 302 and "error=access_denied" in r.headers["location"]
    assert client.get("/consent", params={"request": request_id}).status_code == 400  # used up
    assert client.get("/consent", params={"request": "made-up"}).status_code == 400
    # a token for another resource is refused
    r = client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": "https://evil.example/mcp"}, follow_redirects=False)
    assert "error=invalid_target" in r.headers["location"]


def test_client_name_is_escaped(client):
    client_id = register(client, name="<script>alert(1)</script>")
    _, challenge = pkce()
    request_id = start_authorize(client, client_id, challenge)
    page = client.get("/consent", params={"request": request_id}).text
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_static_token_still_works(client):
    assert call_mcp(client, "static-tok").status_code == 200


def test_stored_static_token_stops_working_once_deleted(home):
    """`logout --forget-credentials` deletes mcp_token; a running server must stop accepting it."""
    config.set_secret("mcp_token", "stored-tok")
    app = ms.http_app(oauth.StoredToken(ttl=0), public_url=PUBLIC)
    with TestClient(app, base_url=PUBLIC) as tc:
        assert call_mcp(tc, "stored-tok").status_code == 200
        config.delete_secret("mcp_token")
        assert call_mcp(tc, "stored-tok").status_code == 401
        config.set_secret("mcp_token", "new-tok")
        assert call_mcp(tc, "stored-tok").status_code == 401
        assert call_mcp(tc, "new-tok").status_code == 200


def test_provider_reads_the_stored_token_again(home):
    import asyncio

    config.set_secret("mcp_token", "stored-tok")
    provider = oauth.DotiphantOAuthProvider(PUBLIC, static_token=oauth.StoredToken(ttl=0))
    found = asyncio.run(provider.load_access_token("stored-tok"))
    assert found and found.client_id == oauth.STATIC_CLIENT_ID
    config.delete_secret("mcp_token")
    assert asyncio.run(provider.load_access_token("stored-tok")) is None
    assert asyncio.run(provider.load_access_token("")) is None  # a missing token accepts nothing


def test_stored_token_is_cached_briefly(home, monkeypatch):
    now = [100.0]
    reads = []
    real = config.get_secret

    def counting(name):
        reads.append(name)
        return real(name)

    monkeypatch.setattr(config, "get_secret", counting)
    config.set_secret("mcp_token", "stored-tok")
    source = oauth.StoredToken(ttl=5, clock=lambda: now[0])
    assert source() == "stored-tok" and source() == "stored-tok"
    assert reads == ["mcp_token"]  # no keychain read per request
    config.delete_secret("mcp_token")
    now[0] += 4
    assert source() == "stored-tok"
    now[0] += 1
    assert source() is None


def test_unreadable_stored_token_denies(home, monkeypatch):
    def broken(name):
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(config, "get_secret", broken)
    assert not oauth.static_token_matches(oauth.StoredToken(ttl=0), "anything")


def test_explicit_token_is_kept_in_memory(home):
    """A token passed with --token is not stored, so deleting mcp_token does not affect it."""
    app = ms.http_app("passed-tok", public_url=PUBLIC)
    config.delete_secret("mcp_token")
    with TestClient(app, base_url=PUBLIC) as tc:
        assert call_mcp(tc, "passed-tok").status_code == 200


def test_run_http_checks_the_stored_token_unless_one_was_passed(home, monkeypatch):
    import uvicorn

    seen = []
    monkeypatch.setattr(ms, "http_app", lambda token, *a, **k: seen.append(token))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    ms.run_http(port=8766)
    assert isinstance(seen[-1], oauth.StoredToken) and seen[-1]() == config.get_secret("mcp_token")
    ms.run_http(port=8766, token="passed")
    assert seen[-1] == "passed"


def test_static_verifier_without_issuer_reads_the_stored_token(home):
    import asyncio

    config.set_secret("mcp_token", "stored-tok")
    verifier = ms._StaticTokenVerifier(oauth.StoredToken(ttl=0))
    assert asyncio.run(verifier.verify_token("stored-tok"))
    config.delete_secret("mcp_token")
    assert asyncio.run(verifier.verify_token("stored-tok")) is None



def test_cli_revoke_and_list(client):
    client_id, tokens = full_authorization(client)
    assert client_id in ms.clients_text() and "ChatGPT" in ms.clients_text()
    assert "Revoked" in ms.revoke_clients(client_id)
    assert call_mcp(client, tokens["access_token"]).status_code == 401
    assert "No apps are authorized" in ms.clients_text()
    with pytest.raises(ValueError):
        ms.revoke_clients("nope")
    _, tokens = full_authorization(client)
    assert "Revoked 1 app." in ms.revoke_clients("all")
    assert call_mcp(client, tokens["access_token"]).status_code == 401


def test_host_checks_allow_public_host_only(client):
    assert call_mcp(client, "static-tok").status_code == 200  # Host: abc.ngrok.app
    r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": "Bearer static-tok",
                                                 "Host": "evil.example"})
    assert r.status_code == 421


def test_loopback_without_public_url(home):
    app = ms.http_app("tok", port=8766)
    with TestClient(app, base_url="http://127.0.0.1:8766") as tc:
        assert tc.get("/.well-known/oauth-authorization-server").json()["issuer"] == "http://127.0.0.1:8766"
        r = tc.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": "Bearer tok", "Host": "abc.ngrok.app"})
        assert r.status_code == 421  # the public host is only allowed when it was passed


def test_public_url_validation():
    assert ms.check_public_url("https://ABC.ngrok.app/") == "https://abc.ngrok.app"
    for bad in ("http://abc.ngrok.app", "https://abc.ngrok.app/mcp", "abc.ngrok.app", "https://u:p@x.app"):
        with pytest.raises(ValueError):
            ms.check_public_url(bad)


def test_pairing_code_cli(home):
    out = ms.pairing_message()
    code = oauth.pairing_code()
    assert oauth.format_code(code) in out
    assert oauth.pairing_code() == code  # shown again, not replaced
    assert json.loads((home / "oauth.json").read_text())["pairing"]["code"] == code


def test_registration_flood_evicts_the_oldest(client, monkeypatch):
    monkeypatch.setattr(oauth, "MAX_UNAPPROVED_CLIENTS", 2)
    first = register(client, name="first")
    register(client, name="second")
    newest = register(client, name="third")  # accepted: the flood cannot block a real app
    clients = json.loads(oauth.store_path().read_text())["clients"]
    assert first not in clients and newest in clients and len(clients) == 2


def test_pending_flood_evicts_the_oldest(client, monkeypatch):
    monkeypatch.setattr(oauth, "MAX_PENDING", 3)
    client_id = register(client)
    _, challenge = pkce()
    ids = [start_authorize(client, client_id, challenge) for _ in range(4)]
    assert client.get("/consent", params={"request": ids[0]}).status_code == 400
    assert client.get("/consent", params={"request": ids[-1]}).status_code == 200
    assert len(json.loads(oauth.store_path().read_text())["pending"]) == 3


def test_consent_page_shows_this_requests_exact_redirect(client):
    other = "https://evil.example/cb?x=1"
    r = client.post("/register", json={"client_name": "ChatGPT", "redirect_uris": [REDIRECT, other],
                                       "token_endpoint_auth_method": "none"})
    client_id = r.json()["client_id"]
    _, challenge = pkce()
    r = client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": other, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "s"}, follow_redirects=False)
    request_id = parse_qs(urlparse(r.headers["location"]).query)["request"][0]
    page = client.get("/consent", params={"request": request_id}).text
    assert "<span class=host>evil.example</span>/cb?x=1" in page
    assert "chatgpt.com" not in page  # only the address this request returns to


@pytest.mark.parametrize("uri", [
    "http://evil.example/cb", "javascript:alert(1)", "data:text/html,hi", "ftp://x.example/cb",
    "https://user@evil.example/cb", "https://chatgpt.com/cb#frag", "https://x.example/" + "a" * 500,
    "custom-app://callback",
])
def test_bad_redirect_uris_are_refused(client, uri):
    r = client.post("/register", json={"client_name": "x", "redirect_uris": [uri], "token_endpoint_auth_method": "none"})
    assert r.status_code == 400, uri


def test_loopback_redirects_are_allowed(client):
    for uri in ("http://127.0.0.1:3000/cb", "http://localhost/cb", "http://[::1]:9/cb"):
        r = client.post("/register", json={"redirect_uris": [uri], "token_endpoint_auth_method": "none"})
        assert r.status_code == 201, (uri, r.text)


def test_client_metadata_is_capped(client, home):
    base = {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}
    assert client.post("/register", json={**base, "client_name": "x" * 101}).status_code == 400
    six = [f"https://chatgpt.com/cb{i}" for i in range(6)]
    assert client.post("/register", json={**base, "redirect_uris": six}).status_code == 400
    r = client.post("/register", json={**base, "client_name": "ok", "logo_uri": "https://x.example/logo.png",
                                       "client_uri": "https://x.example", "software_id": "s" * 1000})
    assert r.status_code == 201
    stored = json.loads(oauth.store_path().read_text())["clients"][r.json()["client_id"]]["info"]
    assert "logo_uri" not in stored and "client_uri" not in stored and "software_id" not in stored
    assert len(json.dumps(stored)) <= oauth.MAX_CLIENT_BYTES
    # Oversized bodies are refused before parsing.
    r = client.post("/register", content=b'{"client_name": "' + b"x" * 20000 + b'"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_refresh_token_reuse_revokes_the_grant(client):
    client_id, tokens = full_authorization(client)
    form = {"grant_type": "refresh_token", "client_id": client_id}
    r = client.post("/token", data={**form, "refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert call_mcp(client, new["access_token"]).status_code == 200
    # The old refresh token comes back (stolen copy): the whole grant ends.
    assert client.post("/token", data={**form, "refresh_token": tokens["refresh_token"]}).status_code == 400
    assert call_mcp(client, new["access_token"]).status_code == 401
    assert client.post("/token", data={**form, "refresh_token": new["refresh_token"]}).status_code == 400
    assert any("presented again" in e for e in client.events)


def test_oauth_file_writes_hold_a_lock(home):
    """`dotiphant mcp --revoke` in another process waits for the server's write, and vice versa."""
    import threading

    from dotiphant import status as sync_status

    oauth.new_pairing_code()
    lock = open(home / "oauth.lock", "a+")
    assert sync_status.try_lock(lock)  # another process is in the middle of a write
    done = threading.Event()
    t = threading.Thread(target=lambda: (oauth.revoke("all"), done.set()))
    t.start()
    assert not done.wait(0.3)  # blocked behind the lock
    sync_status.unlock(lock)
    assert done.wait(5)
    t.join()
    lock.close()
