"""OpenAI-compatible streaming adapter for hybrid cloud turns (Phase 1).

Uses the already-declared ``openai`` dependency. Configuration is entirely
environment-driven so no secrets land in the repo:

* ``LATTICEAI_CLOUD_API_KEY`` (required to actually call)
* ``LATTICEAI_CLOUD_BASE_URL`` (optional; default OpenAI)
* ``LATTICEAI_CLOUD_MODEL`` (optional; default gpt-4o-mini)

When the API key is missing the adapter raises a clear error so the bridge
can surface an honest message instead of silently failing.
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Optional


class OpenAICompatibleAdapter:
    """Minimal Chat Completions streaming adapter."""

    provider_name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
    ) -> None:
        self.api_key = (api_key or os.environ.get("LATTICEAI_CLOUD_API_KEY") or "").strip()
        self.base_url = (
            base_url or os.environ.get("LATTICEAI_CLOUD_BASE_URL") or ""
        ).strip() or None
        self.default_model = (
            default_model
            or os.environ.get("LATTICEAI_CLOUD_MODEL")
            or "gpt-4o-mini"
        ).strip()

    def _client(self):
        if not self.api_key:
            raise RuntimeError(
                "Cloud adapter is not configured. Set LATTICEAI_CLOUD_API_KEY "
                "(and optionally LATTICEAI_CLOUD_BASE_URL / LATTICEAI_CLOUD_MODEL)."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "openai package is required for the cloud streaming adapter"
            ) from exc
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return AsyncOpenAI(**kwargs)

    async def stream(
        self,
        *,
        system: str,
        user: str,
        context: str,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        client = self._client()
        chosen = (model or self.default_model).strip()
        messages = [
            {"role": "system", "content": system},
        ]
        if context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Local Knowledge Graph context (minimal related nodes only):\n"
                        f"{context}"
                    ),
                }
            )
        messages.append({"role": "user", "content": user})

        stream = await client.chat.completions.create(
            model=chosen,
            messages=messages,
            stream=True,
            temperature=0.2,
        )
        async for event in stream:
            try:
                delta = event.choices[0].delta
                piece = getattr(delta, "content", None) or ""
            except Exception:  # noqa: BLE001
                piece = ""
            if piece:
                yield piece


__all__ = ["OpenAICompatibleAdapter"]
