"""The seam the four LLMRouter mixins share.

``LLMRouter`` is assembled from the loading, registry, generation and document
mixins. Each reads state it does not own — the registry dict and its lock, the
snapshot helper, the cloud error hint — because the point of the split is that
"how a model is loaded" and "how a document is streamed" stop sharing a
1,007-line file, not that they stop sharing ``self``.

Typing-only, exactly like :mod:`lattice_brain.ingestion._contract`: the
declarations below are never the implementation, so the MRO and every method
resolution stay byte-for-byte what the single-file class had. Adding a
cross-mixin call without declaring it here is a type error.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, Tuple

from .catalog import CloudModel


class RouterCore:
    """What any router mixin may assume about ``self``.

    Never instantiated directly. Members are declared, not implemented: the
    implementation lives in whichever mixin owns it.
    """

    # ── State owned by _RegistryMixin.__init__ ───────────────────────────────
    #: A local entry is ``(model, tokenizer, draft_model, loader_kind)``; a
    #: cloud entry is a :class:`CloudModel`.
    _cache: Dict[str, Any]
    _current: Optional[str]
    _last_used: Dict[str, float]
    _max_local_models: int
    #: ``threading.RLock``; annotated ``Any`` because the runtime lock type is
    #: private in typeshed and nothing here depends on its identity.
    _lock: Any

    # ── registry.py: reached from the load path ──────────────────────────────
    def _touch(self, model_id: Optional[str] = None) -> None:
        raise NotImplementedError

    def _enforce_local_model_limit(self, incoming_key: str) -> None:
        raise NotImplementedError

    # ── loading.py: reached from every unload path ───────────────────────────
    def _release_memory(self) -> None:
        raise NotImplementedError

    # ── registry.py: reached from both generation halves ─────────────────────
    def _model_snapshot(
        self, model_id: Optional[str] = None
    ) -> tuple[Optional[str], object | None]:
        raise NotImplementedError

    def _unpack_local_cache(self, cached: Any) -> Tuple[Any, Any, Any, str]:
        raise NotImplementedError

    def _local_server_error_hint(self, cloud: CloudModel, error: Exception) -> str:
        raise NotImplementedError

    # ── generation.py: the document half drains the same queue ───────────────
    @staticmethod
    def _drain_stream_queue(queue: "Any") -> AsyncIterator[str]:
        raise NotImplementedError
