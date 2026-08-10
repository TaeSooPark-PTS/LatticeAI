"""wpb01 branch coverage — ``lattice_brain.graph.discovery``.

Two seams:

* ``discover_local_roots`` on a machine where the platform's removable-media
  root is absent (no ``/Volumes`` on macOS, no ``/mnt`` or ``/media`` on
  Linux). Both are forced through the ``Path.exists`` seam so the outcome does
  not depend on which OS the suite happens to run on.
* ``audit_local_folder`` past its 25-entry sample caps — the audit must keep
  *counting* every remaining file while it stops *collecting* examples, for
  each of the four sample sources (excluded dirs, inaccessible files, excluded
  files, non-indexable files) and for the readable-sample list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.graph.discovery as discovery_mod  # noqa: E402
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402

#: Comfortably above the 25-sample cap, so every kind still has entries left
#: after the cap is reached no matter what order the walk produces.
BULK = 30


@pytest.fixture()
def store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _hide_paths(monkeypatch: pytest.MonkeyPatch, hidden: set) -> None:
    real_exists = Path.exists

    def fake_exists(self, *args, **kwargs):
        if str(self) in hidden:
            return False
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)


def _fault_stat(monkeypatch: pytest.MonkeyPatch, prefix: str) -> None:
    """Make ``stat()`` fail for files whose name starts with ``prefix``."""
    real_stat = Path.stat
    real_is_dir = Path.is_dir
    real_is_file = Path.is_file
    real_is_symlink = Path.is_symlink

    def faulted(path: Path) -> bool:
        return path.name.startswith(prefix)

    def fake_stat(self, *args, **kwargs):
        if faulted(self):
            raise OSError(5, "simulated I/O error")
        return real_stat(self, *args, **kwargs)

    def fake_is_dir(self, *args, **kwargs):
        return False if faulted(self) else real_is_dir(self, *args, **kwargs)

    def fake_is_file(self, *args, **kwargs):
        return True if faulted(self) else real_is_file(self, *args, **kwargs)

    def fake_is_symlink(self, *args, **kwargs):
        # is_symlink() routes through stat() on some versions; answer directly
        # so only the product's own stat() call sees the injected fault.
        return False if faulted(self) else real_is_symlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    monkeypatch.setattr(Path, "is_file", fake_is_file)


# ── discover_local_roots ────────────────────────────────────────────────────


def test_macos_roots_without_a_volumes_directory(
    store: KnowledgeGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discovery_mod, "_current_os_type", lambda: "macos")
    _hide_paths(monkeypatch, {"/Volumes"})

    result = store.discover_local_roots()

    assert result["os_type"] == "macos"
    assert [root for root in result["roots"] if root["kind"] == "volume"] == []
    assert any(root["kind"] == "home" for root in result["roots"])


def test_linux_roots_without_mount_directories(
    store: KnowledgeGraphStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discovery_mod, "_current_os_type", lambda: "linux")
    _hide_paths(monkeypatch, {"/mnt", "/media"})

    result = store.discover_local_roots()

    assert result["os_type"] == "linux"
    kinds = {root["kind"] for root in result["roots"]}
    assert "mounts" not in kinds
    assert "volume" not in kinds


# ── audit_local_folder sample caps ──────────────────────────────────────────


def _build_bulk_tree(root: Path) -> None:
    """One folder holding 30 of every entry kind the audit samples."""
    for index in range(BULK):
        (root / (".hidden%02d" % index)).mkdir()
        (root / ("link%02d" % index)).symlink_to(root / "no-such-target")
        (root / ("boom%02d.txt" % index)).write_text("faulted", encoding="utf-8")
        (root / ("ok%02d.txt" % index)).write_text("readable body", encoding="utf-8")
        (root / ("skip%02d.xyz" % index)).write_text("unsupported", encoding="utf-8")


def test_audit_counts_everything_but_caps_every_sample_list(
    store: KnowledgeGraphStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bulk"
    root.mkdir()
    _build_bulk_tree(root)
    _fault_stat(monkeypatch, "boom")

    result = store.audit_local_folder(root)

    summary = result["summary"]
    # Counting is exhaustive …
    assert summary["excluded_dirs"] == BULK
    assert summary["inaccessible_items"] == BULK
    assert summary["readable_files"] == BULK
    assert summary["unsupported_files"] == BULK
    assert result["by_status"]["excluded"] == BULK  # the symlinks
    assert summary["total_files"] == BULK * 2  # readable + unsupported
    # … while sampling stops at 25 for both lists.
    assert len(result["allowed_samples"]) == 25
    assert len(result["excluded_samples"]) == 25


def test_audit_samples_stay_uncapped_on_a_small_tree(
    store: KnowledgeGraphStore, tmp_path: Path
) -> None:
    """The other side of the same guards: below the cap nothing is dropped."""
    root = tmp_path / "small"
    root.mkdir()
    (root / ".hidden").mkdir()
    (root / "link").symlink_to(root / "no-such-target")
    (root / "ok.txt").write_text("readable body", encoding="utf-8")
    (root / "skip.xyz").write_text("unsupported", encoding="utf-8")

    result = store.audit_local_folder(root)

    assert len(result["allowed_samples"]) == 1
    assert len(result["excluded_samples"]) == 3
