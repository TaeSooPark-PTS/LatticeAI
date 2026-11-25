from __future__ import annotations

import json
import time
from pathlib import Path

from latticeai.core import sessions as sessions_mod
from latticeai.core.sessions import SessionStore, _hash_token


def _write_session(
    data_dir: Path, token: str, email: str, *, created: float | None = None
) -> None:
    path = data_dir / "sessions.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    existing[_hash_token(token)] = [
        email,
        created if created is not None else time.time(),
        email,
    ]
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing), encoding="utf-8")


def test_a_token_written_after_construction_is_found(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    assert store.get_email("later-token") is None

    _write_session(tmp_path, "later-token", "alice@example.com")

    assert store.get_email("later-token") == "alice@example.com"
    assert store.get_subject("later-token") == "alice@example.com"


def test_a_session_file_that_appears_after_construction_is_picked_up(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "fresh"
    store = SessionStore(data_dir)

    _write_session(data_dir, "first-ever", "bob@example.com")

    assert store.get_email("first-ever") == "bob@example.com"


def test_an_unchanged_file_is_stat_ed_but_never_re_parsed(
    tmp_path: Path, monkeypatch
) -> None:
    _write_session(tmp_path, "known", "carol@example.com")
    store = SessionStore(tmp_path)
    monkeypatch.setattr(sessions_mod, "SESSION_RELOAD_MIN_INTERVAL", 0.0)

    reads = {"n": 0}
    real_load = sessions_mod.load_sessions

    def counting_load(data_dir=None):
        reads["n"] += 1
        return real_load(data_dir)

    monkeypatch.setattr(sessions_mod, "load_sessions", counting_load)

    for _ in range(25):
        assert store.get_email("no-such-token") is None

    assert reads["n"] == 0, "an unchanged file must not be parsed again"
    assert store.get_email("known") == "carol@example.com"


def test_a_constantly_changing_file_is_parsed_at_most_once_per_interval(
    tmp_path: Path, monkeypatch
) -> None:
    store = SessionStore(tmp_path)

    reads = {"n": 0}
    real_load = sessions_mod.load_sessions

    def counting_load(data_dir=None):
        reads["n"] += 1
        return real_load(data_dir)

    monkeypatch.setattr(sessions_mod, "load_sessions", counting_load)
    monkeypatch.setattr(sessions_mod, "SESSION_RELOAD_MIN_INTERVAL", 3600.0)

    for index in range(25):
        _write_session(tmp_path, f"churn-{index}", f"u{index}@example.com")
        assert store.get_email("no-such-token") is None

    assert reads["n"] == 1

    monkeypatch.setattr(sessions_mod, "SESSION_RELOAD_MIN_INTERVAL", 0.0)
    assert store.get_email("churn-24") == "u24@example.com"


def test_the_interval_does_not_delay_the_first_lookup_after_boot(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    _write_session(tmp_path, "same-second", "dave@example.com")

    assert store.get_email("same-second") == "dave@example.com"


def test_an_expired_entry_is_still_expired_after_a_reload(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sessions_mod, "SESSION_RELOAD_MIN_INTERVAL", 0.0)
    store = SessionStore(tmp_path, ttl_seconds=60, refresh_threshold_seconds=10**9)

    _write_session(tmp_path, "stale", "erin@example.com", created=time.time() - 3600)

    assert store.get_email("stale") is None
    assert _hash_token("stale") not in store._sessions


def test_this_store_writes_onto_the_file_rather_than_over_it(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sessions_mod, "SESSION_RELOAD_MIN_INTERVAL", 10**9)
    store = SessionStore(tmp_path)
    _write_session(tmp_path, "written-elsewhere", "frank@example.com")

    mine = store.create("gina@example.com", email="gina@example.com")

    on_disk = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert _hash_token("written-elsewhere") in on_disk
    assert _hash_token(mine) in on_disk

    store.invalidate(mine)
    on_disk = json.loads((tmp_path / "sessions.json").read_text(encoding="utf-8"))
    assert _hash_token("written-elsewhere") in on_disk
    assert _hash_token(mine) not in on_disk


def test_our_own_write_is_not_mistaken_for_someone_elses(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sessions_mod, "SESSION_RELOAD_MIN_INTERVAL", 0.0)
    store = SessionStore(tmp_path)
    store.create("heidi@example.com", email="heidi@example.com")

    reads = {"n": 0}
    real_load = sessions_mod.load_sessions

    def counting_load(data_dir=None):
        reads["n"] += 1
        return real_load(data_dir)

    monkeypatch.setattr(sessions_mod, "load_sessions", counting_load)
    assert store.get_email("nope") is None

    assert reads["n"] == 0


def test_an_unusable_data_dir_reports_no_stamp_instead_of_raising(
    tmp_path: Path,
) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("i am a file", encoding="utf-8")

    assert sessions_mod._sessions_stamp(blocked) is None
