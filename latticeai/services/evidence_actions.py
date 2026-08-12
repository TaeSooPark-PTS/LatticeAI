"""Evidence → action bridge (v9.9.6).

Answers already carry citations and a grounding badge, and clicking a source
already opens the stored chunk. The loop stopped there: to *use* the evidence
the user had to retype the request and hope the model retrieved the same
sources again.

This service closes that gap deterministically. Given the citations an answer
actually used, it resolves them against the graph and composes ready-to-send,
evidence-scoped prompts — "이 근거로 요약 만들기", "체크리스트 만들기",
"문서 파일 만들기", "한 페이지로 만들기". No model runs here: the output is a
prompt plus the resolved evidence, so the existing chat/file-generation path
stays the single execution road.

Honesty rules:

* Citations that no longer resolve are reported in ``missing`` — never
  silently dropped, and never invented.
* When nothing resolves, ``actions`` is empty and ``reason`` says why. The UI
  must not offer an action the Brain cannot ground.
* Every composed prompt tells the model to use only the quoted evidence and
  to say so when the evidence does not cover the request.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from latticeai.core.messages import bilingual

LOGGER = logging.getLogger(__name__)

__all__ = ["EvidenceActionService", "EVIDENCE_ACTIONS"]

# Per-source excerpt cap. Long enough to carry the claim, short enough that a
# four-citation prompt still fits a small local model's context.
_EXCERPT_CHARS = 600
_MAX_SOURCES = 8



#: Historical module-local name for the shared phrase-pair helper.
_phrase = bilingual


# The action catalog is closed and deterministic — the UI renders exactly
# these, and each one names the artifact it produces.
EVIDENCE_ACTIONS: List[Dict[str, Any]] = [
    {
        "id": "summary",
        "label": _phrase("이 근거로 요약 만들기", "Summarize from this evidence"),
        "instruction": _phrase(
            "핵심만 5줄 이내로 요약해 주세요.",
            "Summarize the key points in five lines or fewer.",
        ),
        "kind": "chat",
    },
    {
        "id": "checklist",
        "label": _phrase("이 근거로 체크리스트 만들기", "Build a checklist"),
        "instruction": _phrase(
            "실행 가능한 체크리스트를 만들어 주세요. 각 항목은 한 줄이고, 근거가 있는 항목만 넣으세요.",
            "Build an actionable checklist. One line per item, only items the evidence supports.",
        ),
        "kind": "chat",
    },
    {
        "id": "document",
        "label": _phrase("이 근거로 문서 파일 만들기", "Write a document file"),
        "instruction": _phrase(
            "정리된 마크다운 문서를 만들어 {path} 파일로 저장해 주세요.",
            "Write a structured markdown document and save it as {path}.",
        ),
        "kind": "file",
        "extension": ".md",
    },
    {
        "id": "page",
        "label": _phrase("이 근거로 한 페이지 만들기", "Build a one-page view"),
        "instruction": _phrase(
            "내용을 한눈에 보는 HTML 한 페이지로 만들어 {path} 파일로 저장해 주세요.",
            "Build a single self-contained HTML page and save it as {path}.",
        ),
        "kind": "file",
        "extension": ".html",
    },
]

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "evidence-note") -> str:
    """Deterministic, filesystem-safe stem for a suggested artifact path.

    Non-ASCII text (Korean questions are the common case) leaves nothing to
    slug — the fallback keeps the suggestion predictable instead of producing
    a mangled or empty filename.
    """
    slug = _SLUG_STRIP_RE.sub("-", str(text or "").lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)[:48].strip("-")
    return slug or fallback


class EvidenceActionService:
    """Compose evidence-scoped follow-up actions for an answer's citations."""

    def __init__(
        self,
        *,
        node_reader: Optional[Callable[..., Mapping[str, Any]]] = None,
        excerpt_chars: int = _EXCERPT_CHARS,
    ) -> None:
        self._node_reader = node_reader
        self._excerpt_chars = max(120, int(excerpt_chars))

    # ── evidence resolution ──────────────────────────────────────────────

    def _read_node(self, node_id: str, allowed_workspaces: Any) -> Optional[Mapping[str, Any]]:
        if self._node_reader is None:
            return None
        try:
            return self._node_reader(node_id, allowed_workspaces=allowed_workspaces)
        except TypeError:
            # Readers without scope support (tests, older ports).
            try:
                return self._node_reader(node_id)
            except Exception as exc:  # noqa: BLE001 — resolution is best-effort
                LOGGER.debug("evidence node read failed for %s: %s", node_id, exc)
                return None
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("evidence node read failed for %s: %s", node_id, exc)
            return None

    def resolve(
        self,
        source_ids: Sequence[str],
        *,
        allowed_workspaces: Any = None,
    ) -> Dict[str, Any]:
        """Resolve citation ids to ``{"sources": [...], "missing": [...]}``."""
        sources: List[Dict[str, Any]] = []
        missing: List[str] = []
        seen: set = set()
        for raw in list(source_ids or [])[:_MAX_SOURCES]:
            node_id = str(raw or "").strip()
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            node = self._read_node(node_id, allowed_workspaces)
            if not isinstance(node, Mapping):
                missing.append(node_id)
                continue
            # /api/graph/node wraps the record; get_node returns it directly.
            wrapped = node.get("node")
            record: Mapping[str, Any] = wrapped if isinstance(wrapped, Mapping) else node
            title = str(record.get("title") or record.get("id") or node_id)
            body = str(record.get("summary") or record.get("content") or "").strip()
            metadata = record.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            origin = ""
            for key in ("relative_path", "file_path", "filename", "source_uri", "source"):
                value = metadata.get(key)
                if value:
                    origin = str(value)
                    break
            sources.append({
                "id": node_id,
                "title": title,
                "type": str(record.get("type") or ""),
                "origin": origin,
                "excerpt": body[: self._excerpt_chars],
                "truncated": len(body) > self._excerpt_chars,
            })
        return {"sources": sources, "missing": missing}

    # ── prompt composition ───────────────────────────────────────────────

    def _evidence_block(self, sources: Sequence[Mapping[str, Any]], language: str) -> str:
        header = "[근거 자료]" if language == "ko" else "[EVIDENCE]"
        lines = [header]
        for index, source in enumerate(sources, start=1):
            origin = f" ({source['origin']})" if source.get("origin") else ""
            lines.append(f"{index}. {source['title']}{origin}")
            excerpt = str(source.get("excerpt") or "").strip()
            if excerpt:
                suffix = " …" if source.get("truncated") else ""
                lines.append(f"   {excerpt}{suffix}")
        return "\n".join(lines)

    def _guard(self, language: str) -> str:
        if language == "ko":
            return (
                "위 근거 자료에 있는 내용만 사용하세요. 근거에 없는 사실은 지어내지 말고, "
                "근거가 부족하면 '이 부분은 근거가 없습니다'라고 적으세요."
            )
        return (
            "Use only the evidence quoted above. Do not invent facts that are not in it; "
            "when the evidence does not cover something, say so explicitly."
        )

    def actions_for(
        self,
        *,
        question: str = "",
        source_ids: Sequence[str] = (),
        language: str = "ko",
        allowed_workspaces: Any = None,
    ) -> Dict[str, Any]:
        """Ready-to-send, evidence-scoped follow-up actions for one answer."""
        language = "ko" if str(language or "ko").lower().startswith("ko") else "en"
        resolved = self.resolve(source_ids, allowed_workspaces=allowed_workspaces)
        sources = resolved["sources"]
        question_text = str(question or "").strip()
        if not sources:
            return {
                "sources": [],
                "missing": resolved["missing"],
                "actions": [],
                "reason": (
                    "근거로 쓸 출처를 찾지 못했습니다."
                    if language == "ko"
                    else "No usable evidence could be resolved."
                ),
            }

        evidence = self._evidence_block(sources, language)
        guard = self._guard(language)
        stem = slugify(question_text)
        actions: List[Dict[str, Any]] = []
        for spec in EVIDENCE_ACTIONS:
            path = f"{stem}{spec['extension']}" if spec.get("extension") else ""
            instruction = spec["instruction"][language].replace("{path}", path)
            question_line = (
                (f"원래 질문: {question_text}" if language == "ko" else f"Original question: {question_text}")
                if question_text
                else ""
            )
            prompt = "\n\n".join(
                part for part in (evidence, instruction, guard, question_line) if part
            )
            action: Dict[str, Any] = {
                "id": spec["id"],
                "kind": spec["kind"],
                "label": dict(spec["label"]),
                "prompt": prompt,
                "source_ids": [source["id"] for source in sources],
            }
            if path:
                action["suggested_path"] = path
            actions.append(action)
        return {
            "sources": sources,
            "missing": resolved["missing"],
            "actions": actions,
            "reason": "",
        }
