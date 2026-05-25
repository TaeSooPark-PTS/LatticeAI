"""Local folder Graph RAG tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from knowledge_graph import KnowledgeGraphStore


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def test_audit_local_folder_classifies_supported_sensitive_and_unsupported(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "notes.md").write_text("Graph RAG local folder scan", encoding="utf-8")
    (root / "app.py").write_text("def run():\n    return 'Lattice AI'\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (root / "payload.bin").write_bytes(b"\x00\x01")

    audit = _store(tmp_path).audit_local_folder(root)

    assert audit["summary"]["total_files"] == 4
    assert audit["summary"]["readable_files"] == 2
    assert audit["summary"]["sensitive_files"] == 1
    assert audit["summary"]["unsupported_files"] == 1
    assert audit["by_category"]["text"] == 1
    assert audit["by_category"]["code"] == 1


def test_index_local_folder_creates_graph_and_skips_unchanged_files(tmp_path):
    root = tmp_path / "source"
    src = root / "src"
    src.mkdir(parents=True)
    (root / "notes.md").write_text(
        "Lattice AI uses Graph RAG. TODO implement local folder scan.",
        encoding="utf-8",
    )
    (src / "app.py").write_text("def graph_rag():\n    return 'local knowledge'\n", encoding="utf-8")
    excluded = root / "node_modules" / "pkg"
    excluded.mkdir(parents=True)
    (excluded / "index.js").write_text("console.log('skip')", encoding="utf-8")

    store = _store(tmp_path)
    first = store.index_local_folder(root, user_email="user@example.com")
    second = store.index_local_folder(root, user_email="user@example.com")

    assert first["counts"]["indexed"] == 2
    assert second["counts"]["skipped_unchanged"] == 2

    stats = store.stats()
    assert stats["local_sources"] == 1
    assert stats["local_file_status"]["indexed"] == 2
    assert stats["nodes"]["Folder"] >= 2
    assert stats["nodes"]["Document"] >= 1
    assert stats["nodes"]["CodeFile"] >= 1

    matches = store.search("Graph RAG")["matches"]
    assert any(match["title"] == "notes.md" for match in matches)


def test_index_local_folder_marks_missing_files_deleted(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    target = root / "notes.md"
    target.write_text("Lattice AI Graph RAG", encoding="utf-8")

    store = _store(tmp_path)
    store.index_local_folder(root, user_email="user@example.com")
    target.unlink()
    result = store.index_local_folder(root, user_email="user@example.com")

    assert result["counts"]["deleted"] == 1
    assert store.stats()["local_file_status"]["deleted"] == 1


def test_set_local_source_watch_updates_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "notes.md").write_text("Lattice AI Graph RAG", encoding="utf-8")

    store = _store(tmp_path)
    source_id = store.index_local_folder(root, user_email="user@example.com")["source"]["id"]
    store.set_local_source_watch(source_id, True)

    source = store.local_sources()["sources"][0]
    assert source["id"] == source_id
    assert source["watch_enabled"] is True
