"""The two records the pipeline speaks in: one item in, one result out.

:class:`IngestionItem` is what every source normalizes to before the pipeline
sees it; :class:`IngestionResult` is what every source normalizes to after. The
result's ``as_dict`` is the frozen ``/api/ingestion`` payload shape — additive
keys appear only when populated, so pre-v9.8 consumers see what they always did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class IngestionItem:
    """A single thing to ingest, normalized across every source type."""

    source_type: str
    title: Optional[str] = None
    text: Optional[str] = None          # text/web sources
    path: Optional[str] = None          # file sources
    source_uri: Optional[str] = None
    mime_type: Optional[str] = None
    owner: Optional[str] = None
    workspace_id: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    captured_at: Optional[str] = None
    modified_at: Optional[str] = None
    conversation_id: Optional[str] = None
    agent_used: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IngestionResult:
    """The outcome of one ingestion, including provenance and idempotency."""

    status: str                         # ok | unavailable | blocked | failed
    source_type: str
    node_id: Optional[str] = None
    source_node_id: Optional[str] = None
    content_hash: Optional[str] = None
    title: Optional[str] = None
    chunk_ids: List[str] = field(default_factory=list)
    chunk_count: int = 0
    duplicate: bool = False
    embedded: bool = False
    indexing_status: str = "pending"    # indexed | skipped | failed | pending
    provenance_id: Optional[str] = None
    detail: Optional[str] = None
    # v9.8.0 additive quality fields — advisory only, never gate behavior.
    extraction_quality: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    quality_gate: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "source_type": self.source_type,
            "node_id": self.node_id,
            "source_node_id": self.source_node_id,
            "content_hash": self.content_hash,
            "title": self.title,
            "chunk_ids": self.chunk_ids,
            "chunk_count": self.chunk_count,
            "duplicate": self.duplicate,
            "embedded": self.embedded,
            "indexing_status": self.indexing_status,
            "provenance_id": self.provenance_id,
            "detail": self.detail,
        }
        # Additive keys only when populated so pre-v9.8 payloads are unchanged.
        if self.extraction_quality is not None:
            payload["extraction_quality"] = self.extraction_quality
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.quality_gate is not None:
            payload["quality_gate"] = self.quality_gate
        return payload
