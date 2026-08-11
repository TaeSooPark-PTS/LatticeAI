"""The seam the three ingestion mixins share.

``IngestionPipeline`` is assembled from the routing, folder and jobs mixins.
Each of them reads constructor state it does not own and calls back into
``ingest`` — the point of the split is that "how one picture is stored" and
"how a folder is walked" stop sharing a thousand-line file, not that they stop
sharing ``self``.

Typing-only, exactly like :mod:`lattice_brain.graph._kg_contract`: at runtime
the mixins alias it to ``object``, so the MRO and every method resolution stay
byte-for-byte what the single-file class had. Adding a cross-mixin call without
declaring it here is a type error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ingestion_jobs import BackgroundIngestionJob, BackgroundIngestionQueue
from ..multimodal import MultimodalPorts
from .models import IngestionItem, IngestionResult


class IngestionCore:
    """What any ingestion mixin may assume about ``self``.

    Never instantiated and never inherited at runtime — see the module
    docstring. Members are declared, not implemented: the implementation lives
    in ``pipeline.py`` or in whichever mixin owns it.
    """

    # ── State owned by IngestionPipeline.__init__ ────────────────────────────
    _kg: Any
    _hooks: Any
    _enable: bool
    _audit: Optional[Any]
    _max_text_bytes: int
    _pipeline_name: str
    _bg_queue: BackgroundIngestionQueue
    _auto_vector_index_opt_in: bool
    _multimodal_opt_in: bool
    _multimodal: MultimodalPorts
    _keyframes: int

    # ── pipeline.py: the gates, resolved when they are asked ─────────────────
    @property
    def _allow_multimodal(self) -> bool:
        raise NotImplementedError

    @property
    def _allow_video(self) -> bool:
        raise NotImplementedError

    # ── pipeline.py: the one door every mixin routes back through ────────────
    def available(self) -> bool:
        raise NotImplementedError

    def ingest(
        self, item: IngestionItem, *, user_email: Optional[str] = None
    ) -> IngestionResult:
        raise NotImplementedError

    # ── routing.py: reached from the folder walk and the modality doors ──────
    def _resolve_file_path(self, item: IngestionItem) -> Path:
        raise NotImplementedError

    # ── folders.py: the folder-scan allow-list the multimodal gates widen ────
    def _folder_extensions(self) -> frozenset:
        raise NotImplementedError

    # ── jobs_api.py: scheduling, reached from the folder walk ────────────────
    def schedule_background(
        self,
        items: List[IngestionItem],
        *,
        incremental: bool = True,
        user_email: Optional[str] = None,
    ) -> BackgroundIngestionJob:
        raise NotImplementedError

    def run_background_job(
        self, job_id: str, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def _execute_background_job(
        self, job: BackgroundIngestionJob, *, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError
