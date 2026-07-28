"""Network boundary modes for hybrid local + cloud LLM usage.

Local-first principle: the Knowledge Graph and all durable memory stay on the
machine. Cloud LLMs are an explicit, opt-in worker. This module defines the
dial that controls whether any knowledge is allowed to leave the host.

Modes
-----
* ``local_only``   — default. No chat context is ever sent to a cloud provider.
* ``cloud_allowed`` — user has explicitly opted in. Only the *minimal* related
  nodes selected by the hybrid context extractor may be sent. Sensitive types
  and hard filters still apply (mode-invariant).

The mode is orthogonal to PermissionMode (agent autonomy). A session can be
``cloud_allowed`` + ``strict`` at the same time.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Mapping, Optional, Set


class NetworkBoundaryMode(str, Enum):
    LOCAL_ONLY = "local_only"
    CLOUD_ALLOWED = "cloud_allowed"


DEFAULT_NETWORK_MODE = NetworkBoundaryMode.LOCAL_ONLY

# Node types that must never be included in a cloud payload, regardless of mode.
# Extend carefully; this is a hard circuit breaker.
HARD_BLOCK_NODE_TYPES: Set[str] = frozenset({
    # Keep empty for v1 scaffolding; product can add "Credential", "Secret", etc.
})

# Metadata keys that, when present and truthy, block a node from leaving.
HARD_BLOCK_METADATA_FLAGS: Set[str] = frozenset({
    "sensitive",
    "private",
    "do_not_share",
    "local_only",
})


def normalize_network_mode(value: Any) -> NetworkBoundaryMode:
    """Parse user/API/env input into a NetworkBoundaryMode; unknown → local_only."""
    if isinstance(value, NetworkBoundaryMode):
        return value
    text = str(value or "").strip().lower()
    aliases = {
        "local_only": NetworkBoundaryMode.LOCAL_ONLY,
        "local": NetworkBoundaryMode.LOCAL_ONLY,
        "local-only": NetworkBoundaryMode.LOCAL_ONLY,
        "offline": NetworkBoundaryMode.LOCAL_ONLY,
        "cloud_allowed": NetworkBoundaryMode.CLOUD_ALLOWED,
        "cloud": NetworkBoundaryMode.CLOUD_ALLOWED,
        "cloud-allowed": NetworkBoundaryMode.CLOUD_ALLOWED,
        "hybrid": NetworkBoundaryMode.CLOUD_ALLOWED,
        "online": NetworkBoundaryMode.CLOUD_ALLOWED,
    }
    return aliases.get(text, DEFAULT_NETWORK_MODE)


def network_mode_catalog() -> list[Dict[str, Any]]:
    """UI/API catalog for the network boundary selector."""
    return [
        {
            "id": NetworkBoundaryMode.LOCAL_ONLY.value,
            "label": "Local only",
            "label_ko": "로컬만",
            "summary": "Nothing leaves this machine. Answers use local models and the local Brain only.",
            "summary_ko": "이 컴퓨터를 벗어나지 않습니다. 로컬 모델과 로컬 Brain만 사용합니다.",
            "risk": "low",
            "requires_ack": False,
        },
        {
            "id": NetworkBoundaryMode.CLOUD_ALLOWED.value,
            "label": "Cloud streaming allowed",
            "label_ko": "클라우드 스트리밍 허용",
            "summary": (
                "Minimal related Knowledge Graph nodes may be sent to a cloud LLM. "
                "The streamed answer is written back into the local Brain with provenance."
            ),
            "summary_ko": (
                "관련된 최소 Knowledge Graph 노드만 클라우드 LLM으로 전송될 수 있습니다. "
                "스트리밍 답변은 provenance와 함께 로컬 Brain에 다시 기록됩니다."
            ),
            "risk": "medium",
            "requires_ack": True,
            "warning": (
                "Cloud mode sends a compact summary of selected local nodes to an external provider. "
                "Sensitive nodes remain blocked. You can switch back to Local only at any time."
            ),
            "warning_ko": (
                "클라우드 모드는 선택된 로컬 노드의 압축 요약을 외부 제공자에게 전송합니다. "
                "민감 노드는 계속 차단됩니다. 언제든지 로컬만으로 되돌릴 수 있습니다."
            ),
        },
    ]


def is_node_blocked_for_cloud(node: Mapping[str, Any]) -> Optional[str]:
    """Return a reason string when this node must never leave the host."""
    node_type = str(node.get("type") or "")
    if node_type in HARD_BLOCK_NODE_TYPES:
        return f"node type {node_type!r} is blocked from cloud payloads"

    meta = node.get("metadata") or {}
    if not isinstance(meta, Mapping):
        return None
    for flag in HARD_BLOCK_METADATA_FLAGS:
        if meta.get(flag):
            return f"node flagged {flag!r} is blocked from cloud payloads"
    return None


def network_mode_contract(mode: NetworkBoundaryMode | str) -> Dict[str, Any]:
    """Serializable contract for API / agent / UI."""
    mode = normalize_network_mode(mode)
    entry = next(
        (m for m in network_mode_catalog() if m["id"] == mode.value),
        network_mode_catalog()[0],
    )
    return {
        "mode": mode.value,
        "label": entry["label"],
        "label_ko": entry["label_ko"],
        "risk": entry["risk"],
        "requires_ack": entry["requires_ack"],
        "allows_cloud": mode == NetworkBoundaryMode.CLOUD_ALLOWED,
        "hard_block_node_types": sorted(HARD_BLOCK_NODE_TYPES),
        "hard_block_metadata_flags": sorted(HARD_BLOCK_METADATA_FLAGS),
    }


__all__ = [
    "NetworkBoundaryMode",
    "DEFAULT_NETWORK_MODE",
    "HARD_BLOCK_NODE_TYPES",
    "HARD_BLOCK_METADATA_FLAGS",
    "normalize_network_mode",
    "network_mode_catalog",
    "is_node_blocked_for_cloud",
    "network_mode_contract",
]
