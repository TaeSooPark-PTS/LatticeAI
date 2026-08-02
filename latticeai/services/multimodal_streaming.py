"""Multimodal / video streaming contracts behind NetworkBoundaryMode (Phase 3).

Video generation and other multimodal cloud calls share the same boundary dial
as text: nothing leaves the machine unless mode is CLOUD_ALLOWED *and* the
hybrid policy has ``allow_multimodal=True``.

This module is intentionally adapter-shaped: concrete providers (Runway, Luma,
Veo-compatible endpoints, etc.) plug in later without changing the chat path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol

from latticeai.core.network_boundary import (
    NetworkBoundaryMode,
    normalize_network_mode,
)
from latticeai.services.hybrid_context import MinimalContext


@dataclass
class MultimodalTurnResult:
    user_message: str
    media_urls: List[str] = field(default_factory=list)
    media_kind: str = "video"  # video | image | audio
    answer_text: str = ""
    sent_node_ids: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_message": self.user_message,
            "media_urls": list(self.media_urls),
            "media_kind": self.media_kind,
            "answer_text": self.answer_text,
            "sent_node_ids": list(self.sent_node_ids),
            "provider": self.provider,
            "model": self.model,
            "usage": dict(self.usage),
        }


class MultimodalAdapter(Protocol):
    # See CloudLLMAdapter.stream: an async generator is declared with `def`.
    def stream_media(
        self,
        *,
        prompt: str,
        context: str,
        model: Optional[str] = None,
    ) -> AsyncIterator[Any]:
        """Yield progress events, then a final event with media_urls.

        Typed as ``Any`` rather than ``Dict[str, Any]`` because adapters are
        supplied by callers: the bridge validates each event's shape rather
        than trusting the annotation.
        """
        ...


class MultimodalStreamingBridge:
    """Boundary-gated multimodal turn orchestrator."""

    def __init__(self, adapter: Optional[MultimodalAdapter] = None) -> None:
        self._adapter = adapter

    async def run_turn(
        self,
        *,
        user_message: str,
        minimal: MinimalContext,
        mode: NetworkBoundaryMode | str,
        allow_multimodal: bool,
        model: Optional[str] = None,
    ) -> MultimodalTurnResult:
        mode = normalize_network_mode(mode)
        if mode != NetworkBoundaryMode.CLOUD_ALLOWED:
            raise PermissionError(
                f"multimodal cloud refused under NetworkBoundaryMode={mode.value!r}"
            )
        if not allow_multimodal:
            raise PermissionError(
                "multimodal cloud is disabled by hybrid policy (allow_multimodal=false)"
            )
        if self._adapter is None:
            return MultimodalTurnResult(
                user_message=user_message,
                media_urls=[],
                answer_text=(
                    "[multimodal adapter not configured] "
                    f"Would stream media grounded on {len(minimal.node_ids)} local node(s)."
                ),
                sent_node_ids=list(minimal.node_ids),
                provider="none",
                model=model or "",
            )

        urls: List[str] = []
        notes: List[str] = []
        async for event in self._adapter.stream_media(
            prompt=user_message,
            context=minimal.compact_text,
            model=model,
        ):
            if not isinstance(event, dict):
                continue
            if event.get("media_url"):
                urls.append(str(event["media_url"]))
            if event.get("text"):
                notes.append(str(event["text"]))
        return MultimodalTurnResult(
            user_message=user_message,
            media_urls=urls,
            answer_text="\n".join(notes),
            sent_node_ids=list(minimal.node_ids),
            provider=getattr(self._adapter, "provider_name", "multimodal"),
            model=str(model or getattr(self._adapter, "default_model", "")),
        )


__all__ = [
    "MultimodalTurnResult",
    "MultimodalAdapter",
    "MultimodalStreamingBridge",
]
