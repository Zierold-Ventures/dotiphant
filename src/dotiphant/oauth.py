"""OAuth 2.1 for `dotiphant mcp --http`: a tiny authorization server for one user, the machine owner.

Remote AI apps (ChatGPT, claude.ai and others) register themselves (dynamic client
registration), send the user's browser to /authorize, and get tokens from /token with PKCE.
The HTTP handlers come from the mcp SDK; this module is the provider behind them plus the
consent page.

The owner approves an app in one of two ways:
- in the terminal: `dotiphant mcp --approve REQUEST_ID` shows the app's name and the exact
  address the browser returns to, and asks y/N. This is the safer path.
- on the consent page, with a one-time pairing code that only the owner sees
  (`dotiphant mcp --http` prints it, `dotiphant mcp --pair` shows it again).
A stranger who finds the public URL can register a client and open the consent page, but
cannot approve it. Wrong codes close the request after a few tries, and many wrong codes in
a short time pause code entry for everyone for a while (terminal approval keeps working).

Clients, hashed tokens, open requests and the pairing code live in `oauth.json` in the data
folder (0600), never in dotiphant.db. Every read-modify-write holds a file lock, and lookups
re-read the file, so `dotiphant mcp --revoke`, `--pair` and `--approve` in another terminal
take effect at once.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import html
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from . import config
from . import status as sync_status

SCOPE = "dotiphant:read"
STATIC_CLIENT_ID = "dotiphant-static-token"
ACCESS_TTL = 3600  # 1 hour
REFRESH_TTL = 90 * 86400  # 90 days; every refresh rotates it
CODE_TTL = 300  # authorization codes
PENDING_TTL = 900  # how long a consent page stays valid
PAIRING_TTL = 30 * 60
PAIRING_DIGITS = 8
STATIC_TOKEN_TTL = 5.0  # seconds a stored static token is trusted before it is read again

# Guessing the pairing code. The code has 8 digits: 10**8 values. Each approval request
# allows MAX_ATTEMPTS_PER_REQUEST wrong codes and then closes, but anyone can open new
# requests, so the real limit is global: after GLOBAL_MAX_FAILURES wrong codes within
# GLOBAL_WINDOW seconds, code entry pauses for GLOBAL_PAUSE seconds. That allows at most
# 20 guesses per 20 minutes, so about 40 guesses while one code is valid (30 minutes).
# A code exists only after the owner makes one, and it is used up on success. The chance
# to hit a given code is at most 40 / 10**8 = 4e-7; even an attacker who tried
# continuously for a year (60 guesses an hour) would land below 0.6 percent.
MAX_ATTEMPTS_PER_REQUEST = 5
GLOBAL_MAX_FAILURES = 20
GLOBAL_WINDOW = 600
GLOBAL_PAUSE = 600

MAX_PENDING = 50  # open approval requests; the oldest is dropped beyond this
MAX_UNAPPROVED_CLIENTS = 50  # registered but never approved; the oldest is dropped beyond this

# Client metadata is written by whoever registers, so it is checked and trimmed.
MAX_CLIENT_NAME = 100
MAX_REDIRECT_URIS = 5
MAX_REDIRECT_URI_LEN = 500
MAX_CLIENT_BYTES = 4096  # stored metadata per client
MAX_BODY_BYTES = 16 * 1024  # request bodies on the OAuth endpoints
KEPT_CLIENT_FIELDS = (
    "client_id", "client_secret", "client_id_issued_at", "client_secret_expires_at", "client_name",
    "redirect_uris", "token_endpoint_auth_method", "grant_types", "response_types", "scope",
)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
CONSENT_PATH = "/consent"
SHORT_ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L


# --- storage -----------------------------------------------------------------------

def store_path() -> Path:
    return config.data_dir() / "oauth.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(store_path().read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("clients", "tokens", "rotated", "pending"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def _save(data: dict[str, Any]) -> None:
    config.write_private(store_path(), json.dumps(data, indent=1))


_held = threading.local()


@contextlib.contextmanager
def _file_lock() -> Iterator[None]:
    """Exclusive lock on oauth.lock, shared with other Dotiphant processes. Re-entrant per thread."""
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    with open(config.data_dir() / "oauth.lock", "a+") as f:
        deadline = time.monotonic() + 10
        while not sync_status.try_lock(f):
            if time.monotonic() > deadline:
                raise RuntimeError("oauth.json is busy in another Dotiphant process. Try again.")
            time.sleep(0.02)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0
            sync_status.unlock(f)


@contextlib.contextmanager
def _transaction() -> Iterator[dict[str, Any]]:
    """Load oauth.json under the lock and save it when the block ends without an error."""
    with _file_lock():
        data = _load()
        yield data
        _save(data)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _drop_expired(data: dict[str, Any], now: float) -> None:
    data["tokens"] = {h: t for h, t in data["tokens"].items() if not t.get("expires_at") or t["expires_at"] > now}
    data["rotated"] = {h: t for h, t in data["rotated"].items() if t.get("expires_at", 0) > now}
    data["pending"] = {k: p for k, p in data["pending"].items() if p.get("created", 0) > now - PENDING_TTL}


def _drop_grant(data: dict[str, Any], grant: str) -> None:
    data["tokens"] = {h: t for h, t in data["tokens"].items() if t.get("grant") != grant}
    data["rotated"] = {h: t for h, t in data["rotated"].items() if t.get("grant") != grant}


# --- pairing code ------------------------------------------------------------------

def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def new_pairing_code() -> str:
    """Create a fresh one-time pairing code, replacing any earlier one."""
    code = str(secrets.randbelow(10**PAIRING_DIGITS)).zfill(PAIRING_DIGITS)
    with _transaction() as data:
        data["pairing"] = {"code": code, "expires_at": time.time() + PAIRING_TTL}
    return code


def pairing_code() -> str:
    """The current pairing code, or a new one if there is none or it expired."""
    with _file_lock():
        p = _load().get("pairing") or {}
        if p.get("code") and p.get("expires_at", 0) > time.time():
            return p["code"]
        return new_pairing_code()


def pause_left() -> int:
    """Seconds until code entry is allowed again after too many wrong codes (0: allowed)."""
    guard = _load().get("guard") or {}
    return max(0, int(guard.get("paused_until", 0) - time.time()))


def check_pairing_code(supplied: str) -> str:
    """Returns 'ok', 'wrong', 'expired' or 'paused'. A correct code is used up."""
    digits = "".join(ch for ch in supplied if ch.isdigit())
    with _transaction() as data:
        now = time.time()
        guard = data.get("guard") or {}
        if guard.get("paused_until", 0) > now:
            return "paused"  # even a correct code: otherwise the pause would not slow guessing
        p = data.get("pairing") or {}
        if not p.get("code"):
            return "expired"
        if p.get("expires_at", 0) <= now:
            data["pairing"] = {}
            return "expired"
        if hmac.compare_digest(digits.encode(), p["code"].encode()):
            data["pairing"] = {}
            return "ok"
        failures = [t for t in guard.get("failures", []) if t > now - GLOBAL_WINDOW] + [now]
        if len(failures) >= GLOBAL_MAX_FAILURES:
            data["guard"] = {"paused_until": now + GLOBAL_PAUSE, "failures": []}
            return "paused"
        data["guard"] = {"failures": failures}
        return "wrong"


# --- redirect URIs and client metadata ------------------------------------------------

def redirect_uri_problem(uri: str) -> str | None:
    """Why this redirect URI is refused, or None when it is fine."""
    if len(uri) > MAX_REDIRECT_URI_LEN:
        return f"redirect_uri is longer than {MAX_REDIRECT_URI_LEN} characters"
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in uri):
        return "redirect_uri contains spaces or control characters"
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    try:
        host = parsed.hostname
    except ValueError:
        host = None
    if scheme not in ("https", "http"):
        return "redirect_uri must use https:// (or http:// on 127.0.0.1, localhost or [::1])"
    if not host:
        return "redirect_uri needs a host"
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        return "redirect_uri must use https:// unless it points at 127.0.0.1, localhost or [::1]"
    if "@" in parsed.netloc:
        return "redirect_uri must not contain a user name (user@host)"
    if "#" in uri:
        return "redirect_uri must not contain a fragment (#...)"
    return None


def _checked_client(client_info: OAuthClientInformationFull) -> dict[str, Any]:
    """The metadata worth keeping, after the limits. Raises RegistrationError."""
    name = client_info.client_name or ""
    if len(name) > MAX_CLIENT_NAME:
        raise RegistrationError("invalid_client_metadata", f"client_name is longer than {MAX_CLIENT_NAME} characters")
    uris = [str(u) for u in client_info.redirect_uris or []]
    if not uris:
        raise RegistrationError("invalid_redirect_uri", "at least one redirect_uri is required")
    if len(uris) > MAX_REDIRECT_URIS:
        raise RegistrationError("invalid_redirect_uri", f"at most {MAX_REDIRECT_URIS} redirect_uris are allowed")
    for uri in uris:
        if problem := redirect_uri_problem(uri):
            raise RegistrationError("invalid_redirect_uri", problem)
    dumped = client_info.model_dump(mode="json", exclude_none=True)
    kept = {k: dumped[k] for k in KEPT_CLIENT_FIELDS if k in dumped}
    if len(json.dumps(kept)) > MAX_CLIENT_BYTES:
        raise RegistrationError("invalid_client_metadata", "client metadata is too large")
    return kept


class BodyLimit:
    """Refuses request bodies over MAX_BODY_BYTES on the OAuth endpoints, before any parsing."""

    PATHS = ("/register", "/token", "/revoke", "/authorize", CONSENT_PATH)

    def __init__(self, app, limit: int = MAX_BODY_BYTES):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in self.PATHS:
            return await self.app(scope, receive, send)
        too_large = PlainTextResponse("Request body too large.", status_code=413)
        length = dict(scope.get("headers") or []).get(b"content-length", b"0")
        if not length.isdigit() or int(length) > self.limit:
            return await too_large(scope, receive, send)
        body, more = b"", True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > self.limit:
                return await too_large(scope, receive, send)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


# --- approval requests (shared with `dotiphant mcp --approve`) ---------------------------

def _short_id() -> str:
    return "".join(secrets.choice(SHORT_ID_ALPHABET) for _ in range(8))


def format_short_id(short: str) -> str:
    return f"{short[:4]}-{short[4:]}"


def _normalize_short_id(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


def find_pending(short: str) -> dict[str, Any] | None:
    """An open approval request by its short id, for the terminal: name, redirect URI, created."""
    data = _load()
    _drop_expired(data, time.time())
    p = data["pending"].get(_normalize_short_id(short))
    return dict(p, id=format_short_id(_normalize_short_id(short))) if p else None


def approve_pending(short: str) -> bool:
    """Mark an open request as approved by the owner in the terminal. False when it is gone."""
    with _transaction() as data:
        _drop_expired(data, time.time())
        p = data["pending"].get(_normalize_short_id(short))
        if not p:
            return False
        p["approved"] = True
        return True


def _forget_pending(short: str) -> None:
    with _transaction() as data:
        data["pending"].pop(short, None)


def _approved_in_terminal(short: str) -> bool:
    return bool((_load()["pending"].get(short) or {}).get("approved"))


# --- clients (for the CLI) ---------------------------------------------------------

def list_clients() -> list[dict[str, Any]]:
    """Clients the owner approved that still hold a valid token."""
    data = _load()
    now = time.time()
    _drop_expired(data, now)
    out = []
    for client_id, c in data["clients"].items():
        tokens = [t for t in data["tokens"].values() if t["client_id"] == client_id]
        if not tokens:
            continue
        out.append({
            "client_id": client_id,
            "name": c["info"].get("client_name") or "(no name)",
            "approved_at": min(t.get("approved_at", now) for t in tokens),
            "redirect": _redirect_hosts(c["info"]),
        })
    return sorted(out, key=lambda c: c["approved_at"])


def revoke(client_id: str) -> int:
    """Remove one client (or 'all') with its tokens. Returns how many clients were removed."""
    with _transaction() as data:
        if client_id == "all":
            count = len({t["client_id"] for t in data["tokens"].values()})
            data["clients"], data["tokens"], data["rotated"] = {}, {}, {}
        else:
            had_tokens = any(t["client_id"] == client_id for t in data["tokens"].values())
            existed = data["clients"].pop(client_id, None) is not None
            data["tokens"] = {h: t for h, t in data["tokens"].items() if t["client_id"] != client_id}
            data["rotated"] = {h: t for h, t in data["rotated"].items() if t.get("client_id") != client_id}
            count = 1 if (existed or had_tokens) else 0
    return count


def _redirect_hosts(info: dict[str, Any]) -> str:
    hosts = sorted({urlparse(str(u)).netloc or str(u) for u in info.get("redirect_uris") or []})
    return ", ".join(hosts)


# --- static token ------------------------------------------------------------------

StaticToken = str | Callable[[], str | None] | None


class StoredToken(config.StoredSecret):
    """The static bearer token as stored in the secrets, read again at most every few seconds.

    A running server stops accepting the token soon after it is deleted (for example by
    `dotiphant logout --forget-credentials`) or replaced. A missing or unreadable token accepts nothing.
    """

    def __init__(self, name: str = "mcp_token", ttl: float = STATIC_TOKEN_TTL,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(name, ttl, clock)


def static_token_matches(source: StaticToken, token: str) -> bool:
    """True when `token` is the static token: a fixed value (--token) or the stored one."""
    expected = source() if callable(source) else source
    return bool(expected) and bool(token) and hmac.compare_digest(token.encode(), expected.encode())


# --- provider ----------------------------------------------------------------------

class DotiphantOAuthProvider:
    """OAuthAuthorizationServerProvider for one user, plus the static bearer token."""

    def __init__(self, base_url: str, static_token: StaticToken = None,
                 log: Callable[[str], None] | None = None):
        self.base_url = base_url.rstrip("/")
        self.resource_url = self.base_url + "/mcp"
        self.static_token = static_token
        self.log = log or (lambda msg: None)
        self.pending: dict[str, dict[str, Any]] = {}
        self.codes: dict[str, AuthorizationCode] = {}

    # clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        c = _load()["clients"].get(client_id)
        return OAuthClientInformationFull.model_validate(c["info"]) if c else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        info = _checked_client(client_info)
        with _transaction() as data:
            now = time.time()
            _drop_expired(data, now)
            approved = {t["client_id"] for t in data["tokens"].values()}
            # Registration is open to anyone who finds the URL: forget stale unapproved clients,
            # and drop the oldest ones so a flood cannot block a real registration.
            unapproved = sorted(((v.get("registered_at", 0), k) for k, v in data["clients"].items()
                                 if k not in approved))
            for registered_at, k in unapproved:
                if now - registered_at >= 86400:
                    del data["clients"][k]
            unapproved = [(t, k) for t, k in unapproved if k in data["clients"]]
            while len(unapproved) >= MAX_UNAPPROVED_CLIENTS:
                del data["clients"][unapproved.pop(0)[1]]
            data["clients"][client_info.client_id] = {"info": info, "registered_at": now}

    # authorization

    def _resource_ok(self, resource: str | None) -> bool:
        if not resource:
            return True
        r = resource.rstrip("/")
        return r in (self.base_url, self.resource_url)

    def _drop_pending(self, request_id: str) -> None:
        p = self.pending.pop(request_id, None)
        if p:
            _forget_pending(p["short"])

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not self._resource_ok(params.resource):
            raise AuthorizeError("invalid_target", "This server only issues tokens for " + self.resource_url)
        now = time.time()
        for k in [k for k, v in self.pending.items() if v["created"] <= now - PENDING_TTL]:
            self._drop_pending(k)
        while len(self.pending) >= MAX_PENDING:  # a flood pushes out the oldest, never new ones
            self._drop_pending(min(self.pending, key=lambda k: self.pending[k]["created"]))
        request_id = secrets.token_urlsafe(24)
        short = _short_id()
        redirect = str(params.redirect_uri)
        self.pending[request_id] = {"client_id": client.client_id, "params": params, "created": now,
                                    "short": short, "attempts": 0}
        with _transaction() as data:
            _drop_expired(data, now)
            data["pending"][short] = {"client_id": client.client_id, "client_name": client.client_name or "",
                                      "redirect_uri": redirect, "created": now, "approved": False}
        return f"{self.base_url}{CONSENT_PATH}?request={request_id}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        code = self.codes.get(authorization_code)
        return code if code and code.client_id == client.client_id else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        if self.codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "authorization code was already used")
        with _transaction() as data:
            return self._issue(data, client.client_id, secrets.token_hex(8), time.time())

    def _issue(self, data: dict[str, Any], client_id: str, grant: str, approved_at: float) -> OAuthToken:
        now = time.time()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        _drop_expired(data, now)
        if client_id not in data["clients"]:
            raise TokenError("invalid_client", "client was revoked")
        base = {"client_id": client_id, "grant": grant, "approved_at": approved_at}
        data["tokens"][_hash(access)] = {**base, "kind": "access", "expires_at": now + ACCESS_TTL}
        data["tokens"][_hash(refresh)] = {**base, "kind": "refresh", "expires_at": now + REFRESH_TTL}
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, scope=SCOPE, refresh_token=refresh)

    # tokens

    def _lookup(self, token: str, kind: str) -> dict[str, Any] | None:
        t = _load()["tokens"].get(_hash(token))
        if not t or t.get("kind") != kind or t.get("expires_at", 0) <= time.time():
            return None
        return t

    def _reuse_detected(self, data: dict[str, Any], token_hash: str) -> bool:
        """A refresh token that was already rotated came back: someone copied it. End the whole grant."""
        old = data["rotated"].get(token_hash)
        if not old:
            return False
        _drop_grant(data, old["grant"])
        self.log(f"A used refresh token was presented again for client {old.get('client_id')}. "
                 "All tokens of that approval were revoked; the app must be approved again.")
        return True

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        t = self._lookup(refresh_token, "refresh")
        if not t:
            if refresh_token and _hash(refresh_token) in _load()["rotated"]:
                with _transaction() as data:
                    self._reuse_detected(data, _hash(refresh_token))
            return None
        if t["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=t["client_id"], scopes=[SCOPE],
                            expires_at=int(t["expires_at"]), resource=self.resource_url)

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        reused = False
        with _transaction() as data:
            h = _hash(refresh_token.token)
            old = data["tokens"].pop(h, None)
            if not old:
                reused = self._reuse_detected(data, h)
            else:
                # Rotate: the old refresh token and the grant's older access tokens stop working.
                # The old token's hash is kept, so presenting it again ends the grant (OAuth 2.1 BCP).
                data["tokens"] = {k: t for k, t in data["tokens"].items() if t.get("grant") != old["grant"]}
                data["rotated"][h] = {"grant": old["grant"], "client_id": old["client_id"],
                                      "expires_at": old.get("expires_at", time.time() + REFRESH_TTL)}
                return self._issue(data, client.client_id, old["grant"], old.get("approved_at", time.time()))
        raise TokenError("invalid_grant", "refresh token was already used" + ("; the grant was revoked" if reused else ""))

    async def load_access_token(self, token: str) -> AccessToken | None:
        if static_token_matches(self.static_token, token):
            return AccessToken(token=token, client_id=STATIC_CLIENT_ID, scopes=[SCOPE], resource=self.resource_url)
        t = self._lookup(token, "access")
        if not t:
            return None
        return AccessToken(token=token, client_id=t["client_id"], scopes=[SCOPE],
                           expires_at=int(t["expires_at"]), resource=self.resource_url)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with _transaction() as data:
            t = data["tokens"].get(_hash(token.token))
            if t:
                _drop_grant(data, t["grant"])

    # consent page

    def _pending(self, request_id: str) -> dict[str, Any] | None:
        p = self.pending.get(request_id or "")
        if p and p["created"] > time.time() - PENDING_TTL:
            return p
        if request_id in self.pending:
            self._drop_pending(request_id)
        return None

    def _grant(self, request_id: str, client: OAuthClientInformationFull, how: str) -> Response:
        params: AuthorizationParams = self.pending[request_id]["params"]
        self._drop_pending(request_id)
        code = secrets.token_urlsafe(32)
        self.codes = {k: v for k, v in self.codes.items() if v.expires_at > time.time()}
        self.codes[code] = AuthorizationCode(
            code=code, scopes=[SCOPE], expires_at=time.time() + CODE_TTL, client_id=client.client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource_url,
        )
        self.log(f"Approved '{client.client_name or client.client_id}' (client id {client.client_id}) {how}.")
        return _redirect(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state))

    async def consent(self, request: Request) -> Response:
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            form: dict[str, Any] = {}
        else:
            form = dict(await request.form())
            request_id = str(form.get("request", ""))
        pending = self._pending(request_id)
        if not pending:
            return _page("This approval link has expired",
                         "<p>Start connecting again from your AI app.</p>", status=400)
        client = await self.get_client(pending["client_id"])
        if client is None:
            self._drop_pending(request_id)
            return _page("This app was removed", "<p>Start connecting again from your AI app.</p>", status=400)
        params: AuthorizationParams = pending["params"]
        page = lambda error=None, status=200: _consent_page(  # noqa: E731
            client, str(params.redirect_uri), request_id, pending["short"], error=error, status=status)
        if request.method == "GET":
            return page()

        action = form.get("action")
        if action == "deny":
            self._drop_pending(request_id)
            self.log(f"Denied access for '{client.client_name or client.client_id}'.")
            return _redirect(construct_redirect_uri(str(params.redirect_uri), error="access_denied",
                                                    error_description="The owner denied access", state=params.state))

        if action == "continue":
            if _approved_in_terminal(pending["short"]):
                return self._grant(request_id, client, "in the terminal")
            return page("Not approved in the terminal yet. Run the command below, answer y, "
                        "then choose Continue again.", 409)

        result = check_pairing_code(str(form.get("code", "")))
        if result == "ok":
            return self._grant(request_id, client, "with the pairing code. The pairing code is used up; "
                                                   "`dotiphant mcp --pair` makes a new one")
        if result == "paused":
            minutes = max(1, -(-pause_left() // 60))
            self.log("Code entry is paused after many wrong pairing codes. Terminal approval still works.")
            return page(f"Too many wrong pairing codes were entered recently, so code entry is paused for about "
                        f"{minutes} minutes. You can still approve in the terminal (below).", 429)
        if result == "expired":
            return page("The pairing code has expired or was already used. Run <code>dotiphant mcp --pair</code> "
                        "on your computer for a new one, or approve in the terminal (below).", 400)
        pending["attempts"] += 1
        self.log("Someone entered a wrong pairing code on the approval page.")
        if pending["attempts"] >= MAX_ATTEMPTS_PER_REQUEST:
            self._drop_pending(request_id)
            return _page("Too many wrong pairing codes",
                         "<p>This approval request is closed. Start connecting again from your AI app.</p>",
                         status=403)
        left = MAX_ATTEMPTS_PER_REQUEST - pending["attempts"]
        return page(f"That pairing code is wrong. Check the terminal where Dotiphant runs and try again "
                    f"({left} tries left for this request).", 403)


# --- HTML --------------------------------------------------------------------------

_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}

_STYLE = """
body{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem;
color:#1b1b1b;background:#fafafa}
h1{font-size:1.3rem}h2{font-size:1.05rem;margin:1.5rem 0 .3rem}
.box{background:#fff;border:1px solid #ddd;border-radius:8px;padding:1rem 1.2rem}
input{font-size:1.4rem;letter-spacing:.15em;width:100%;box-sizing:border-box;padding:.4rem;margin:.3rem 0 1rem}
button{font-size:1rem;padding:.5rem 1rem;margin-right:.5rem;border-radius:6px;border:1px solid #888;background:#fff}
button.ok{background:#1b1b1b;color:#fff;border-color:#1b1b1b}.err{color:#b00020}.small{color:#555;font-size:.9rem}
code{background:#eee;padding:0 .2rem}.uri{word-break:break-all;font-family:ui-monospace,monospace;font-size:.95rem}
.host{font-size:1.25rem;font-weight:700;background:#fff3c4;padding:0 .2rem}
@media (prefers-color-scheme:dark){body{background:#151515;color:#eee}.box{background:#202020;border-color:#333}
button{background:#2a2a2a;color:#eee}button.ok{background:#eee;color:#111}code{background:#333}.small{color:#aaa}
.host{background:#5a4a00}}
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
           f"<body><h1>{html.escape(title)}</h1>{body}</body></html>")
    return HTMLResponse(doc, status_code=status, headers=_HEADERS)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


def uri_html(uri: str) -> str:
    """The full URI, escaped, with its host set apart so it is hard to miss."""
    parsed = urlparse(uri)
    prefix = f"{parsed.scheme}://{parsed.netloc}"
    if not parsed.netloc or not uri.startswith(prefix):
        return f"<span class=uri>{html.escape(uri)}</span>"
    return (f"<span class=uri>{html.escape(parsed.scheme)}://<span class=host>{html.escape(parsed.netloc)}</span>"
            f"{html.escape(uri[len(prefix):])}</span>")


def _consent_page(client: OAuthClientInformationFull, redirect_uri: str, request_id: str, short: str,
                  error: str | None = None, status: int = 200) -> HTMLResponse:
    name = html.escape(client.client_name or "An unnamed app")
    host = html.escape(urlparse(redirect_uri).netloc or redirect_uri)
    err = f"<p class=err>{error}</p>" if error else ""
    rid = html.escape(request_id)
    command = f"dotiphant mcp --approve {format_short_id(short)}"
    body = f"""
<div class=box>
<p><b>{name}</b> wants read-only access to your Dotiphant archive: it can search and read your archived
Telegram chats. It cannot send, edit or delete anything. The name is chosen by the app itself.</p>
<p>After you approve, your browser goes to this address, and whoever runs it gets the access:</p>
<p>{uri_html(redirect_uri)}</p>
<p class=small>Approve only if <b>{host}</b> belongs to the AI app you are connecting right now.</p>
</div>
{err}
<h2>Option 1: approve in your terminal (safer)</h2>
<p>On the computer where Dotiphant runs, run:</p>
<p><code>{html.escape(command)}</code></p>
<p class=small>It shows the app name and this address again and asks you to confirm. Then choose Continue.</p>
<form method=post action="{CONSENT_PATH}">
<input type=hidden name=request value="{rid}">
<button class=ok name=action value=continue>Continue</button>
</form>
<h2>Option 2: enter the pairing code</h2>
<form method=post action="{CONSENT_PATH}">
<input type=hidden name=request value="{rid}">
<p><label for=code>Pairing code</label><br>
<span class=small>Shown in the terminal where <code>dotiphant mcp --http</code> runs, or with
<code>dotiphant mcp --pair</code>.</span></p>
<input id=code name=code inputmode=numeric autocomplete=one-time-code placeholder="1234-5678">
<button class=ok name=action value=approve>Approve</button>
<button name=action value=deny>Deny</button>
</form>
<p class=small>If you did not start connecting an app to Dotiphant just now, choose Deny.</p>
"""
    return _page("Allow access to your Telegram archive?", body, status)
