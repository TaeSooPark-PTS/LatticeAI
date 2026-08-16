"""File-backed session store with sliding-window TTL.

v4: bearer tokens are stored **hashed** (sha256) at rest — a process that can
read ``sessions.json`` must not be able to hijack every session. Pre-v4 files
holding raw tokens are migrated transparently on load (sessions survive the
upgrade; the raw token never touches disk again).

11.8.0: the in-memory map is a **cache of the file, not the file itself.**
Since v11.6.0 the writer is ``lattice-auth``: it creates the session and
appends it to ``sessions.json``, and this process only ever reads. Loading
once in ``__init__`` therefore made every login that happened after worker
boot invisible here — silently under ``trusted_local_owner`` (the anonymous
owner path answers first), and as a flat 401 under
``LATTICEAI_REQUIRE_AUTH=true``, for a token that is sitting in the file.

A lookup that misses now re-reads the file before giving up. Two guards keep
that from turning a token-guessing burst into a disk-read burst: the re-read
is skipped when the file's ``mtime_ns``/``size`` are unchanged since the last
load, and — for a file that *is* changing — throttled to one parse per
``SESSION_RELOAD_MIN_INTERVAL``. Both are checked under the same lock that
guards the map, so concurrent misses collapse into one read.
"""

import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from latticeai.core.io_utils import atomic_write_json
from latticeai.core.security import sha256_hex

SESSION_TTL = 60 * 60 * 24  # 24 hours
SESSION_REFRESH_THRESHOLD = 60 * 15  # only persist if >15 min since last bump
#: Floor between two miss-triggered re-reads of ``sessions.json``. A wrong or
#: expired token is the common case for an unauthenticated probe, and each one
#: misses; without a floor a burst of them would be a burst of file reads.
#: Well under any human's retry, so a real login is still picked up at once.
SESSION_RELOAD_MIN_INTERVAL = 1.0
_lock = threading.Lock()

_HEX64 = frozenset("0123456789abcdef")


def _hash_token(token: str) -> str:
    """Session keys are hashed at rest; a missing token hashes the empty string."""
    return sha256_hex(token or "")


def _looks_hashed(key: str) -> bool:
    return len(key) == 64 and set(key) <= _HEX64


def _sessions_file(data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        try:
            from latticeai.core.config import Config
            data_dir = Config.from_env().data_dir
        except Exception:
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
                    # Pre-v4 entry: the key IS the raw bearer token. Re-key it
                    # under its hash so the plaintext stops living on disk.
                    sessions[_hash_token(key)] = tuple(value)
                    migrated = True
            if migrated:
                persist_sessions(sessions, data_dir)
            return sessions
    except Exception as e:
        logging.warning("load_sessions failed (starting empty): %s", e)
    return {}


def persist_sessions(sessions: Dict[str, tuple], data_dir: Optional[Path] = None) -> None:
    try:
        atomic_write_json(
            _sessions_file(data_dir),
            {key: list(value) for key, value in sessions.items()},
        )
    except Exception as e:
        logging.warning("persist_sessions failed: %s", e)


def _sessions_stamp(data_dir: Optional[Path] = None) -> Optional[tuple]:
    """``(mtime_ns, size)`` of ``sessions.json``, or ``None`` when unreadable.

    ``None`` is *not* "unchanged": a file that has just appeared and one that
    was just removed both need the map rebuilt, and both land here.
    """
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
        self._refresh_threshold_seconds = int(refresh_threshold_seconds or SESSION_REFRESH_THRESHOLD)
        self._sessions: Dict[str, tuple] = load_sessions(data_dir)
        # Stamped *after* the load, so a pre-v4 file that ``load_sessions``
        # rewrote on the way in is not mistaken for a foreign write.
        self._loaded_stamp: Optional[tuple] = _sessions_stamp(data_dir)
        # ``-inf`` and not "now": the first *changed* file must be free to
        # load, or a login that lands in the same second as the worker's boot
        # stays invisible for a second for no reason.
        self._last_reload_at: float = float("-inf")

    def _reload_if_stale(self, *, force: bool = False) -> bool:
        """Re-read ``sessions.json`` when it has changed. Caller holds ``_lock``.

        Returns whether the map was replaced. The two guards answer two
        different costs, in the order that keeps both cheap:

        * the **stamp** runs first and on every miss. It is one ``stat``, and
          when the file has not moved — the overwhelming case, since a wrong
          token is what an unauthenticated probe sends — that is the entire
          cost. No parse, no allocation.
        * the **interval** runs only once the file *has* moved, and bounds how
          often a busy file is actually parsed. It never drops the change: the
          stamp is recorded only when the load really happened, so the next
          miss past the interval still sees the file as new.

        ``force`` skips the interval only. It is for the two paths that are
        about to *write* the file: a throttled read there would merge onto a
        stale map and drop somebody else's session, and a login or a logout is
        rare enough that the read costs nothing worth counting.
        """
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
        """Write the map, then re-stamp. Caller holds ``_lock``.

        Re-stamping matters: without it our own write looks like somebody
        else's the next time a lookup misses, and the store re-reads the file
        it just produced.
        """
        persist_sessions(self._sessions, self._data_dir)
        self._loaded_stamp = _sessions_stamp(self._data_dir)

    def create(self, subject: str, *, email: Optional[str] = None) -> str:
        token = secrets.token_urlsafe(32)
        with _lock:
            # Merge onto whatever is on disk rather than over it — this process
            # is not the only writer.
            self._reload_if_stale(force=True)
            self._sessions[_hash_token(token)] = (subject, time.time(), email or subject)
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
                # A miss is the only thing that can be wrong because the map is
                # stale: an entry we *do* hold was really issued, and one we
                # hold that the file no longer has is handled by the TTL and by
                # ``active_session_email``'s account check on every request.
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
