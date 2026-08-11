"""Cloud model verification — does this key actually answer for this model?

A one-token chat completion per configured cloud model, cached per service
instance for :data:`CLOUD_VERIFY_TTL_SECONDS`. A model the router already knows
is unavailable is recorded as such without a network call.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional

from latticeai.models.router import (
    OPENAI_COMPATIBLE_PROVIDERS,
    AsyncOpenAI,
    parse_model_ref,
)
from latticeai.services.model_runtime.state import ModelRuntimeState

CLOUD_VERIFY_TTL_SECONDS = 600

async def _probe_cloud_model(model_ref: str) -> Dict[str, Any]:
    provider, model_name = parse_model_ref(model_ref)
    config = OPENAI_COMPATIBLE_PROVIDERS.get(provider)
    if not config:
        return {"ok": False, "reason": f"Unsupported provider: {provider}"}

    api_key = os.getenv(config["env_key"]) or config.get("api_key_fallback")
    if not api_key:
        return {"ok": False, "reason": f"Missing API key: {config['env_key']}"}

    base_url = os.getenv(config.get("base_url_env", "")) if config.get("base_url_env") else None
    base_url = base_url or config.get("base_url")
    try:
        # base_url is passed only when configured: an explicit None is not
        # the same as omitting the argument.
        client = (
            AsyncOpenAI(api_key=api_key, base_url=base_url)
            if base_url
            else AsyncOpenAI(api_key=api_key)
        )
        await asyncio.wait_for(
            client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            ),
            timeout=15,
        )
        return {"ok": True, "reason": "ok"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:220]}


async def verify_cloud_models(
    force: bool = False,
    provider_filter: Optional[str] = None,
    *,
    state: ModelRuntimeState,
    cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict]:
    now = time.time()
    r = state.router
    cloud_items = [item for item in (r.detected_cloud_models() if r else []) if item.get("tag") == "cloud"]
    if provider_filter:
        cloud_items = [item for item in cloud_items if item.get("provider") == provider_filter]

    results: Dict[str, Dict] = {}
    for item in cloud_items:
        model_ref = item["id"]
        cached = cache.get(model_ref)
        if not force and cached and (now - cached.get("ts", 0) <= CLOUD_VERIFY_TTL_SECONDS):
            results[model_ref] = cached
            continue
        if item.get("available") is False:
            record = {"ok": False, "reason": item.get("requires") or "API key missing", "ts": now}
            cache[model_ref] = record
            results[model_ref] = record
            continue
        probe = await _probe_cloud_model(model_ref)
        record = {"ok": bool(probe.get("ok")), "reason": probe.get("reason", ""), "ts": now}
        cache[model_ref] = record
        results[model_ref] = record
    return results
