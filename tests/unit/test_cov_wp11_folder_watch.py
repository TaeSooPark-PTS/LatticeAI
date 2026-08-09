"""wp11: folder-watch filters, scan refusals and the poller loop itself.

``tests/unit/test_folder_watch.py`` covers the opt-in lifecycle end to end.
What never ran is everything that only happens when the disk misbehaves or the
user reconfigures mid-flight: a bad interval env var, enabling a path that is
not a folder, re-enabling an existing watch, disabling by path, scanning a
watch that is unknown/disabled/vanished, the ``.latticeignore`` + skip-dir +
size + unreadable-file branches of the snapshot walk, the per-scan cap, and
the polling loop.

The poller is driven synchronously: ``_poll_loop`` reads its stop signal from
``self._stop``, so a scripted stand-in whose ``wait()`` returns ``False`` once
and ``True`` afterwards runs exactly one pass in the test thread — no sleeps,
no wall-clock dependence, no background thread to join.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from latticeai.services import folder_watch as folder_watch_module
from latticeai.services.folder_watch import (
    DEFAULT_WATCH_INTERVAL_SECONDS,
    WATCH_INTERVAL_ENV,
    FolderWatchService,
)


class _RecordingPipeline:
    """Stands in for :class:`IngestionPipeline`: records every ingested item."""

    def __init__(self, *, duplicate: bool = False, status: str = "ok",
                 detail: Optional[str] = None, error: Optional[Exception] = None,
                 on_ingest=None) -> None:
        self.items: List[Any] = []
        self._duplicate = duplicate
        self._status = status
        self._detail = detail
        self._error = error
        self._on_ingest = on_ingest

    def available(self) -> bool:
        return True

    def ingest(self, item, user_email=None):
        self.items.append(item)
        if self._on_ingest is not None:
            self._on_ingest(item)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            status=self._status, duplicate=self._duplicate, detail=self._detail,
        )

    @property
    def relative_paths(self) -> List[str]:
        return [item.metadata["relative_path"] for item in self.items]


class _ScriptedStop:
    """Deterministic stand-in for the poller's ``threading.Event``.

    ``wait()`` replays the scripted answers (``False`` → run one pass, then
    ``True`` → leave the loop), so ``_poll_loop`` executes synchronously.
    """

    def __init__(self, waits: List[bool], *, already_set: bool = False) -> None:
        self._waits = list(waits)
        self._already_set = already_set
        self.wait_calls: List[Any] = []

    def wait(self, timeout=None) -> bool:
        self.wait_calls.append(timeout)
        return self._waits.pop(0) if self._waits else True

    def is_set(self) -> bool:
        return self._already_set

    def set(self) -> None:
        self._already_set = True


def _service(tmp_path: Path, pipeline: Any) -> FolderWatchService:
    return FolderWatchService(
        pipeline=pipeline,
        config_path=tmp_path / "state" / "folder_watch.json",
        interval_seconds=3600,  # scans are driven explicitly by the tests
    )


def _corpus(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("첫 노트", encoding="utf-8")


def _stored_config(tmp_path: Path, watch: Dict[str, Any]) -> Path:
    """Persist a watch record the way a previous process would have."""
    config_path = tmp_path / "state" / "folder_watch.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"watches": {watch["id"]: watch}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path


# ── configuration ────────────────────────────────────────────────────────────

def test_interval_env_falls_back_when_unparsable(monkeypatch):
    monkeypatch.setenv(WATCH_INTERVAL_ENV, "every-so-often")
    assert folder_watch_module._default_interval() == DEFAULT_WATCH_INTERVAL_SECONDS

    monkeypatch.setenv(WATCH_INTERVAL_ENV, "0.05")
    assert folder_watch_module._default_interval() == 1.0  # floor, never a busy loop


# ── enable / disable ─────────────────────────────────────────────────────────

def test_enable_refuses_a_path_that_is_not_a_folder(tmp_path):
    service = _service(tmp_path, _RecordingPipeline())
    target = tmp_path / "notes.txt"
    target.write_text("not a folder", encoding="utf-8")

    result = service.enable(target)

    assert result["status"] == "failed"
    assert "not a directory" in result["detail"]
    assert service.status()["watches"] == []


def test_re_enabling_the_same_scope_refreshes_instead_of_duplicating(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    service = _service(tmp_path, _RecordingPipeline())
    first = service.enable(root, owner="user@example.com", workspace_id="org:acme")
    assert first["already_watching"] is False

    (root / "second.md").write_text("두 번째", encoding="utf-8")
    again = service.enable(root, owner="user@example.com", workspace_id="org:acme")

    assert again["already_watching"] is True
    assert again["watch"]["id"] == first["watch"]["id"]
    # Re-enabling re-snapshots, so the file added meanwhile is baseline, not new.
    assert again["watch"]["tracked_files"] == 2
    assert service.scan_once(first["watch"]["id"])["new"] == 0

    # A different workspace scope over the same folder is a separate consent.
    other = service.enable(root, owner="user@example.com", workspace_id="personal")
    assert other["already_watching"] is False
    assert other["watch"]["id"] != first["watch"]["id"]
    assert service.status()["enabled_count"] == 2
    service.stop_all()


def test_disable_accepts_a_path_instead_of_an_id(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    service = _service(tmp_path, _RecordingPipeline())
    watch = service.enable(root, owner="user@example.com")["watch"]

    result = service.disable(path=str(root))

    assert result["status"] == "ok"
    assert result["watch"]["id"] == watch["id"]
    assert service.status()["watches"] == []
    assert service.disable(path=str(root)) == {"status": "not_found"}
    service.stop_all()


# ── scan refusals ────────────────────────────────────────────────────────────

def test_scan_once_reports_unknown_and_disabled_watches(tmp_path):
    stored = {
        "id": "watch_stored", "path": str(tmp_path / "corpus"), "owner": None,
        "workspace_id": None, "recursive": True, "enabled": False,
        "created_at": "2026-01-01T00:00:00+00:00", "last_scan_at": None,
        "last_result": None, "snapshot": {},
    }
    _stored_config(tmp_path, stored)
    service = _service(tmp_path, _RecordingPipeline())

    assert service.scan_once("nope") == {"status": "not_found", "watch_id": "nope"}
    assert service.scan_once("watch_stored") == {
        "status": "disabled", "watch_id": "watch_stored",
    }
    # A stored-but-disabled record never resumes polling either.
    assert service.restore() == {"restored": 0, "polling": False}


def test_scan_reports_a_folder_that_disappeared_and_keeps_the_snapshot(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    service = _service(tmp_path, _RecordingPipeline())
    watch = service.enable(root, owner="user@example.com")["watch"]
    assert watch["tracked_files"] == 1

    (root / "notes.txt").unlink()
    root.rmdir()
    result = service.scan_once(watch["id"])

    assert result["status"] == "failed"
    assert "folder unavailable" in result["detail"]
    public = service.status()["watches"][0]
    assert public["last_result"]["status"] == "failed"
    # snapshot=None on the failure path → the baseline survives the outage.
    assert public["tracked_files"] == 1
    service.stop_all()


def test_scan_counts_duplicates_and_routes_pdfs_without_reading_them(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _RecordingPipeline(duplicate=True)
    service = _service(tmp_path, pipeline)
    watch = service.enable(root, owner="user@example.com")["watch"]

    (root / "report.pdf").write_bytes(b"%PDF-1.4\n")
    result = service.scan_once(watch["id"])

    assert result["new"] == 1
    assert result["duplicate"] == 1
    assert result["ingested"] == 0
    item = pipeline.items[0]
    assert item.source_type == "pdf"
    assert "extracted" not in item.metadata  # extraction is the pipeline's job
    assert item.metadata["watch_id"] == watch["id"]
    service.stop_all()


def test_scan_records_an_unreadable_file_as_a_scan_error(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _RecordingPipeline()
    service = _service(tmp_path, pipeline)
    watch = service.enable(root, owner="user@example.com")["watch"]

    (root / "broken.md").write_text("나중에 읽히지 않는다", encoding="utf-8")
    real_read_text = Path.read_text

    def broken(self, *args, **kwargs):
        if self.name == "broken.md":
            raise OSError("input/output error")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken)
    result = service.scan_once(watch["id"])

    assert result["new"] == 1
    assert result["failed"] == 1
    assert result["ingested"] == 0
    assert pipeline.items == []
    assert result["errors"][0]["path"] == "broken.md"
    assert "read failed" in result["errors"][0]["detail"]
    service.stop_all()


def test_scan_stops_at_the_per_scan_cap_and_reports_truncation(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    root.mkdir()
    pipeline = _RecordingPipeline()
    service = _service(tmp_path, pipeline)
    watch = service.enable(root, owner="user@example.com")["watch"]

    monkeypatch.setattr(folder_watch_module, "MAX_FILES_PER_SCAN", 1)
    (root / "a.md").write_text("첫 번째", encoding="utf-8")
    (root / "b.md").write_text("두 번째", encoding="utf-8")
    result = service.scan_once(watch["id"])

    assert result["new"] == 2
    assert result["truncated"] is True
    assert result["ingested"] == 1
    assert len(pipeline.items) == 1
    service.stop_all()


def test_scan_survives_the_watch_being_disabled_mid_scan(tmp_path):
    """A user may opt out while a scan is in flight; the result is still
    returned and nothing is written back onto the removed consent record."""
    root = tmp_path / "corpus"
    root.mkdir()
    holder: Dict[str, Any] = {}

    def disable_mid_scan(_item):
        holder["service"].disable(watch_id=holder["watch_id"])

    pipeline = _RecordingPipeline(on_ingest=disable_mid_scan)
    service = _service(tmp_path, pipeline)
    holder["service"] = service
    watch = service.enable(root, owner="user@example.com")["watch"]
    holder["watch_id"] = watch["id"]

    (root / "note.md").write_text("한 개", encoding="utf-8")
    result = service.scan_once(watch["id"])

    assert result["ingested"] == 1
    assert service.status()["watches"] == []
    stored = json.loads((tmp_path / "state" / "folder_watch.json").read_text(encoding="utf-8"))
    assert stored["watches"] == {}
    service.stop_all()


# ── snapshot filters ─────────────────────────────────────────────────────────

def test_snapshot_applies_skip_dirs_ignore_rules_and_the_size_cap(tmp_path, monkeypatch):
    root = tmp_path / "corpus"
    (root / "keep").mkdir(parents=True)
    (root / "drafts").mkdir()
    (root / "node_modules").mkdir()
    (root / ".hidden").mkdir()

    (root / ".latticeignore").write_text("drafts/\nsecret.txt\n", encoding="utf-8")
    (root / "keep" / "kept.md").write_text("보관", encoding="utf-8")
    (root / "drafts" / "draft.md").write_text("초안", encoding="utf-8")
    (root / "node_modules" / "dep.js").write_text("dep", encoding="utf-8")
    (root / ".hidden" / "hidden.md").write_text("숨김", encoding="utf-8")
    (root / ".dotfile.md").write_text("점파일", encoding="utf-8")
    (root / "secret.txt").write_text("비밀", encoding="utf-8")
    (root / "notes.log").write_text("확장자 제외", encoding="utf-8")
    (root / "huge.md").write_text("x" * 400, encoding="utf-8")
    (root / "unreadable.md").write_text("stat 실패", encoding="utf-8")
    (root / "notes.md").write_text("정상", encoding="utf-8")

    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self.name == "unreadable.md":
            raise OSError("stat failed")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    monkeypatch.setattr(folder_watch_module, "DEFAULT_MAX_FILE_BYTES", 64)

    pipeline = _RecordingPipeline()
    service = _service(tmp_path, pipeline)
    watch = service.enable(root, owner="user@example.com")["watch"]

    # Only the two survivors of the filter chain are tracked.
    assert watch["tracked_files"] == 2

    for name in ("keep/kept.md", "notes.md", "drafts/draft.md", "secret.txt",
                 ".dotfile.md"):
        (root / name).write_text("변경됨", encoding="utf-8")
    (root / "huge.md").write_text("y" * 400, encoding="utf-8")  # still over the cap
    result = service.scan_once(watch["id"])

    assert sorted(pipeline.relative_paths) == ["keep/kept.md", "notes.md"]
    assert result["changed"] == 2
    assert result["new"] == 0
    service.stop_all()


# ── poller ───────────────────────────────────────────────────────────────────

def test_enable_reuses_the_running_poller_thread(tmp_path):
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    _corpus(first_root)
    _corpus(second_root)
    service = _service(tmp_path, _RecordingPipeline())

    service.enable(first_root, owner="user@example.com")
    thread = service._thread
    assert thread is not None and thread.is_alive()

    service.enable(second_root, owner="user@example.com")

    assert service._thread is thread  # one poller, not one per watch
    assert service.status()["polling"] is True
    service.stop_all()
    assert service.status()["polling"] is False


def test_poll_loop_exits_when_no_watch_is_enabled(tmp_path):
    service = _service(tmp_path, _RecordingPipeline())
    stop = _ScriptedStop([False])
    service._stop = stop

    service._poll_loop()

    assert stop.wait_calls == [3600]  # waited the configured interval, once


def test_poll_loop_leaves_immediately_once_stopped(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _RecordingPipeline()
    service = _service(tmp_path, pipeline)
    service.enable(root, owner="user@example.com")
    service.stop_all()
    (root / "new.md").write_text("스캔되면 안 된다", encoding="utf-8")

    service._stop = _ScriptedStop([False], already_set=True)
    service._poll_loop()

    assert pipeline.items == []  # the stop flag wins over the pending work


def test_poll_loop_survives_a_failing_watch_and_keeps_going(tmp_path):
    root = tmp_path / "corpus"
    _corpus(root)
    pipeline = _RecordingPipeline(error=RuntimeError("pipeline exploded"))
    service = _service(tmp_path, pipeline)
    service.enable(root, owner="user@example.com")
    service.stop_all()
    (root / "new.md").write_text("한 번은 시도된다", encoding="utf-8")

    stop = _ScriptedStop([False, False])
    service._stop = stop
    service._poll_loop()

    # Two passes ran; the raising scan never killed the poller.
    assert len(pipeline.items) == 2
    assert stop.wait_calls == [3600, 3600, 3600]
    assert service.status()["watches"][0]["last_scan_at"] is None
