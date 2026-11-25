import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from latticeai.core.io_utils import atomic_write_json
from latticeai.core.security import sha256_hex

SESSION_TTL = 60 * 60 * 24
SESSION_REFRESH_THRESHOLD = 60 * 15
SESSION_RELOAD_MIN_INTERVAL = 1.0
_lock = threading.Lock()

_HEX64 = frozenset("0123456789abcdef")


def _hash_token(token: str) -> str:
    return sha256_hex(token or "")


def _looks_hashed(key: str) -> bool:
    return len(key) == 64 and set(key) <= _HEX64


def _sessions_file(data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        try:
            from latticeai.core.config import Config

            data_dir = Config.from_env().data_dir
        except (
            ImportError,
            OSError,
            ValueError,
            TypeError,
            RuntimeError,
            AttributeError,
        ):
            import os

            data_dir = Path(os.getenv("LATTICEAI_DATA_DIR") or (Path.home() / ".ltcai"))
    d = data_dir
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d / "sessions.json"


def load_sessions(data_dir: Optional[Path] = None) -> Dict[str, tuple]:
    try:
        f = _sessions_file(data_dir)
        if f.exists():
            raw = json.loads(f.read_text())
            sessions: Dict[str, tuple] = {}
            migrated = False
            for key, value in raw.items():
                if _looks_hashed(key):
                    sessions[key] = tuple(value)
                else:
                    sessions[_hash_token(key)] = tuple(value)
                    migrated = True
            if migrated:
                persist_sessions(sessions, data_dir)
            return sessions
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
    ) as e:
        logging.warning("load_sessions failed (starting empty): %s", e)
    return {}


def persist_sessions(
    sessions: Dict[str, tuple], data_dir: Optional[Path] = None
) -> None:
    try:
        atomic_write_json(
            _sessions_file(data_dir),
            {key: list(value) for key, value in sessions.items()},
        )
    except (OSError, ValueError, TypeError, RuntimeError) as e:
        logging.warning("persist_sessions failed: %s", e)


def _sessions_stamp(data_dir: Optional[Path] = None) -> Optional[tuple]:
    try:
        stat = _sessions_file(data_dir).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _entry_subject(entry: tuple) -> Optional[str]:
    return entry[0] if entry else None


def _entry_email(entry: tuple) -> Optional[str]:
    if len(entry) >= 3 and entry[2]:
        return entry[2]
    return entry[0] if entry else None


def _entry_created_at(entry: tuple) -> float:
    if len(entry) >= 2:
        return float(entry[1])
    return 0.0


class SessionStore:
    def __init__(
        self,
        data_dir: Optional[Path] = None,
        *,
        ttl_seconds: int = SESSION_TTL,
        refresh_threshold_seconds: int = SESSION_REFRESH_THRESHOLD,
    ):
        self._data_dir = data_dir
        self._ttl_seconds = int(ttl_seconds or SESSION_TTL)
        self._refresh_threshold_seconds = int(
            refresh_threshold_seconds or SESSION_REFRESH_THRESHOLD
        )
        self._sessions: Dict[str, tuple] = load_sessions(data_dir)
        self._loaded_stamp: Optional[tuple] = _sessions_stamp(data_dir)
        self._last_reload_at: float = float("-inf")

    def _reload_if_stale(self, *, force: bool = False) -> bool:
        stamp = _sessions_stamp(self._data_dir)
        if stamp == self._loaded_stamp:
            return False
        now = time.monotonic()
        if not force and now - self._last_reload_at < SESSION_RELOAD_MIN_INTERVAL:
            return False
        self._last_reload_at = now
        self._sessions = load_sessions(self._data_dir)
        self._loaded_stamp = stamp
        return True

    def _persist(self) -> None:
        persist_sessions(self._sessions, self._data_dir)
        self._loaded_stamp = _sessions_stamp(self._data_dir)

    def create(self, subject: str, *, email: Optional[str] = None) -> str:
        token = secrets.token_urlsafe(32)
        with _lock:
            self._reload_if_stale(force=True)
            self._sessions[_hash_token(token)] = (
                subject,
                time.time(),
                email or subject,
            )
            self._persist()
        return token

    def get_email(self, token: str) -> Optional[str]:
        entry = self._get_entry(token)
        return _entry_email(entry) if entry else None

    def get_subject(self, token: str) -> Optional[str]:
        entry = self._get_entry(token)
        return _entry_subject(entry) if entry else None

    def _get_entry(self, token: str) -> Optional[tuple]:
        now = time.time()
        key = _hash_token(token)
        with _lock:
            entry = self._sessions.get(key)
            if entry is None and self._reload_if_stale():
                entry = self._sessions.get(key)
            if entry is None:
                return None
            created_at = _entry_created_at(entry)
            if now - created_at > self._ttl_seconds:
                self._sessions.pop(key, None)
                self._persist()
                return None
            if now - created_at > self._refresh_threshold_seconds:
                refreshed = (_entry_subject(entry), now, _entry_email(entry))
                self._sessions[key] = refreshed
                self._persist()
                return refreshed
            return entry

    def invalidate(self, token: str) -> None:
        with _lock:
            self._reload_if_stale(force=True)
            self._sessions.pop(_hash_token(token), None)
            self._persist()
