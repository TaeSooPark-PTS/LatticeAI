"""Brain Core runtime assembly for app startup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from latticeai.runtime.stages import RuntimeStage


@dataclass(frozen=True)
class BrainRuntime(RuntimeStage):
    BRAIN_CORE: Any
    KNOWLEDGE_GRAPH: Any
    CONVERSATIONS: Any


def build_brain_runtime(
    *,
    data_dir: Any,
    history_file: Any,
    enable_graph: bool,
    embedder: Any,
    storage_engine: Any,
) -> BrainRuntime:
    """Construct Brain Core storage/conversation primitives behind one seam."""

    from lattice_brain import BrainCore, ConversationStore

    brain_core = (
        BrainCore.from_paths(
            data_dir,
            embedder=embedder.provider,
            storage_engine=storage_engine,
        )
        if enable_graph
        else None
    )
    knowledge_graph = brain_core.knowledge if brain_core is not None else None
    conversations = (
        brain_core.conversations
        if brain_core is not None
        else ConversationStore(data_dir / "knowledge_graph.sqlite")
    )
    conversations.import_legacy_json(history_file)
    return BrainRuntime(
        BRAIN_CORE=brain_core,
        KNOWLEDGE_GRAPH=knowledge_graph,
        CONVERSATIONS=conversations,
    )


__all__ = ["BrainRuntime", "build_brain_runtime"]
