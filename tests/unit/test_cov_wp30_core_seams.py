"""wp30 coverage — Brain Core facade, memory records, quiet(), sensitivity.

The small seams around the package boundary: the lazy ``__getattr__`` that
must refuse unknown names, the two ``StorageUnavailable`` refusals
:class:`BrainCore` makes before it touches a database, the Memory System's
title/simulation guards, the suppressed-exception recorder, and the
never-leaves path classifier.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain
from lattice_brain.core import BrainCore, BrainCoreConfig
from lattice_brain.memory import BrainMemory
from lattice_brain.quiet import quiet
from lattice_brain.sensitivity import (
    LOCAL_ONLY_FLAG,
    LOCAL_ONLY_REASON,
    sensitive_reason_for_path,
    stamp_sensitivity,
)
from lattice_brain.storage import StorageCapabilities, StorageUnavailable


class _StubEngine:
    """Duck-typed StorageEngine: BrainCore only reads ``capabilities()``."""

    def __init__(self, caps: StorageCapabilities) -> None:
        self._caps = caps

    def capabilities(self) -> StorageCapabilities:
        return self._caps


class _StubPipeline:
    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.calls: list = []

    def available(self) -> bool:
        return self._available

    def ingest(self, item, *, user_email=None):
        self.calls.append((item, user_email))
        return _StubResult(item)


class _StubResult:
    def __init__(self, item) -> None:
        self.item = item

    def as_dict(self):
        return {"status": "ok", "source_type": self.item.source_type,
                "title": self.item.title, "metadata": self.item.metadata}


# ── lattice_brain.__getattr__ ────────────────────────────────────────────────

def test_package_getattr_serves_lazy_names_and_refuses_unknown():
    assert lattice_brain.ContextAssembler.__name__ == "ContextAssembler"
    with pytest.raises(AttributeError, match="no_such_symbol"):
        lattice_brain.no_such_symbol  # noqa: B018 — attribute access is the assertion


# ── BrainCore storage refusals ───────────────────────────────────────────────

def test_brain_core_refuses_unavailable_storage(tmp_path):
    engine = _StubEngine(
        StorageCapabilities(engine="postgres", available=False, reason="socket refused")
    )
    with pytest.raises(StorageUnavailable, match="socket refused"):
        BrainCore(BrainCoreConfig(data_dir=tmp_path, storage_engine=engine))


def test_brain_core_refuses_unavailable_storage_without_reason(tmp_path):
    engine = _StubEngine(StorageCapabilities(engine="postgres", available=False))
    with pytest.raises(StorageUnavailable, match="postgres storage is unavailable"):
        BrainCore(BrainCoreConfig(data_dir=tmp_path, storage_engine=engine))


def test_brain_core_refuses_non_sqlite_engine_without_silent_fallback(tmp_path):
    engine = _StubEngine(StorageCapabilities(engine="postgres", available=True))
    with pytest.raises(StorageUnavailable, match="requires SQLiteEngine"):
        BrainCore(BrainCoreConfig(data_dir=tmp_path, storage_engine=engine))
    # No SQLite file was created as a fallback.
    assert not (tmp_path / "knowledge_graph.sqlite").exists()


# ── BrainMemory ──────────────────────────────────────────────────────────────

def test_brain_memory_available_follows_pipeline():
    assert BrainMemory(None).available() is False
    assert BrainMemory(_StubPipeline(available=False)).available() is False
    assert BrainMemory(_StubPipeline(available=True)).available() is True


def test_brain_memory_requires_titles():
    memory = BrainMemory(_StubPipeline())
    with pytest.raises(ValueError, match="decision needs a title"):
        memory.record_decision("   ")
    with pytest.raises(ValueError, match="experience needs a title"):
        memory.record_experience("", detail="ran something")


def test_brain_memory_rejects_simulation_before_the_title_check():
    memory = BrainMemory(_StubPipeline())
    rejected = memory.record_experience("", run={"id": "r1", "mode": "simulation"})
    assert rejected["status"] == "rejected"
    real = memory.record_experience("shipped", run={"id": "r1", "mode": "live"})
    assert real["status"] == "ok"
    assert real["metadata"]["run_id"] == "r1"


# ── quiet() ──────────────────────────────────────────────────────────────────

def test_quiet_without_an_active_exception_is_a_no_op(caplog):
    with caplog.at_level(logging.DEBUG, logger="lattice_brain.suppressed"):
        quiet("nothing raised")
    assert caplog.records == []


def test_quiet_records_the_handled_exception_with_location(caplog):
    with caplog.at_level(logging.DEBUG, logger="lattice_brain.suppressed"):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            quiet("deliberate")
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "suppressed RuntimeError" in message
    assert "(deliberate)" in message
    assert "test_cov_wp30_core_seams.py" in message


# ── sensitivity ──────────────────────────────────────────────────────────────

def test_sensitive_reason_for_path_variants():
    assert sensitive_reason_for_path("") is None
    assert sensitive_reason_for_path(None) is None
    assert sensitive_reason_for_path("/home/u/notes/plan.md") is None
    assert sensitive_reason_for_path("/home/u/.env") == "'.env' is a secret-bearing filename"
    assert sensitive_reason_for_path(r"C:\Users\u\.ssh\known_hosts") == "path contains '/.ssh/'"


def test_stamp_sensitivity_never_downgrades_an_existing_flag():
    metadata = {}
    assert stamp_sensitivity(metadata, "/home/u/notes/plan.md") is None
    assert metadata == {}

    reason = stamp_sensitivity(metadata, "/home/u/.ssh/id_rsa")
    assert reason == "path contains '/.ssh/'"
    assert metadata[LOCAL_ONLY_FLAG] is True

    metadata[LOCAL_ONLY_REASON] = "user marked it private"
    stamp_sensitivity(metadata, "/home/u/.aws/credentials")
    assert metadata[LOCAL_ONLY_REASON] == "user marked it private"
