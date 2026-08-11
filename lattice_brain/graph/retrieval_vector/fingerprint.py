"""Which embedder actually built the index, recorded in ``graph_meta``.

``vector_search`` filters on the current model/dim, so swapping the embedder
silently yields zero vector rows; the fingerprint turns that into the honest
``stale_embedder`` signal. Moved verbatim out of ``retrieval_vector.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from .._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


class _VectorFingerprintMixin(_Core):
    """Embedder-fingerprint read/write. Composed into the public mixin."""

    # ── embedder fingerprint (review Wave 2.2 — stale_embedder) ──────────────
    # vector_search filters on the CURRENT model/dim, so swapping the embedder
    # silently yields zero vector rows. The fingerprint persisted in graph_meta
    # records which embedder actually built the index; a mismatch is surfaced
    # as the honest ``stale_embedder`` signal instead of a silent degradation.

    _EMBEDDER_FINGERPRINT_KEY = "embedder_fingerprint"

    def _embedder_fingerprint_record(
        self, conn: sqlite3.Connection
    ) -> Optional[Dict[str, Any]]:
        """Read the recorded embedder fingerprint from graph_meta (or None)."""
        row = conn.execute(
            "SELECT value FROM graph_meta WHERE key=?",
            (self._EMBEDDER_FINGERPRINT_KEY,),
        ).fetchone()
        if not row:
            return None
        payload = _safe_loads(row["value"])
        if not isinstance(payload, dict) or not payload.get("model_id"):
            return None
        try:
            dim = int(payload.get("dim") or 0)
        except (TypeError, ValueError):
            dim = 0
        return {"model_id": str(payload["model_id"]), "dim": dim}

    def _write_embedder_fingerprint(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Persist the CURRENT embedder identity (same transaction as caller)."""
        fingerprint = {
            "model_id": self._embedding_model.model_id,
            "dim": int(self._embedding_model.dim),
        }
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            (self._EMBEDDER_FINGERPRINT_KEY, _json(fingerprint)),
        )
        return fingerprint

    def record_embedder_fingerprint(self) -> Dict[str, Any]:
        """Record the current embedder (model_id + dim) as the index builder."""
        with self._connect() as conn:
            return self._write_embedder_fingerprint(conn)

    def embedder_fingerprint_status(self) -> Dict[str, Any]:
        """Compare the current embedder against the recorded index fingerprint.

        Returns ``{"current": {model_id, dim}, "recorded": {...} | None,
        "stale_embedder": bool}``. ``stale_embedder`` is True only when a
        fingerprint was recorded AND it differs from the current embedder —
        an unrecorded index (legacy DBs, nothing indexed yet) is honestly
        "unknown", never reported stale. Never raises.
        """
        current = {
            "model_id": self._embedding_model.model_id,
            "dim": int(self._embedding_model.dim),
        }
        recorded: Optional[Dict[str, Any]] = None
        try:
            with self._connect() as conn:
                recorded = self._embedder_fingerprint_record(conn)
        except Exception:  # noqa: BLE001 — status must degrade, never raise
            recorded = None
        stale = bool(
            recorded is not None
            and (
                recorded.get("model_id") != current["model_id"]
                or recorded.get("dim") != current["dim"]
            )
        )
        return {"current": current, "recorded": recorded, "stale_embedder": stale}
