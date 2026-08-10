"""wp16 coverage — ``lattice_brain.graph.discovery`` (the metadata-only pass).

Everything here happens *before* consent: listing safe starting points, showing
one folder level, walking a tree, and auditing what would be readable. No file
body is ever opened, so the tests assert the shape of those answers against
real ``tmp_path`` trees. Three things a test process cannot own are patched at
their seam: the reported OS, whether a well-known mount point exists, and a
syscall that fails (``PermissionError`` / ``OSError``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph import _kg_fsutil as fsutil
from lattice_brain.graph import discovery as discovery_module
from lattice_brain.graph.store import KnowledgeGraphStore


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _force_os(monkeypatch, os_type: str) -> None:
    monkeypatch.setattr(discovery_module, "_current_os_type", lambda: os_type)


def _redirect_dirs(monkeypatch, mapping, *, iterdir_error=None) -> None:
    """Make fixed absolute paths (``/Volumes``, ``/mnt``, ``D:\\``) resolvable.

    The scanner asks the OS about mount points that exist on one platform and
    not another; mapping them onto real temp directories keeps the behaviour
    under test identical on macOS and ubuntu.
    """
    real_exists = Path.exists
    real_iterdir = Path.iterdir

    def fake_exists(self, *args, **kwargs):
        if str(self) in mapping:
            return True
        return real_exists(self, *args, **kwargs)

    def fake_iterdir(self):
        key = str(self)
        if key in mapping and mapping[key] is not None:
            if iterdir_error is not None:
                raise iterdir_error
            return real_iterdir(mapping[key])
        return real_iterdir(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "iterdir", fake_iterdir)


def _install_path_faults(
    monkeypatch, *, stat_fail=None, iterdir_fail=None, not_regular=()
) -> None:
    """Make named entries fail the way an unreadable filesystem entry fails.

    ``is_dir``/``is_file`` are answered without touching the fault so that
    directory sorting still works — only the explicit ``stat()``/``iterdir()``
    call the product code makes raises.
    """
    stat_fail = stat_fail or {}
    iterdir_fail = iterdir_fail or {}
    real_stat = Path.stat
    real_is_dir = Path.is_dir
    real_is_file = Path.is_file
    real_iterdir = Path.iterdir

    def fake_stat(self, *args, **kwargs):
        exc = stat_fail.get(self.name)
        if exc is not None:
            raise exc
        return real_stat(self, *args, **kwargs)

    def fake_is_dir(self, *args, **kwargs):
        if self.name in stat_fail:
            return False
        return real_is_dir(self, *args, **kwargs)

    def fake_is_file(self, *args, **kwargs):
        if self.name in not_regular:
            return False
        if self.name in stat_fail:
            return True
        return real_is_file(self, *args, **kwargs)

    def fake_iterdir(self):
        exc = iterdir_fail.get(self.name)
        if exc is not None:
            raise exc
        return real_iterdir(self)

    def fake_is_symlink(self, *args, **kwargs):
        # 3.11/3.12 route is_symlink() through Path.stat(follow_symlinks=False),
        # so the injected fault would leak out of the product's own stat() call;
        # 3.13+ swallow every OSError here. Answer False for faulted names on
        # every version so only the explicit stat() the product makes raises.
        if self.name in stat_fail:
            return False
        return real_is_symlink(self, *args, **kwargs)

    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    monkeypatch.setattr(Path, "is_file", fake_is_file)
    monkeypatch.setattr(Path, "iterdir", fake_iterdir)


# ── discover_local_roots ─────────────────────────────────────────────────────


def test_discover_local_roots_skips_duplicates_missing_and_unresolvable(
    monkeypatch, tmp_path: Path, store
) -> None:
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Desktop").symlink_to(home / "Documents", target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    _force_os(monkeypatch, "haiku")  # none of the three OS branches

    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        if self.name == "Pictures":
            raise OSError("resolve failed")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    payload = store.discover_local_roots()

    assert payload["os_type"] == "haiku"
    assert payload["computer"]
    assert payload["privacy_notice"]
    # 홈 + Documents only: Desktop resolves onto Documents (already seen),
    # Downloads/Projects do not exist, Pictures cannot be resolved at all.
    assert [root["kind"] for root in payload["roots"]] == ["home", "documents"]
    assert payload["roots"][0]["path"] == str(home.resolve())
    assert payload["roots"][1]["label"] == "문서"


def test_discover_local_roots_lists_macos_volumes(
    monkeypatch, tmp_path: Path, store
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    volumes = tmp_path / "volumes"
    (volumes / "Backup").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _force_os(monkeypatch, "macos")
    _redirect_dirs(monkeypatch, {"/Volumes": volumes})

    payload = store.discover_local_roots()

    volume_roots = [root for root in payload["roots"] if root["kind"] == "volume"]
    assert [root["label"] for root in volume_roots] == ["Backup"]
    assert volume_roots[0]["recommended"] is False


def test_discover_local_roots_survives_unreadable_volumes(
    monkeypatch, tmp_path: Path, store
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _force_os(monkeypatch, "macos")
    _redirect_dirs(
        monkeypatch,
        {"/Volumes": tmp_path / "volumes"},
        iterdir_error=OSError("volume listing failed"),
    )

    payload = store.discover_local_roots()

    assert [root["kind"] for root in payload["roots"]] == ["home"]


def test_discover_local_roots_lists_windows_drives_and_cloud_folders(
    monkeypatch, tmp_path: Path, store
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cloud = tmp_path / "OneDrive"
    cloud.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OneDrive", str(cloud))
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    _force_os(monkeypatch, "windows")
    drive = Path("D:\\")
    _redirect_dirs(monkeypatch, {str(drive): None, str(drive.resolve()): None})

    payload = store.discover_local_roots()

    kinds = {root["kind"]: root for root in payload["roots"]}
    assert kinds["drive"]["label"] == "D: 드라이브"
    assert kinds["drive"]["recommended"] is True
    assert kinds["cloud"]["path"] == str(cloud.resolve())


def test_discover_local_roots_lists_linux_mounts(
    monkeypatch, tmp_path: Path, store
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    mnt = tmp_path / "mnt"
    (mnt / "usb-stick").mkdir(parents=True)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _force_os(monkeypatch, "linux")
    _redirect_dirs(monkeypatch, {"/mnt": mnt, "/media": media})

    payload = store.discover_local_roots()

    labels = [root["label"] for root in payload["roots"]]
    assert "/mnt" in labels and "/media" in labels
    assert "usb-stick" in labels


def test_discover_local_roots_survives_unreadable_mounts(
    monkeypatch, tmp_path: Path, store
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _force_os(monkeypatch, "linux")
    _redirect_dirs(
        monkeypatch,
        {"/mnt": tmp_path / "mnt", "/media": tmp_path / "media"},
        iterdir_error=PermissionError("mount listing denied"),
    )

    payload = store.discover_local_roots()

    assert [root["kind"] for root in payload["roots"]] == ["home", "mounts", "mounts"]


# ── preview_local_tree ───────────────────────────────────────────────────────


def test_preview_local_tree_rejects_missing_paths(tmp_path: Path, store) -> None:
    with pytest.raises(ValueError, match="경로가 존재하지 않습니다"):
        store.preview_local_tree(tmp_path / "nope")


def test_preview_local_tree_rejects_files(tmp_path: Path, store) -> None:
    target = tmp_path / "note.md"
    target.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="폴더가 아닙니다"):
        store.preview_local_tree(target)


def test_preview_local_tree_lists_one_level_with_metadata(
    tmp_path: Path, store
) -> None:
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / "notes.md").write_text("hello", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")

    payload = store.preview_local_tree(root, max_items=2)

    by_name = {item["name"]: item for item in payload["items"]}
    assert payload["truncated"] is True
    assert len(payload["items"]) == 2
    assert by_name[".git"]["type"] == "directory"
    assert by_name[".git"]["excluded_reason"] == "excluded_folder"
    assert by_name[".git"]["hidden"] is True
    assert payload["inaccessible"] == 0
    assert payload["privacy_notice"]

    full = store.preview_local_tree(root)
    entries = {item["name"]: item for item in full["items"]}
    assert entries["notes.md"]["size_bytes"] == 5
    assert entries["notes.md"]["extension"] == ".md"
    assert entries["notes.md"]["modified_at"]
    assert entries[".env"]["excluded_reason"] == "sensitive_or_excluded_file"


def test_preview_local_tree_reports_denied_listing(
    monkeypatch, tmp_path: Path, store
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    _install_path_faults(
        monkeypatch, iterdir_fail={"vault": PermissionError("nope")}
    )

    payload = store.preview_local_tree(root)

    assert payload["items"] == []
    assert "접근 권한 없음" in payload["error"]


def test_preview_local_tree_marks_unreadable_entries(
    monkeypatch, tmp_path: Path, store
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "locked.txt").write_text("x", encoding="utf-8")
    (root / "dangling.md").symlink_to(root / "missing-target.md")
    _install_path_faults(
        monkeypatch, stat_fail={"locked.txt": PermissionError("denied")}
    )

    payload = store.preview_local_tree(root)

    by_name = {item["name"]: item for item in payload["items"]}
    assert payload["inaccessible"] == 2
    assert by_name["locked.txt"]["accessible"] is False
    assert by_name["locked.txt"]["excluded_reason"] == "permission_denied"
    assert by_name["dangling.md"]["accessible"] is False
    assert by_name["dangling.md"]["type"] == "unknown"
    assert "permission_denied" not in by_name["dangling.md"]["excluded_reason"]


# ── _iter_local_scan_entries ─────────────────────────────────────────────────


def test_scan_entries_report_unreadable_directories(
    monkeypatch, tmp_path: Path, store
) -> None:
    root = tmp_path / "root"
    (root / "denied").mkdir(parents=True)
    (root / "broken").mkdir()
    _install_path_faults(
        monkeypatch,
        iterdir_fail={
            "denied": PermissionError("denied"),
            "broken": OSError("io error"),
        },
    )

    entries = list(store._iter_local_scan_entries(root, max_files=10))

    reasons = {entry["path"].name: entry["reason"] for entry in entries}
    assert {entry["kind"] for entry in entries} == {"inaccessible_dir"}
    assert reasons["denied"].startswith("permission_denied: ")
    assert reasons["broken"] == "io error"


def test_scan_entries_exclude_symlinks_and_irregular_files(
    monkeypatch, tmp_path: Path, store
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "real.txt").write_text("x", encoding="utf-8")
    (root / "alias.txt").symlink_to(root / "real.txt")
    (root / "socket.txt").write_text("x", encoding="utf-8")
    _install_path_faults(monkeypatch, not_regular={"socket.txt"})

    entries = {
        entry["path"].name: entry
        for entry in store._iter_local_scan_entries(root, max_files=10)
    }

    assert entries["alias.txt"]["kind"] == "excluded"
    assert entries["alias.txt"]["reason"] == "symlink"
    assert entries["socket.txt"]["kind"] == "excluded"
    assert entries["socket.txt"]["reason"] == "not_regular_file"
    assert entries["real.txt"]["kind"] == "file"


def test_scan_entries_report_unreadable_files(
    monkeypatch, tmp_path: Path, store
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "locked.txt").write_text("x", encoding="utf-8")
    (root / "gone.txt").write_text("x", encoding="utf-8")
    _install_path_faults(
        monkeypatch,
        stat_fail={
            "locked.txt": PermissionError("denied"),
            "gone.txt": OSError("io error"),
        },
    )

    entries = {
        entry["path"].name: entry
        for entry in store._iter_local_scan_entries(root, max_files=10)
    }

    assert {entry["kind"] for entry in entries.values()} == {"inaccessible_file"}
    assert entries["locked.txt"]["reason"].startswith("permission_denied: ")
    assert entries["gone.txt"]["reason"] == "io error"


def test_scan_entries_stop_at_max_files(tmp_path: Path, store) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for index in range(3):
        (root / f"file{index}.txt").write_text("x", encoding="utf-8")

    entries = list(store._iter_local_scan_entries(root, max_files=2))

    assert [entry["kind"] for entry in entries] == ["file", "file", "limit_reached"]
    assert entries[-1]["reason"] == "max_files"


# ── _local_file_decision ─────────────────────────────────────────────────────


def test_local_file_decision_rejects_oversized_files(
    monkeypatch, tmp_path: Path, store
) -> None:
    target = tmp_path / "notes.md"
    target.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(fsutil, "LOCAL_SIZE_LIMITS", {"text": 1, "document": 1})

    decision = store._local_file_decision(target, tmp_path, target.stat())

    assert decision["status"] == "too_large"
    assert decision["reason"] == "size>1"
    assert decision["indexable"] is False
    assert decision["category"] == "text"


# ── audit_local_folder ───────────────────────────────────────────────────────


def test_audit_local_folder_rejects_missing_paths(tmp_path: Path, store) -> None:
    with pytest.raises(ValueError, match="경로가 존재하지 않습니다"):
        store.audit_local_folder(tmp_path / "nope")


def test_audit_local_folder_rejects_files(tmp_path: Path, store) -> None:
    target = tmp_path / "note.md"
    target.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="폴더가 아닙니다"):
        store.audit_local_folder(target)


def test_audit_local_folder_counts_excluded_and_inaccessible(
    monkeypatch, tmp_path: Path, store
) -> None:
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    (root / "notes.md").write_text("hello", encoding="utf-8")
    (root / "alias.md").symlink_to(root / "notes.md")
    (root / "locked.md").write_text("x", encoding="utf-8")
    (root / "photo.png").write_bytes(b"\x89PNG")
    _install_path_faults(
        monkeypatch, stat_fail={"locked.md": PermissionError("denied")}
    )

    payload = store.audit_local_folder(root, include_ocr=True)

    summary = payload["summary"]
    assert summary["excluded_dirs"] == 1
    assert summary["inaccessible_items"] == 1
    assert summary["readable_files"] == 2
    assert summary["limit_reached"] is False
    assert summary["image_ocr_candidates"] == 1
    assert summary["storage_root"] == str(store.db_path.parent)
    assert payload["by_status"]["excluded"] == 1  # the symlink
    assert payload["by_status"]["failed"] == 1  # the unreadable file
    assert payload["include_ocr_requested"] is True
    assert payload["consent_required"]["image_ocr"] is True
    excluded_reasons = {
        sample["name"]: sample["reason"] for sample in payload["excluded_samples"]
    }
    assert excluded_reasons[".git"] == "excluded_folder"
    assert excluded_reasons["alias.md"] == "symlink"
    assert excluded_reasons["locked.md"].startswith("permission_denied: ")


def test_audit_local_folder_stops_at_max_files(tmp_path: Path, store) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for index in range(3):
        (root / f"file{index}.md").write_text("hello", encoding="utf-8")

    payload = store.audit_local_folder(root, max_files=1)

    assert payload["summary"]["limit_reached"] is True
    assert payload["summary"]["total_files"] == 1


def test_audit_local_folder_ignores_unknown_entry_kinds(
    monkeypatch, tmp_path: Path, store
) -> None:
    root = tmp_path / "root"
    root.mkdir()

    def fake_entries(scan_root, *, max_files):
        assert scan_root == root
        yield {"kind": "future_kind", "path": scan_root / "mystery.md"}

    monkeypatch.setattr(store, "_iter_local_scan_entries", fake_entries)

    payload = store.audit_local_folder(root)

    assert payload["summary"]["total_files"] == 0
    assert payload["by_status"] == {}


# ── source mutation guards ───────────────────────────────────────────────────


def test_set_local_source_watch_requires_a_source_id(store) -> None:
    with pytest.raises(ValueError, match="source_id required"):
        store.set_local_source_watch("   ", True)


def test_set_local_source_watch_rejects_unknown_sources(store) -> None:
    with pytest.raises(ValueError, match="knowledge source not found: ghost"):
        store.set_local_source_watch("ghost", True)


def test_remove_local_source_requires_a_source_id(store) -> None:
    with pytest.raises(ValueError, match="source_id required"):
        store.remove_local_source("")


def test_remove_local_source_rejects_unknown_sources(store) -> None:
    with pytest.raises(ValueError, match="knowledge source not found: ghost"):
        store.remove_local_source("ghost")
