from __future__ import annotations

import pytest

from latticeai.services.memory_service import MemoryService, MemoryServiceError


class BrokenMemoryStore:
    def list_memories(self, **_kwargs):
        raise OSError("store unavailable")


def test_memory_manager_does_not_report_backend_failure_as_empty_brain(tmp_path):
    service = MemoryService(
        store=BrokenMemoryStore(),
        data_dir=tmp_path,
        knowledge_graph=None,
        enable_graph=False,
    )

    with pytest.raises(MemoryServiceError, match="backend unavailable"):
        service.manager(user_email="user@example.com", workspace_id="personal")


class BrokenSearchStore:
    def search_memories(self, *_args, **_kwargs):
        raise RuntimeError("search offline")


def test_recall_surfaces_degraded_source_instead_of_quiet_success(tmp_path):
    service = MemoryService(
        store=BrokenSearchStore(),
        data_dir=tmp_path,
        knowledge_graph=None,
        enable_graph=False,
    )

    result = service.recall("hello", workspace_id="personal")

    assert result["status"] == "degraded"
    assert result["errors"][0]["source"] == "workspace"
