"""Per-folder memory state (v9.9.7).

Review follow-up: "폴더/프로젝트 단위 '기억 상태' 대시보드 — 이 폴더는 몇 %
인덱싱됐고, 벡터는 신선한지, 최근 오류는 무엇인지". House rules verified here:
coverage is computed from real index rows, an empty folder reports ``None``
rather than "0% indexed", failures carry their stored reason, and vector
freshness is never claimed per folder (the index is global).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore


@pytest.fixture()
def store(tmp_path):
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _add_source(store, source_id="src-1", root="/tmp/docs", label="Docs"):
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_sources
              (id, root_path, os_type, drive_id, label, status, include_ocr,
               watch_enabled, consent_json, created_at, updated_at, last_scanned_at)
            VALUES (?, ?, 'darwin', NULL, ?, 'active', 0, 1, '{}',
                    '2026-07-01T00:00:00Z', '2026-07-27T00:00:00Z', '2026-07-27T00:00:00Z')
            """,
            (source_id, root, label),
        )


def _add_file(store, source_id, name, status, error=None, scanned="2026-07-27T00:00:00Z"):
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO local_file_index
              (id, source_id, os_type, drive_id, root_path, file_path, relative_path,
               file_name, extension, size_bytes, modified_at, sha256, last_scanned_at,
               last_indexed_at, parser_type, status, error_message, graph_node_id,
               deleted, metadata_json)
            VALUES (?, ?, 'darwin', NULL, '/tmp/docs', ?, ?, ?, '.md', 10,
                    NULL, NULL, ?, NULL, 'text', ?, ?, NULL, 0, '{}')
            """,
            (f"{source_id}:{name}", source_id, f"/tmp/docs/{name}", name, name, scanned, status, error),
        )


def test_coverage_is_computed_from_real_index_rows(store):
    _add_source(store)
    for i in range(7):
        _add_file(store, "src-1", f"ok{i}.md", "indexed")
    _add_file(store, "src-1", "bad.md", "failed", error="parser exploded")
    _add_file(store, "src-1", "big.md", "skipped", error="file too large")

    folder = store.local_source_health()["folders"][0]
    assert folder["files"] == {"total": 9, "indexed": 7, "failed": 1, "skipped": 1, "pending": 0}
    assert folder["coverage"] == pytest.approx(7 / 9, abs=1e-4)
    assert folder["label"] == "Docs"
    assert folder["watch_enabled"] is True


def test_an_empty_folder_reports_unknown_coverage_not_zero_percent(store):
    _add_source(store)
    folder = store.local_source_health()["folders"][0]
    assert folder["files"]["total"] == 0
    assert folder["coverage"] is None


def test_failures_carry_the_reason_they_actually_failed_with(store):
    _add_source(store)
    _add_file(store, "src-1", "a.md", "failed", error="parser exploded", scanned="2026-07-27T01:00:00Z")
    _add_file(store, "src-1", "b.md", "failed", error="permission denied", scanned="2026-07-27T02:00:00Z")
    _add_file(store, "src-1", "c.md", "indexed")

    folder = store.local_source_health()["folders"][0]
    reasons = [item["detail"] for item in folder["recent_errors"]]
    assert "permission denied" in reasons and "parser exploded" in reasons
    # Newest first, and clean files never appear as errors.
    assert folder["recent_errors"][0]["path"] == "b.md"
    assert all(item["path"] != "c.md" for item in folder["recent_errors"])


def test_error_samples_are_bounded(store):
    _add_source(store)
    for i in range(10):
        _add_file(store, "src-1", f"bad{i}.md", "failed", error=f"boom {i}")
    assert len(store.local_source_health(error_samples=2)["folders"][0]["recent_errors"]) == 2
    assert len(store.local_source_health(error_samples=0)["folders"][0]["recent_errors"]) == 0
    # Garbage clamps to the default rather than exploding.
    assert store.local_source_health(error_samples="lots")["folders"][0]["recent_errors"]


def test_multiple_folders_do_not_mix_their_counts_or_errors(store):
    _add_source(store, "src-1", "/tmp/a", "A")
    _add_source(store, "src-2", "/tmp/b", "B")
    _add_file(store, "src-1", "a.md", "indexed")
    _add_file(store, "src-2", "b.md", "failed", error="b failed")

    folders = {f["id"]: f for f in store.local_source_health()["folders"]}
    assert folders["src-1"]["files"]["indexed"] == 1
    assert folders["src-1"]["recent_errors"] == []
    assert folders["src-2"]["recent_errors"][0]["detail"] == "b failed"


def test_no_connected_folders_is_an_empty_dashboard_not_an_error(store):
    payload = store.local_source_health()
    assert payload == {"folders": [], "count": 0}
