"""File-backed session store with sliding-window TTL."""

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional

SESSION_TTL = 60 * 60 * 24  # 24 hours
SESSION_REFRESH_THRESHOLD = 60 * 15  # only persist if >15 min since last bump
_lock = threading.Lock()


def _sessions_file(data_dir: Optional[Path] = None) -> Path:
    d = data_dir or Path(os.getenv("LATTICEAI_DATA_DIR") or (Path.home() / ".ltcai"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "sessions.json"


def load_sessions(data_dir: Optional[Path] = None) -> Dict[str, tuple]:
    try:
        f = _sessions_file(data_dir)
        if f.exists():
            raw = json.loads(f.read_text())
            return {k: tuple(v) for k, v in raw.items()}
    except Exception as e:
        logging.warning("load_sessions failed (starting empty): %s", e)
    return {}


def persist_sessions(sessions: Dict[str, tuple], data_dir: Optional[Path] = None) -> None:
    try:
        _sessions_file(data_dir).write_text(json.dumps({k: list(v) for k, v in sessions.items()}, ensure_ascii=False))
    except Exception as e:
        logging.warning("persist_sessions failed: %s", e)


class SessionStore:
    def __init__(self, data_dir: Optional[Path] = None):
        self._data_dir = data_dir
        self._sessions: Dict[str, tuple] = load_sessions(data_dir)

    def create(self, email: str) -> str:
        token = secrets.token_urlsafe(32)
        with _lock:
            self._sessions[token] = (email, time.time())
            persist_sessions(self._sessions, self._data_dir)
        return token

    def get_email(self, token: str) -> Optional[str]:
        now = time.time()
        with _lock:
            entry = self._sessions.get(token)
            if entry is None:
                return None
            email, created_at = entry
            if now - created_at > SESSION_TTL:
                self._sessions.pop(token, None)
                persist_sessions(self._sessions, self._data_dir)
                return None
            if now - created_at > SESSION_REFRESH_THRESHOLD:
                self._sessions[token] = (email, now)
                persist_sessions(self._sessions, self._data_dir)
            return email

    def invalidate(self, token: str) -> None:
        with _lock:
            self._sessions.pop(token, None)
            persist_sessions(self._sessions, self._data_dir)
