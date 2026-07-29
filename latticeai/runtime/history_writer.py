"""Persisting one chat turn: redact, audit, store, then grow the Brain.

Extracted from the `app_factory._build` closure in 10.3.0. It was 66 lines
deep inside a 1,343-line composition root, which meant the one function that
decides what a chat message looks like *after* redaction — and what the audit
log records about it — could not be exercised without standing up the whole
application.

The order here is the contract, and it is deliberate:

1. **Redact first.** Everything downstream (the audit preview, the durable
   store, the knowledge graph) sees the redacted text, never the original.
2. **Audit before storing.** The audit row carries a masked preview and the
   sensitivity verdict, so a message that should not have been sent is
   visible in the log even if the store write later fails.
3. **Ingest through the pipeline, not the store.** Chat messages enter the
   Brain the same way files and web pages do, so they get provenance and the
   hook lifecycle rather than a direct write that bypasses both.
4. **A failed ingest does not lose the message.** Graph growth is best-effort;
   the conversation store is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryWriterDeps:
    """Everything one chat turn needs, injected so this is testable in isolation."""

    conversations: Any
    append_audit_event: Callable[..., None]
    classify_sensitive_message: Callable[[Dict[str, Any], int], Dict[str, Any]]
    redact_secret_text: Callable[[str], str]
    normalize_branding: Callable[[str], str]
    ingestion_pipeline: Any = None
    ingestion_item_factory: Any = None
    enable_graph: bool = False
    knowledge_graph: Any = None


def write_chat_turn(
    role: str,
    message: str,
    *,
    user_email: Optional[str] = None,
    user_nickname: Optional[str] = None,
    source: Optional[str] = None,
    conversation_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    deps: HistoryWriterDeps,
) -> None:
    """Persist one chat turn. Never raises: a failure here must not lose a reply."""
    redact_secret_text = deps.redact_secret_text
    normalize_branding = deps.normalize_branding
    classify_sensitive_message = deps.classify_sensitive_message
    append_audit_event = deps.append_audit_event
    conversations = deps.conversations
    ingestion_pipeline = deps.ingestion_pipeline
    ingestion_item_factory = deps.ingestion_item_factory
    enable_graph = deps.enable_graph
    knowledge_graph = deps.knowledge_graph

    try:
        # 1. Redact before anything else sees the text.
        message = redact_secret_text(message)
        if role == "assistant":
            message = normalize_branding(message)

        item: Dict[str, Any] = {
            "role": role,
            "content": message,
            "timestamp": datetime.now().isoformat(),
        }
        for key, value in (
            ("user_email", user_email),
            ("user_nickname", user_nickname),
            ("source", source),
            ("conversation_id", conversation_id),
            ("workspace_id", workspace_id),
        ):
            if value:
                item[key] = value

        # 2. Audit carries a masked preview and the verdict, never the body.
        sensitive = classify_sensitive_message(item, -1)
        append_audit_event(
            "chat_message",
            role=role,
            user_email=user_email,
            user_nickname=user_nickname,
            source=source,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            content_preview=sensitive.get("preview"),
            content_chars=len(message or ""),
            sensitivity=sensitive.get("sensitivity"),
            sensitive_labels=sensitive.get("labels") or [],
        )

        # 3. Durable episodic memory. Unbounded SQLite; the old 50-message
        #    chat_history.json cap is gone.
        conversations.append(item)

        # 4. Best-effort Brain growth through the unified pipeline.
        if enable_graph and knowledge_graph and ingestion_pipeline and ingestion_item_factory:
            try:
                ingestion_pipeline.ingest(
                    ingestion_item_factory(
                        source_type="chat_message",
                        text=message,
                        owner=user_email,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        metadata={
                            "role": role,
                            "user_nickname": user_nickname,
                            "source": source,
                            "raw": item,
                        },
                    ),
                    user_email=user_email,
                )
            except Exception as graph_error:  # noqa: BLE001
                # The message is already stored; a graph failure must not
                # look like a lost turn.
                logger.warning("knowledge graph message ingest failed: %s", graph_error)
    except Exception as exc:  # noqa: BLE001
        logger.warning("save_to_history failed: %s", exc)


__all__ = ["HistoryWriterDeps", "write_chat_turn"]
