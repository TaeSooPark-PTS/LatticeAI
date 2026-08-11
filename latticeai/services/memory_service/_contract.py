"""The seam the memory mixins share.

``MemoryService`` is assembled from the store-read, manager, brief, proof,
recall and maintenance mixins. Every one of them reads constructor state it
does not own, and most of them call methods another mixin implements. That
contract existed as an unwritten convention inside one class; this module
writes it down.

Typing-only, exactly like :mod:`lattice_brain.portability._contract`: the
declarations below are never executed as behaviour — each body is a bare
``raise NotImplementedError`` that the composed class always overrides — so the
MRO and every method resolution stay what the single-file class had. Adding a
cross-mixin call without declaring it here is a type error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryCore:
    """What any memory mixin may assume about ``self``.

    Members are declared, not implemented: the implementation lives in
    ``service.py`` (constructor state) or in whichever mixin owns the method.
    """

    # ── State owned by MemoryService.__init__ ────────────────────────────────
    _store: Any
    _kg: Any
    _enable_graph: bool
    _data_dir: Path
    _history_file: Path
    _conversation_store: Any

    # ── stores.py: the reads every other surface is built on ─────────────────
    def _workspace_memories(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _all_memories(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _snapshots(self, *, workspace_id: Optional[str]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _conversations(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _scoped_conversations(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _kg_stats(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def _kg_index(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # ── manager.py: the cross-tier report the brief and the proof rest on ────
    def manager(
        self, *, user_email: Optional[str] = None, workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # ── proof.py / recall.py: what the brief composes ────────────────────────
    def brain_proof(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        active_model: Optional[str] = None,
        recall_query: str = "",
        limit: int = 3,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def recall(
        self,
        query: str,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # ── maintenance.py: clear() delegates every scope to prune() ─────────────
    def prune(
        self,
        *,
        ids: Optional[List[str]] = None,
        kind: Optional[str] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError
