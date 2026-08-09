"""wp11: the debounced local-knowledge watcher, driven through a fake watchdog.

``LocalKnowledgeWatcher`` imports ``watchdog`` in ``__init__`` and keeps the
``Observer`` / ``FileSystemEventHandler`` classes it found. That import is the
seam these tests use: injecting fake modules through ``sys.modules`` exercises
the real wiring (handler subclass, schedule, start, stop, debounce) without a
single OS filesystem event, and injecting ``None`` reproduces the
"watchdog is not installed" state that CI can otherwise never reach.

Nothing here waits: the debounce is set to an hour and the reindex callback the
timer would eventually run is invoked directly, so the assertions are about
what the watcher does, not about how fast the suite happens to be.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from latticeai.services.local_knowledge import LocalKnowledgeWatcher, _LocalWatchHandler


class _FakeObserver:
    """Records what the watcher asked the OS watcher to do."""

    def __init__(self, *, schedule_error: Optional[Exception] = None,
                 stop_error: Optional[Exception] = None) -> None:
        self.scheduled: List[Any] = []
        self.started = False
        self.stopped = False
        self.joined: Any = "not-joined"
        self._schedule_error = schedule_error
        self._stop_error = stop_error

    def schedule(self, handler, path, recursive=False):
        if self._schedule_error is not None:
            raise self._schedule_error
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        if self._stop_error is not None:
            raise self._stop_error
        self.stopped = True

    def join(self, timeout=None):
        self.joined = timeout


class _FakeHandlerBase:
    """Stands in for ``watchdog.events.FileSystemEventHandler``."""

    def __init__(self):
        self.base_initialised = True


def _install_fake_watchdog(monkeypatch, observers: List[_FakeObserver]) -> None:
    """Point ``watchdog`` at fakes for the duration of one test."""
    events = ModuleType("watchdog.events")
    events.FileSystemEventHandler = _FakeHandlerBase
    observers_module = ModuleType("watchdog.observers")
    observers_module.Observer = lambda: observers.pop(0)
    monkeypatch.setitem(sys.modules, "watchdog.events", events)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observers_module)


def _watcher(monkeypatch, observers: List[_FakeObserver], *, get_graph=lambda: None,
             hooks: Any = None) -> LocalKnowledgeWatcher:
    _install_fake_watchdog(monkeypatch, observers)
    return LocalKnowledgeWatcher(get_graph, debounce_seconds=3600, hooks=hooks)


class _Hooks:
    def __init__(self) -> None:
        self.events: List[Any] = []

    def fire_hook(self, kind, event, **kwargs):
        self.events.append((kind, event, kwargs))
        return {"blocked": False}

    def payloads(self, event: str) -> List[Dict[str, Any]]:
        return [item[2].get("payload", {}) for item in self.events if item[1] == event]


# ── watchdog missing ─────────────────────────────────────────────────────────

def test_watcher_without_watchdog_reports_why_and_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "watchdog.observers", None)
    watcher = LocalKnowledgeWatcher(lambda: None)

    status = watcher.status()
    assert status["available"] is False
    assert status["error"]
    assert status["active"] == {}
    assert status["debounce_seconds"] == 5.0

    result = watcher.start_source({"id": "src-1", "root_path": str(tmp_path)})
    assert result == {"watching": False, "source_id": "src-1", "error": status["error"]}


def test_restore_without_a_graph_reports_nothing_restored(monkeypatch):
    monkeypatch.setitem(sys.modules, "watchdog.observers", None)
    watcher = LocalKnowledgeWatcher(lambda: None)

    assert watcher.restore_enabled_sources() == {"restored": 0, "available": False}


def test_restore_swallows_a_failing_graph(monkeypatch):
    class _Graph:
        @staticmethod
        def local_sources():
            raise RuntimeError("graph is locked")

    watcher = _watcher(monkeypatch, [], get_graph=lambda: _Graph())

    assert watcher.restore_enabled_sources() == {"restored": 0, "available": True}


# ── start_source ─────────────────────────────────────────────────────────────

def test_start_source_requires_an_id_and_a_real_folder(monkeypatch, tmp_path):
    watcher = _watcher(monkeypatch, [])

    assert watcher.start_source({"root_path": str(tmp_path)}) == {
        "watching": False, "error": "source_id and root_path are required",
    }
    missing = watcher.start_source({"id": "src-1", "root_path": str(tmp_path / "gone")})
    assert missing == {
        "watching": False, "source_id": "src-1", "error": "source folder is not available",
    }
    assert watcher.status()["active"] == {}


def test_start_source_refuses_when_the_handler_base_is_missing(monkeypatch, tmp_path):
    """Defensive guard: ``available`` says yes but the handler base is gone.

    Only reachable if the two cached watchdog classes ever disagree, which the
    subclass below simulates rather than leaving the branch untested.
    """
    class _HalfWiredWatcher(LocalKnowledgeWatcher):
        @property
        def available(self) -> bool:
            return True

    watcher = _watcher(monkeypatch, [])
    half = _HalfWiredWatcher(lambda: None, debounce_seconds=3600)
    half._event_handler_base = None

    result = half.start_source({"id": "src-1", "root_path": str(tmp_path)})

    assert result["watching"] is False
    assert result["source_id"] == "src-1"
    assert result["error"]
    assert watcher.status()["active"] == {}


def test_start_source_reports_an_observer_that_refuses_to_schedule(monkeypatch, tmp_path):
    observer = _FakeObserver(schedule_error=OSError("inotify limit reached"))
    watcher = _watcher(monkeypatch, [observer])

    result = watcher.start_source({"id": "src-1", "root_path": str(tmp_path)})

    assert result == {
        "watching": False, "source_id": "src-1", "error": "inotify limit reached",
    }
    assert observer.started is False
    assert watcher.status()["active"] == {}


def test_start_source_wires_the_handler_and_debounces_events(monkeypatch, tmp_path):
    first = _FakeObserver()
    second = _FakeObserver()
    watcher = _watcher(monkeypatch, [first, second])
    source = {"id": "src-1", "root_path": str(tmp_path), "consent": {"approved_by": "u@e.com"}}

    result = watcher.start_source(source)

    assert result == {"watching": True, "source_id": "src-1", "root_path": str(tmp_path.resolve())}
    assert first.started is True
    handler, watched_path, recursive = first.scheduled[0]
    assert watched_path == str(tmp_path.resolve())
    assert recursive is True
    assert isinstance(handler, _FakeHandlerBase)
    assert isinstance(handler, _LocalWatchHandler)
    assert handler.base_initialised is True

    # Directory events are ignored; file events arm the debounce timer.
    handler.on_any_event(SimpleNamespace(is_directory=True))
    assert watcher.status()["active"]["src-1"]["last_event_at"] is None

    handler.on_any_event(SimpleNamespace(is_directory=False))
    armed = watcher._watched["src-1"]["timer"]
    assert armed is not None and armed.is_alive()
    first_event_at = watcher.status()["active"]["src-1"]["last_event_at"]
    assert first_event_at is not None

    # A second event replaces the pending timer instead of stacking one.
    handler.on_any_event(SimpleNamespace(is_directory=False))
    rearmed = watcher._watched["src-1"]["timer"]
    assert rearmed is not armed
    assert armed.finished.is_set()  # the superseded timer was cancelled

    # Restarting the same source id swaps the observer and drops the old timer.
    restarted = watcher.start_source(source)
    assert restarted["watching"] is True
    assert first.stopped is True
    assert first.joined == 3
    assert rearmed.finished.is_set()
    assert second.started is True

    watcher.stop_all()
    assert watcher.status()["active"] == {}
    assert second.stopped is True


def test_schedule_ignores_an_unknown_source(monkeypatch):
    watcher = _watcher(monkeypatch, [])

    watcher._schedule("ghost")  # the debounce callback after a stop_source race

    assert watcher.status()["active"] == {}


# ── stop_source ──────────────────────────────────────────────────────────────

def test_stop_source_is_idempotent_for_unknown_ids(monkeypatch):
    watcher = _watcher(monkeypatch, [])

    assert watcher.stop_source("ghost") == {"stopped": False, "source_id": "ghost"}


def test_stop_source_tolerates_a_broken_or_missing_observer(monkeypatch, tmp_path):
    """A stop must always succeed: the watch entry is dropped even when the
    observer raises, and even when the entry somehow has no observer at all."""
    exploding = _FakeObserver(stop_error=RuntimeError("observer thread is wedged"))
    watcher = _watcher(monkeypatch, [exploding, _FakeObserver()])

    watcher.start_source({"id": "src-1", "root_path": str(tmp_path)})
    assert watcher.stop_source("src-1") == {"stopped": True, "source_id": "src-1"}
    assert watcher.status()["active"] == {}

    watcher.start_source({"id": "src-2", "root_path": str(tmp_path)})
    watcher._watched["src-2"]["observer"] = None  # corrupted entry
    assert watcher.stop_source("src-2") == {"stopped": True, "source_id": "src-2"}
    assert watcher.status()["active"] == {}


# ── _run_index (what the debounce timer eventually calls) ────────────────────

def test_run_index_ignores_unknown_sources_and_a_missing_graph(monkeypatch, tmp_path):
    watcher = _watcher(monkeypatch, [_FakeObserver()])
    watcher._run_index("ghost")  # stopped between the timer firing and the run

    watcher.start_source({"id": "src-1", "root_path": str(tmp_path)})
    watcher._run_index("src-1")  # get_graph() is None → nothing to reindex

    assert watcher.status()["active"]["src-1"]["last_indexed_at"] is None
    watcher.stop_all()


def test_run_index_reindexes_the_source_and_fires_the_ingest_hooks(monkeypatch, tmp_path):
    calls: List[Dict[str, Any]] = []

    class _Graph:
        @staticmethod
        def index_local_folder(root, **kwargs):
            calls.append({"root": root, **kwargs})
            return {"status": "ok", "counts": {"indexed": 2, "deleted": 1}}

    hooks = _Hooks()
    watcher = _watcher(monkeypatch, [_FakeObserver()], get_graph=lambda: _Graph(), hooks=hooks)
    watcher.start_source({
        "id": "src-1",
        "root_path": str(tmp_path),
        "include_ocr": True,
        "workspace_id": "org:acme",
        "consent": {"approved_by": "owner@example.com", "workspace_id": "personal"},
    })

    watcher._run_index("src-1")

    assert calls[0]["root"] == Path(str(tmp_path))
    assert calls[0]["include_ocr"] is True
    assert calls[0]["watch_enabled"] is True
    assert calls[0]["user_email"] == "owner@example.com"
    assert calls[0]["workspace_id"] == "org:acme"
    assert calls[0]["source_id_override"] == "src-1"

    active = watcher.status()["active"]["src-1"]
    assert active["last_indexed_at"] is not None
    assert active["last_error"] is None

    assert hooks.payloads("folder.reindex")[0]["trigger"] == "watch"
    assert hooks.payloads("folder.reindex")[1]["status"] == "ok"
    brain = [item for item in hooks.events if item[1] == "tool.kg_ingest.local_folder"]
    assert brain[0][0] == "post_tool"
    assert brain[0][2]["payload"]["source_type"] == "local_folder"
    assert brain[0][2]["user_email"] == "owner@example.com"
    assert brain[0][2]["workspace_id"] == "org:acme"
    watcher.stop_all()


def test_run_index_skips_the_ingest_event_when_nothing_changed(monkeypatch, tmp_path):
    class _Graph:
        @staticmethod
        def index_local_folder(*_args, **_kwargs):
            return {"status": "ok", "counts": {"indexed": 0, "deleted": 0}}

    hooks = _Hooks()
    watcher = _watcher(monkeypatch, [_FakeObserver()], get_graph=lambda: _Graph(), hooks=hooks)
    watcher.start_source({"id": "src-1", "root_path": str(tmp_path)})

    watcher._run_index("src-1")

    assert [item for item in hooks.events if item[1] == "tool.kg_ingest.local_folder"] == []
    assert hooks.payloads("folder.reindex")[1]["status"] == "ok"
    watcher.stop_all()


def test_run_index_records_a_failed_reindex_without_raising(monkeypatch, tmp_path):
    class _Graph:
        @staticmethod
        def index_local_folder(*_args, **_kwargs):
            raise ValueError("folder belongs to another workspace")

    hooks = _Hooks()
    watcher = _watcher(monkeypatch, [_FakeObserver()], get_graph=lambda: _Graph(), hooks=hooks)
    watcher.start_source({"id": "src-1", "root_path": str(tmp_path)})

    watcher._run_index("src-1")

    active = watcher.status()["active"]["src-1"]
    assert active["last_error"] == "folder belongs to another workspace"
    assert active["last_indexed_at"] is None
    failure = hooks.payloads("folder.reindex")[1]
    assert failure["status"] == "error"
    assert failure["error"] == "folder belongs to another workspace"
    watcher.stop_all()


def test_run_index_without_hooks_still_records_the_reindex(monkeypatch, tmp_path):
    class _Graph:
        @staticmethod
        def index_local_folder(*_args, **_kwargs):
            return {"status": "ok"}

    watcher = _watcher(monkeypatch, [_FakeObserver()], get_graph=lambda: _Graph())
    watcher.start_source({"id": "src-1", "root_path": str(tmp_path)})

    watcher._run_index("src-1")

    assert watcher.status()["active"]["src-1"]["last_indexed_at"] is not None
    watcher.stop_all()


def test_restore_enabled_sources_starts_only_the_opted_in_folders(monkeypatch, tmp_path):
    class _Graph:
        @staticmethod
        def local_sources():
            return {"sources": [
                {"id": "src-on", "root_path": str(tmp_path), "watch_enabled": True,
                 "consent": {"approved_by": "owner@example.com"}},
                {"id": "src-off", "root_path": str(tmp_path), "watch_enabled": False},
            ]}

    watcher = _watcher(monkeypatch, [_FakeObserver()], get_graph=lambda: _Graph())

    assert watcher.restore_enabled_sources() == {"restored": 1, "available": True}
    active = watcher.status()["active"]
    assert list(active) == ["src-on"]
    assert active["src-on"]["root_path"] == str(tmp_path)
    watcher.stop_all()


@pytest.mark.parametrize("is_directory", [True, False])
def test_local_watch_handler_forwards_only_file_events(is_directory):
    fired: List[int] = []
    handler = _LocalWatchHandler(lambda: fired.append(1))

    handler.on_any_event(SimpleNamespace(is_directory=is_directory))

    assert fired == ([] if is_directory else [1])
