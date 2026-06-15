"""FastAPI lifespan assembly for app startup and shutdown tasks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict


def build_lifespan_runtime(
    *,
    app_mode: str,
    enable_telegram: bool,
    autoload_models: bool,
    is_public_mode: bool,
    public_model: str,
    allow_local_models: bool,
    local_model: str,
    local_draft_model: str,
    model_idle_unload_seconds: int,
    model_router: Any,
    local_kg_watcher: Any,
    local_server_processes: Dict[str, Any],
    logger: Any,
) -> Dict[str, Any]:
    """Create lifespan and background task helpers for the FastAPI app."""

    import asyncio
    import os

    async def autoload_default_model() -> None:
        if not autoload_models:
            print("⏭️ Model autoload disabled by LATTICEAI_AUTOLOAD_MODELS=false.")
            return

        if is_public_mode:
            model_id = public_model
            provider = model_id.split(":", 1)[0] if ":" in model_id else "openai"
            env_by_provider = {
                "openai": "OPENAI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "groq": "GROQ_API_KEY",
                "together": "TOGETHER_API_KEY",
                "ollama": "OLLAMA_API_KEY",
            }
            required_env = env_by_provider.get(provider)
            if required_env and not os.getenv(required_env) and provider != "ollama":
                print(f"🌐 Public mode ready. Set {required_env} to autoload {model_id}.")
                return
            print(f"🌐 Public mode autoload: {model_id}")
            try:
                msg = await model_router.load_model(model_id)
                print(f"✅ {msg}")
            except Exception as e:  # pragma: no cover - startup diagnostics
                print(f"⚠️ Public model autoload failed: {e}")
            return

        if not allow_local_models:
            print("⏭️ Local model autoload skipped because LATTICEAI_ALLOW_LOCAL_MODELS=false.")
            return

        print("⏳ Auto-loading local model stack:")
        print(f"   - Target: {local_model}")
        if local_draft_model:
            print(f"   - Draft:  {local_draft_model}")
        else:
            print("   - Draft:  disabled (set LATTICEAI_LOCAL_DRAFT_MODEL to enable)")
        try:
            await model_router.load_model(local_model, draft_model_id=local_draft_model or None)
        except Exception as e:  # pragma: no cover - startup diagnostics
            print(f"⚠️ Local model autoload failed: {e}")

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
            except Exception as e:  # pragma: no cover - background diagnostics
                logger.warning("Idle model unload failed: %s", e)

    def spawn(coro, *, name: str):
        """Fire-and-forget asyncio task that logs exceptions instead of swallowing them."""

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
            if enable_telegram:
                from telegram_bot import run_bot

                spawn(run_bot(), name="telegram_bot")
                print("🚀 Telegram Bot Bridge activated!")
            else:
                print("⏭️ Telegram Bot Bridge disabled for this mode.")
            spawn(unload_idle_models_loop(), name="unload_idle_models")
            spawn(autoload_default_model(), name="autoload_default_model")
            if local_kg_watcher:
                restored = local_kg_watcher.restore_enabled_sources()
                if restored.get("restored"):
                    print(f"🕸️ Local knowledge watchers restored: {restored['restored']}")
        except Exception as e:  # pragma: no cover - startup diagnostics
            print(f"⚠️ Startup sequence failed: {e}")
        try:
            yield
        finally:
            if local_kg_watcher:
                local_kg_watcher.stop_all()
            model_router.unload_all()
            for proc in local_server_processes.values():
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                except Exception:
                    pass

    return {
        "autoload_default_model": autoload_default_model,
        "unload_idle_models_loop": unload_idle_models_loop,
        "_spawn": spawn,
        "lifespan": lifespan,
    }
