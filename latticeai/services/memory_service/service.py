"""The composed memory service — constructor state plus the six surfaces.

``MemoryService`` owns the state every mixin reads (the workspace store, the
optional Knowledge Graph, the data directory, and whichever conversation
backing is wired) and inherits its behaviour from the store-read, manager,
brief, proof, recall and maintenance mixins. Same public surface, same method
resolution, in seven readable files instead of one twelve-hundred-line one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .brief import MemoryBriefMixin
from .maintenance import MemoryMaintenanceMixin
from .manager import MemoryManagerMixin
from .proof import MemoryProofMixin
from .recall import MemoryRecallMixin
from .stores import MemoryStoreReadsMixin


class MemoryService(
    MemoryStoreReadsMixin,
    MemoryManagerMixin,
    MemoryBriefMixin,
    MemoryProofMixin,
    MemoryRecallMixin,
    MemoryMaintenanceMixin,
):
    def __init__(
        self,
        *,
        store: Any,
        data_dir: Path,
        knowledge_graph: Any = None,
        enable_graph: bool = True,
        history_file: Optional[Path] = None,
        conversation_store: Any = None,
    ):
        self._store = store
        self._kg = knowledge_graph
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)
        self._data_dir = Path(data_dir)
        self._history_file = Path(history_file) if history_file else (self._data_dir / "chat_history.json")
        # v4: the durable SQLite conversation store supersedes the JSON file
        # as the conversation tier's backing store when provided.
        self._conversation_store = conversation_store
