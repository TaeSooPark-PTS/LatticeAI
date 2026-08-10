"""Opt-in folder watch mode for the unified ingestion pipeline (backlog #8).

Watches previously-ingested folders and incrementally re-ingests new/changed
files through the normal :class:`IngestionPipeline` door (same filters, same
quality pipeline, same workspace scoping, same provenance).

Design decisions (review §7.2 B + risk section):

* **Default OFF, explicit opt-in.** A watch exists only after an explicit API
  call. The stored config is the durable record of that consent; ``restore()``
  resumes only entries persisted with ``enabled: true`` and never invents one.
* **Polling with mtime snapshots**, not an OS watcher. ``watchdog`` is already
  a dependency, but polling is deterministic, portable, and testable
  (``scan_once`` runs synchronously); the interval is configurable via
  ``LATTICEAI_FOLDER_WATCH_INTERVAL`` (seconds, default 30).
* **Reuses the folder-ingest filters** (skip dirs, ``.latticeignore``,
  extension allow-list, size cap) so watch mode can never ingest something a
  manual folder ingest would have refused.
* Deleted files are *counted* but their graph nodes are not removed — node
  deletion is a destructive operation that stays behind explicit user flows.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from lattice_brain.gates import FeatureGate
from lattice_brain.ingestion import (
    DEFAULT_FOLDER_EXTENSIONS,
    DEFAULT_MAX_FILE_BYTES,
    FOLDER_DEFAULT_SKIP_DIRS,
    FOLDER_DOCUMENT_EXTENSIONS,
    LATTICEIGNORE_FILENAME,
    IngestionItem,
    _load_latticeignore,
    _matches_ignore,
)
from latticeai.core.quiet import quiet
from latticeai.core.timeutil import now_iso as _now_iso

WATCH_INTERVAL_ENV = "LATTICEAI_FOLDER_WATCH_INTERVAL"
DEFAULT_WATCH_INTERVAL_SECONDS = 30.0
MAX_FILES_PER_SCAN = 200  # per-watch per-scan ingest cap (thrash guard)

# ── vault watch (v11.2.0) ────────────────────────────────────────────────────
#: A watched *vault* is not a watched folder. 11.1.0 refused to schedule vault
#: syncs for a real reason — link edges need the node ids that only a completed
#: inline ingest has, so a per-file incremental pass would report edges it never
#: wrote. The resolution is not to make the bridge incremental: it is to notice
#: that the vault changed and then run the **whole** bridge sync, which does
#: produce those edges and is idempotent by content hash. What the snapshot buys
#: is skipping the sync entirely when nothing moved.
WATCH_KIND_FOLDER = "folder"
WATCH_KIND_VAULT = "vault"
WATCH_KINDS = (WATCH_KIND_FOLDER, WATCH_KIND_VAULT)
VAULT_WATCH_ENV = "LATTICEAI_VAULT_WATCH"
#: Opt-in, default off, resolved when asked so a settings toggle can move it.
VAULT_WATCH_GATE = FeatureGate(
    VAULT_WATCH_ENV,
    default=False,
    name="vault_watch",
    detail=(
        "Watching an external vault re-syncs it in the background; it is off "
        "until you turn it on."
    ),
)
VAULT_WATCH_DISABLED_DETAIL = (
    "vault watch is off by default; turn it on to let an external vault "
    f"re-sync in the background ({VAULT_WATCH_ENV})"
)


def _default_interval() -> float:
    raw = os.getenv(WATCH_INTERVAL_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_WATCH_INTERVAL_SECONDS
    except ValueError:
        value = DEFAULT_WATCH_INTERVAL_SECONDS
    return max(1.0, value)


class FolderWatchService:
    """Poll-based incremental re-ingestion of explicitly opted-in folders."""

    def __init__(
        self,
        *,
        pipeline: Any,
        config_path: Path,
        interval_seconds: Optional[float] = None,
        vault_bridge: Optional[Any] = None,
    ) -> None:
        self._pipeline = pipeline
        # Injected rather than imported at call time so a vault watch can be
        # driven by a test double, and so this module keeps knowing nothing
        # about how a vault is parsed.
        self._vault_bridge = vault_bridge
        self._config_path = Path(config_path)
        self._interval = float(interval_seconds) if interval_seconds else _default_interval()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._watches: Dict[str, Dict[str, Any]] = self._load_config()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load_config(self) -> Dict[str, Dict[str, Any]]:
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        watches = payload.get("watches")
        return dict(watches) if isinstance(watches, dict) else {}

    def _save_config(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._config_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"watches": self._watches}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._config_path)

    # ── public API ───────────────────────────────────────────────────────────
    def enable(
        self,
        path: Any,
        *,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        recursive: bool = True,
        kind: str = WATCH_KIND_FOLDER,
    ) -> Dict[str, Any]:
        """Explicit opt-in: snapshot the folder now, ingest only future changes.

        The folder is assumed to have been ingested already (the UI offers
        watch after a folder ingest); the enable-time snapshot is the baseline,
        so enabling never re-ingests the whole folder.

        ``kind="vault"`` watches an external Obsidian vault instead, re-running
        the whole bridge sync when the snapshot moves. That mode carries its own
        opt-in gate on top of this one and refuses while it is off.
        """
        kind = str(kind or WATCH_KIND_FOLDER).strip().lower()
        if kind not in WATCH_KINDS:
            return {"status": "failed", "detail": f"unknown watch kind: {kind}"}
        if kind == WATCH_KIND_VAULT and not VAULT_WATCH_GATE.enabled():
            return {"status": "disabled", "detail": VAULT_WATCH_DISABLED_DETAIL}
        root = Path(path).expanduser()
        if not root.is_dir():
            return {"status": "failed", "detail": f"not a directory: {root}"}
        root = root.resolve()
        with self._lock:
            for watch in self._watches.values():
                same_scope = (watch.get("workspace_id") or None) == (workspace_id or None)
                if watch.get("path") == str(root) and same_scope:
                    watch["enabled"] = True
                    watch["kind"] = kind
                    watch["snapshot"] = self._snapshot(root, recursive=bool(watch.get("recursive", True)))
                    self._save_config()
                    self._ensure_thread()
                    return {"status": "ok", "watch": self._public_watch(watch), "already_watching": True}
            watch_id = f"watch_{uuid.uuid4().hex[:12]}"
            watch = {
                "id": watch_id,
                "path": str(root),
                "owner": owner,
                "workspace_id": workspace_id,
                "recursive": bool(recursive),
                "kind": kind,
                "enabled": True,
                "created_at": _now_iso(),
                "last_scan_at": None,
                "last_result": None,
                "snapshot": self._snapshot(root, recursive=bool(recursive)),
            }
            self._watches[watch_id] = watch
            self._save_config()
            self._ensure_thread()
            return {"status": "ok", "watch": self._public_watch(watch), "already_watching": False}

    def disable(
        self,
        *,
        watch_id: Optional[str] = None,
        path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Opt back out: remove the stored consent record entirely."""
        with self._lock:
            target_id = None
            if watch_id and watch_id in self._watches:
                target_id = watch_id
            elif path:
                wanted = str(Path(path).expanduser().resolve())
                for wid, watch in self._watches.items():
                    if watch.get("path") == wanted:
                        target_id = wid
                        break
            if target_id is None:
                return {"status": "not_found"}
            removed = self._watches.pop(target_id)
            self._save_config()
            if not any(w.get("enabled") for w in self._watches.values()):
                self._stop_thread_locked()
        return {"status": "ok", "watch": self._public_watch(removed)}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            watches = [self._public_watch(w) for w in self._watches.values()]
            polling = self._thread is not None and self._thread.is_alive()
        return {
            "enabled_count": sum(1 for w in watches if w.get("enabled")),
            "polling": polling,
            "interval_seconds": self._interval,
            "watches": watches,
            "kinds": list(WATCH_KINDS),
            "vault_watch": {
                **VAULT_WATCH_GATE.describe(),
                "bridge_wired": self._vault_bridge is not None,
            },
            "note": (
                "Watch mode is opt-in and off by default. Deleted files are "
                "counted but never auto-removed from the Brain."
            ),
        }

    def restore(self) -> Dict[str, Any]:
        """Resume polling for watches persisted with the explicit opt-in.

        Never starts anything when the stored config has no enabled entries —
        an empty/missing config means the user never opted in.
        """
        with self._lock:
            enabled = [w for w in self._watches.values() if w.get("enabled")]
            if enabled:
                self._ensure_thread()
        return {"restored": len(enabled), "polling": bool(enabled)}

    def stop_all(self) -> None:
        """Stop the polling thread (keeps the stored opt-in for next start)."""
        with self._lock:
            self._stop_thread_locked()

    def scan_once(self, watch_id: str) -> Dict[str, Any]:
        """Synchronously scan one watch and ingest new/changed files."""
        with self._lock:
            watch = self._watches.get(watch_id)
            if watch is None:
                return {"status": "not_found", "watch_id": watch_id}
            if not watch.get("enabled"):
                return {"status": "disabled", "watch_id": watch_id}
            snapshot = dict(watch.get("snapshot") or {})
            root = Path(watch["path"])
            recursive = bool(watch.get("recursive", True))
            owner = watch.get("owner")
            workspace_id = watch.get("workspace_id")
            kind = str(watch.get("kind") or WATCH_KIND_FOLDER)

        result: Dict[str, Any] = {
            "status": "ok",
            "watch_id": watch_id,
            "kind": kind,
            "new": 0,
            "changed": 0,
            "removed": 0,
            "ingested": 0,
            "duplicate": 0,
            "failed": 0,
            "errors": [],
        }
        if not root.is_dir():
            result.update(status="failed", detail=f"folder unavailable: {root}")
            self._record_scan(watch_id, result, snapshot=None)
            return result

        current = self._snapshot(root, recursive=recursive)
        changed_rel: List[str] = []
        for rel, stamp in current.items():
            previous = snapshot.get(rel)
            if previous is None:
                result["new"] += 1
                changed_rel.append(rel)
            elif previous != stamp:
                result["changed"] += 1
                changed_rel.append(rel)
        result["removed"] = sum(1 for rel in snapshot if rel not in current)

        if kind == WATCH_KIND_VAULT:
            self._vault_scan(
                result,
                root=root,
                changed=bool(changed_rel) or bool(result["removed"]),
                owner=owner,
                workspace_id=workspace_id,
            )
            self._record_scan(watch_id, result, snapshot=current)
            return result

        for rel in changed_rel[:MAX_FILES_PER_SCAN]:
            path = root / rel
            ext = path.suffix.lower()
            metadata: Dict[str, Any] = {
                "relative_path": rel,
                "folder_watch": True,
                "watch_id": watch_id,
            }
            if ext in FOLDER_DOCUMENT_EXTENSIONS:
                source_type = "pdf"
            else:
                source_type = "file"
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    result["failed"] += 1
                    if len(result["errors"]) < 20:
                        result["errors"].append({"path": rel, "detail": f"read failed: {exc}"})
                    continue
                metadata["extracted"] = {"content": content, "chars": len(content)}
            outcome = self._pipeline.ingest(
                IngestionItem(
                    source_type=source_type,
                    title=path.name,
                    path=str(path),
                    source_uri=str(path),
                    owner=owner,
                    workspace_id=workspace_id,
                    metadata=metadata,
                ),
                user_email=owner,
            )
            if outcome.status == "ok":
                if outcome.duplicate:
                    result["duplicate"] += 1
                else:
                    result["ingested"] += 1
            else:
                result["failed"] += 1
                if len(result["errors"]) < 20:
                    result["errors"].append({
                        "path": rel,
                        "detail": outcome.detail or outcome.status,
                    })
        if len(changed_rel) > MAX_FILES_PER_SCAN:
            result["truncated"] = True
        self._record_scan(watch_id, result, snapshot=current)
        return result

    # ── internals ────────────────────────────────────────────────────────────
    def _vault_scan(
        self,
        result: Dict[str, Any],
        *,
        root: Path,
        changed: bool,
        owner: Optional[str],
        workspace_id: Optional[str],
    ) -> None:
        """Re-run the whole vault sync — but only when something actually moved.

        A full sync is what makes the link edges real; the snapshot is only
        there to avoid paying for one when the vault is untouched. The gate is
        re-checked here, not just at enable time, so turning vault watch off
        stops the background work immediately instead of at the next restart.
        """
        if not VAULT_WATCH_GATE.enabled():
            result.update(status="disabled", detail=VAULT_WATCH_DISABLED_DETAIL)
            return
        if not changed:
            result["synced"] = False
            result["detail"] = "vault unchanged since the last scan"
            return
        if self._vault_bridge is None:
            result.update(
                status="failed",
                synced=False,
                detail="no vault bridge is wired; the vault was not re-synced",
            )
            return
        try:
            summary = self._vault_bridge.sync(
                str(root), owner=owner, workspace_id=workspace_id, user_email=owner,
            )
        except Exception as exc:  # noqa: BLE001 — one bad sync must not kill the poller
            result.update(status="failed", synced=False, detail=f"vault sync failed: {exc}")
            return
        result["synced"] = True
        result["ingested"] = int(summary.get("ingested") or 0)
        result["duplicate"] = int(summary.get("duplicate") or 0)
        result["failed"] = int(summary.get("failed") or 0)
        result["errors"] = list(summary.get("errors") or [])[:20]
        result["edges"] = summary.get("edges")
        result["status"] = "ok" if summary.get("status") in ("ok", "dry_run") else str(
            summary.get("status") or "ok"
        )

    def _record_scan(
        self,
        watch_id: str,
        result: Dict[str, Any],
        *,
        snapshot: Optional[Dict[str, Any]],
    ) -> None:
        with self._lock:
            watch = self._watches.get(watch_id)
            if watch is None:
                return
            watch["last_scan_at"] = _now_iso()
            watch["last_result"] = {
                key: result[key]
                for key in ("status", "new", "changed", "removed", "ingested", "duplicate", "failed")
                if key in result
            }
            # Watch trust UI (review Wave 1.3): keep a small sample of the
            # latest scan's failures so the home health card can show *why*
            # a watch is unhealthy, not just a count.
            watch["last_errors"] = list(result.get("errors") or [])[:3]
            if snapshot is not None:
                watch["snapshot"] = snapshot
            self._save_config()

    def _snapshot(self, root: Path, *, recursive: bool) -> Dict[str, List[float]]:
        """{relative_posix_path: [mtime, size]} using the folder-ingest filters."""
        patterns = _load_latticeignore(root)
        snapshot: Dict[str, List[float]] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            rel_dir = current.relative_to(root)
            kept: List[str] = []
            for name in sorted(dirnames):
                if name in FOLDER_DEFAULT_SKIP_DIRS or name.startswith("."):
                    continue
                rel = name if str(rel_dir) == "." else (rel_dir / name).as_posix()
                if _matches_ignore(rel, name, is_dir=True, patterns=patterns):
                    continue
                kept.append(name)
            dirnames[:] = kept if recursive else []
            for name in sorted(filenames):
                if name == LATTICEIGNORE_FILENAME or name.startswith("."):
                    continue
                path = current / name
                rel = name if str(rel_dir) == "." else (rel_dir / name).as_posix()
                if _matches_ignore(rel, name, is_dir=False, patterns=patterns):
                    continue
                if path.suffix.lower() not in DEFAULT_FOLDER_EXTENSIONS:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    quiet()
                    continue
                if stat.st_size > DEFAULT_MAX_FILE_BYTES:
                    continue
                snapshot[rel] = [round(stat.st_mtime, 3), stat.st_size]
        return snapshot

    @staticmethod
    def _public_watch(watch: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": watch.get("id"),
            "path": watch.get("path"),
            "owner": watch.get("owner"),
            "workspace_id": watch.get("workspace_id"),
            "recursive": bool(watch.get("recursive", True)),
            "kind": str(watch.get("kind") or WATCH_KIND_FOLDER),
            "enabled": bool(watch.get("enabled")),
            "created_at": watch.get("created_at"),
            "last_scan_at": watch.get("last_scan_at"),
            "last_result": watch.get("last_result"),
            "last_errors": list(watch.get("last_errors") or []),
            "tracked_files": len(watch.get("snapshot") or {}),
        }

    def _ensure_thread(self) -> None:
        """Start the poller (caller holds the lock)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._poll_loop, name="folder-watch-poller", daemon=True,
        )
        self._thread.start()

    def _stop_thread_locked(self) -> None:
        self._stop.set()
        self._thread = None

    def _poll_loop(self) -> None:
        stop = self._stop
        while not stop.wait(self._interval):
            with self._lock:
                enabled_ids = [
                    wid for wid, watch in self._watches.items() if watch.get("enabled")
                ]
            if not enabled_ids:
                return
            for watch_id in enabled_ids:
                if stop.is_set():
                    return
                try:
                    self.scan_once(watch_id)
                except Exception:  # noqa: BLE001 — one bad watch must not kill the poller
                    quiet()
                    continue


__all__ = [
    "DEFAULT_WATCH_INTERVAL_SECONDS",
    "VAULT_WATCH_DISABLED_DETAIL",
    "VAULT_WATCH_ENV",
    "VAULT_WATCH_GATE",
    "WATCH_INTERVAL_ENV",
    "WATCH_KINDS",
    "WATCH_KIND_FOLDER",
    "WATCH_KIND_VAULT",
    "FolderWatchService",
]
