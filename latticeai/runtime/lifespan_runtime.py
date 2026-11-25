from __future__ import annotations

import os
import subprocess
from contextlib import asynccontextmanager
from typing import Any, Dict

from latticeai.core.quiet import quiet
from latticeai.models.router.errors import MODEL_RUNTIME_BOUNDARY_ERRORS

_PUBLIC_PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "together": "TOGETHER_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}


async def _autoload_public_model(public_model: str, model_router: Any) -> None:
    model_id = public_model
    provider = model_id.split(":", 1)[0] if ":" in model_id else "openai"
    required_env = _PUBLIC_PROVIDER_ENV.get(provider)
    if required_env and not os.getenv(required_env) and provider != "ollama":
        print(f"🌐 Public mode ready. Set {required_env} to autoload {model_id}.")
        return
    print(f"🌐 Public mode autoload: {model_id}")
    try:
        msg = await model_router.load_model(model_id)
        print(f"✅ {msg}")
    except MODEL_RUNTIME_BOUNDARY_ERRORS as e:
        print(f"⚠️ Public model autoload failed: {e}")


async def _autoload_local_model(
    local_model: str, local_draft_model: str, model_router: Any
) -> None:
    print("⏳ Auto-loading local model stack:")
    print(f"   - Target: {local_model}")
    if local_draft_model:
        print(f"   - Draft:  {local_draft_model}")
    else:
        print("   - Draft:  disabled (set LATTICEAI_LOCAL_DRAFT_MODEL to enable)")
    try:
        await model_router.load_model(
            local_model, draft_model_id=local_draft_model or None
        )
    except MODEL_RUNTIME_BOUNDARY_ERRORS as e:
        print(f"⚠️ Local model autoload failed: {e}")


def _stop_local_server_processes(local_server_processes: Dict[str, Any]) -> None:
    for proc in local_server_processes.values():
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except (
            OSError,
            subprocess.SubprocessError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ):
            quiet()


def build_lifespan_runtime(
    *,
    app_mode: str,
    autoload_models: bool,
    is_public_mode: bool,
    public_model: str,
    allow_local_models: bool,
    local_model: str,
    local_draft_model: str,
    model_idle_unload_seconds: int,
    model_router: Any,
    local_server_processes: Dict[str, Any],
    logger: Any,
) -> Dict[str, Any]:

    import asyncio

    async def autoload_default_model() -> None:
        if not autoload_models:
            print("⏭️ Model autoload disabled by LATTICEAI_AUTOLOAD_MODELS=false.")
            return
        if is_public_mode:
            await _autoload_public_model(public_model, model_router)
            return
        if not allow_local_models:
            print(
                "⏭️ Local model autoload skipped because LATTICEAI_ALLOW_LOCAL_MODELS=false."
            )
            return
        await _autoload_local_model(local_model, local_draft_model, model_router)

    async def unload_idle_models_loop() -> None:
        if model_idle_unload_seconds <= 0:
            print("⏭️ Model idle unload disabled.")
            return
        while True:
            await asyncio.sleep(min(60, model_idle_unload_seconds))
            try:
                unloaded = model_router.unload_idle_models(model_idle_unload_seconds)
                if unloaded:
                    print(f"🧹 Idle model unload: {', '.join(unloaded)}")
            except (
                RuntimeError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
            ) as e:
                logger.warning("Idle model unload failed: %s", e)

    def spawn(coro, *, name: str):

        task = asyncio.create_task(coro, name=name)

        def _on_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.warning("background task '%s' failed: %s", name, exc)

        task.add_done_callback(_on_done)
        return task

    @asynccontextmanager
    async def lifespan(_app):
        try:
            print(f"🧭 Lattice AI mode: {app_mode}")
            spawn(unload_idle_models_loop(), name="unload_idle_models")
            spawn(autoload_default_model(), name="autoload_default_model")
        except (
            RuntimeError,
            OSError,
            ValueError,
            TypeError,
            AttributeError,
        ) as e:
            print(f"⚠️ Startup sequence failed: {e}")
        try:
            yield
        finally:
            model_router.unload_all()
            _stop_local_server_processes(local_server_processes)

    return {
        "autoload_default_model": autoload_default_model,
        "unload_idle_models_loop": unload_idle_models_loop,
        "_spawn": spawn,
        "lifespan": lifespan,
    }
