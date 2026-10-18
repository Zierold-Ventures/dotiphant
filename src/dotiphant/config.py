"""Paths, settings and secret storage.

Everything Dotiphant keeps lives in one private data directory. API credentials and the
Telegram session go to the OS keychain when one is available, and only fall back to
files with 0600 permissions when it is not (for example a headless Linux server).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

KEYRING_SERVICE = "Dotiphant (unofficial Telegram archive)"
API_PAGE = "https://my.telegram.org"
API_ID_RULE = "The api_id is a number with 3 to 12 digits, like 1234567."
API_HASH_RULE = "The api_hash is 32 characters of 0-9 and a-f."


def data_dir_path() -> Path:
    """The data directory (DOTIPHANT_HOME or the platform default), without creating it."""
    if env := os.environ.get("DOTIPHANT_HOME"):
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Dotiphant"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Dotiphant"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "dotiphant"


def data_dir() -> Path:
    """Return the private data directory, creating it with 0700 permissions."""
    path = data_dir_path()
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)
    return path


def db_path() -> Path:
    return data_dir() / "dotiphant.db"


def media_dir() -> Path:
    path = data_dir() / "media"
    path.mkdir(exist_ok=True)
    return path


def lock_path() -> Path:
    return data_dir() / "sync.lock"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def status_path() -> Path:
    return data_dir() / "status.json"


@dataclass
class Settings:
    """User choices saved in settings.json. Unknown keys are ignored."""

    include_private: bool = True  # one-to-one chats with people
    include_groups: bool = True  # basic groups, supergroups and forums
    include_channels: bool = False  # broadcast channels
    include_bots: bool = False  # chats with bots
    include_archived: bool = True  # chats in the Telegram "Archived" folder
    history_days: int | None = 365  # how far back the first sync goes; None means everything
    excluded_chats: list[int] = field(default_factory=list)
    favorites: list[int] = field(default_factory=list)
    # Chats whose attachments every sync downloads (files up to 50 MB), not only on request.
    auto_download_chats: list[int] = field(default_factory=list)
    sync_interval_minutes: int = 15
    # Which chats AI apps (the MCP server) may read. "all": every chat except ai_hidden_chats.
    # "selected": only ai_allowed_chats. The viewer and the CLI always show everything.
    ai_access: str = "all"
    ai_hidden_chats: list[int] = field(default_factory=list)
    ai_allowed_chats: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.ai_access not in AI_ACCESS:
            self.ai_access = "selected"  # an unknown value shows AI apps nothing rather than everything
        self.ai_hidden_chats = _chat_id_list(self.ai_hidden_chats)
        self.ai_allowed_chats = _chat_id_list(self.ai_allowed_chats)
        self.auto_download_chats = _chat_id_list(self.auto_download_chats)

    @classmethod
    def load(cls, if_unreadable: "Settings | None" = None) -> "Settings":
        """settings.json, or the defaults when it does not exist.

        A file that exists but cannot be read also gives the defaults, unless `if_unreadable`
        is passed: the MCP server passes settings that hide every chat, so a broken file never
        opens the archive to AI apps.
        """
        try:
            return cls._read()
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError):
            return if_unreadable if if_unreadable is not None else cls()

    @classmethod
    def _read(cls) -> "Settings":
        """settings.json as it is; raises when the file is missing or cannot be read."""
        raw = json.loads(settings_path().read_text())
        if not isinstance(raw, dict):
            raise ValueError("settings.json is not an object")
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    @classmethod
    def _load_for_update(cls) -> "Settings":
        """Like load(), but a file that exists and cannot be read raises SettingsUnreadable."""
        try:
            return cls._read()
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, TypeError) as exc:
            raise SettingsUnreadable(UNREADABLE_TEXT.format(path=settings_path())) from exc

    def set_ai_visible(self, chat_ids: list[int], visible: bool) -> None:
        """Show chats to AI apps or hide them, in both modes.

        Pass the chat with its linked groups (query.linked_chat_ids): the first id is stored,
        and every id is removed from the opposite list.
        """
        if not chat_ids:
            return
        add, drop = ("ai_allowed_chats", "ai_hidden_chats") if visible else ("ai_hidden_chats", "ai_allowed_chats")
        setattr(self, drop, [c for c in getattr(self, drop) if c not in chat_ids])
        kept = getattr(self, add)
        if not any(c in kept for c in chat_ids):
            kept.append(chat_ids[0])

    def save(self) -> None:
        """Write these settings as they are, replacing every field in settings.json.

        To change some fields, use update_settings: it rereads the file under the lock, so
        a change made by another process at the same moment is not lost. Raises
        SettingsUnreadable when settings.json exists but cannot be read.
        """
        with settings_lock():
            Settings._load_for_update()  # refuse to overwrite a file we could not read
            self._write()

    def _write(self) -> None:
        write_private(settings_path(), json.dumps(asdict(self), indent=2))


AI_ACCESS = ("all", "selected")
UNREADABLE_TEXT = ("{path} cannot be read, so it was left unchanged. Fix the file, or delete it "
                   "to start over with the default settings.")
# How long a settings change waits for another process that is changing settings.json.
SETTINGS_LOCK_WAIT = 10.0


class SettingsUnreadable(ValueError):
    """settings.json exists but cannot be read, so changing it would throw away its contents."""


class SettingsBusy(RuntimeError):
    pass


_held = threading.local()


@contextlib.contextmanager
def settings_lock() -> Iterator[None]:
    """Exclusive lock on settings.lock, shared by every Dotiphant process and thread.

    Re-entrant within one thread. Waits up to SETTINGS_LOCK_WAIT seconds.
    """
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    from .status import try_lock, unlock  # status imports this module

    with open(data_dir() / "settings.lock", "a+") as f:
        deadline = time.monotonic() + SETTINGS_LOCK_WAIT
        while not try_lock(f):
            if time.monotonic() > deadline:
                raise SettingsBusy("settings.json is busy in another Dotiphant process. Try again.")
            time.sleep(0.01)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0
            unlock(f)


def update_settings(change: Callable[[Settings], None]) -> Settings:
    """Read settings.json, apply `change`, and save, all under settings_lock.

    Every read-modify-write of settings goes through here, so two processes (the viewer
    and a CLI command, say) never overwrite each other's changes. Returns the saved
    settings. Raises SettingsUnreadable, without writing, when the file cannot be read.
    """
    with settings_lock():
        settings = Settings._load_for_update()
        change(settings)
        settings.__post_init__()
        settings._write()
        return settings


def _chat_id_list(value: object) -> list[int]:
    """A list of unique chat ids from settings.json, skipping anything that is not one."""
    out: list[int] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, bool):
            continue
        if isinstance(item, str) and re.fullmatch(r"-?\d{1,19}", item.strip()):
            item = int(item)
        if isinstance(item, int) and -(2**63) <= item < 2**63 and item not in out:
            out.append(item)
    return out


def write_private(path: Path, text: str) -> None:
    """Write a file atomically with 0600 permissions.

    Each call writes its own temporary file, so two processes saving at once never mix
    their contents; the last one to finish wins.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:  # mkstemp creates it with 0600
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# --- secrets -----------------------------------------------------------------------

class SecretsUnavailable(RuntimeError):
    pass


SECRET_STORES = ("keyring", "file")


def secret_store() -> str:
    """Where secrets go, from DOTIPHANT_SECRETS: 'keyring' (default) or 'file'."""
    value = os.environ.get("DOTIPHANT_SECRETS", "").strip().lower() or "keyring"
    if value not in SECRET_STORES:
        raise ValueError(f"DOTIPHANT_SECRETS must be 'keyring' or 'file', not {value!r}.")
    return value


def _keyring():
    """The system keyring, or None when secrets go to files or no keyring is available."""
    if secret_store() == "file":
        return None
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring) or "fail" in type(backend).__module__:
            return None
        return keyring
    except Exception:
        return None


def secrets_backend_name() -> str:
    kr = _keyring()
    return type(kr.get_keyring()).__name__ if kr else "file (0600)"


def _secret_file(name: str, create: bool = False) -> Path:
    """The fallback file for a secret. Only writers create the secrets/ folder."""
    path = data_dir_path() / "secrets"
    if create:
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            path.chmod(0o700)
    return path / name


def get_secret(name: str) -> str | None:
    kr = _keyring()
    if kr:
        try:
            value = kr.get_password(KEYRING_SERVICE, name)
            if value is not None:
                return value
        except Exception:
            pass
    path = _secret_file(name)
    return path.read_text().strip() if path.exists() else None


def set_secret(name: str, value: str) -> str:
    """Store a secret. Returns where it went: the keychain backend name or 'file'."""
    kr = _keyring()
    if kr:
        try:
            kr.set_password(KEYRING_SERVICE, name, value)
            if kr.get_password(KEYRING_SERVICE, name) == value:
                _secret_file(name).unlink(missing_ok=True)
                return secrets_backend_name()
        except Exception:
            pass
    data_dir()
    write_private(_secret_file(name, create=True), value)
    return "file"


def delete_secret(name: str) -> None:
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass
    _secret_file(name).unlink(missing_ok=True)


class StoredSecret:
    """A stored secret, read again at most every `ttl` seconds.

    Long-running servers use it to notice soon when a secret they rely on is deleted (for
    example by `dotiphant logout --forget-credentials`) or replaced, without reading the
    keychain on every request. A secret that cannot be read counts as missing.
    """

    def __init__(self, name: str, ttl: float = 5.0, clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.ttl = ttl
        self.clock = clock
        self._value: str | None = None
        self._read_at: float | None = None
        self._lock = threading.Lock()

    def __call__(self) -> str | None:
        now = self.clock()
        with self._lock:
            if self._read_at is None or now - self._read_at >= self.ttl:
                try:
                    self._value = get_secret(self.name)
                except Exception:
                    self._value = None
                self._read_at = now
            return self._value


def api_credentials() -> tuple[int, str]:
    """Telegram API ID and hash: environment first, then the keychain."""
    api_id = os.environ.get("DOTIPHANT_API_ID") or get_secret("api_id")
    api_hash = os.environ.get("DOTIPHANT_API_HASH") or get_secret("api_hash")
    if not api_id or not api_hash:
        raise SecretsUnavailable("Telegram API credentials are missing. Run `dotiphant setup`.")
    return int(api_id), api_hash


# --- credential checks shared by `dotiphant setup` and the browser setup ---------------

def valid_api_id(value: object) -> bool:
    return isinstance(value, (str, int)) and bool(re.fullmatch(r"\d{3,12}", str(value).strip()))


def valid_api_hash(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{32}", value.strip()))


def has_credentials() -> bool:
    try:
        api_credentials()
        return True
    except (SecretsUnavailable, ValueError):
        return False


def has_session() -> bool:
    return bool(get_secret("session"))


def save_credentials(api_id: str, api_hash: str) -> str:
    """Store a new API id and hash. Returns where they went (see set_secret).

    A login belongs to the app that made it, so a stored session is deleted whenever the
    credentials change.
    """
    api_id, api_hash = str(api_id).strip(), api_hash.strip().lower()
    if not valid_api_id(api_id):
        raise ValueError(API_ID_RULE)
    if not valid_api_hash(api_hash):
        raise ValueError(API_HASH_RULE)
    if (get_secret("api_id"), get_secret("api_hash")) != (api_id, api_hash):
        delete_secret("session")
    where = set_secret("api_id", api_id)
    set_secret("api_hash", api_hash)
    return where


def forget_credentials() -> None:
    """Delete the API id, hash and the session that was made with them."""
    for name in ("api_id", "api_hash", "session"):
        delete_secret(name)


def credentials_rejected(exc: BaseException) -> bool:
    """True when Telegram refused the API id and hash themselves (not the login)."""
    name = type(exc).__name__
    return "ApiIdInvalid" in name or "ApiIdPublishedFlood" in name or "api_id/api_hash" in str(exc)
