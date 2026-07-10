"""Concurrent model selection must be request-scoped, never process-global."""

import asyncio

from latticeai.models import router as router_mod


def _cloud(name: str):
    # Resolve the class from the live module because the MLX import contract
    # tests intentionally reload this module during the full suite.
    return router_mod.CloudModel(provider="test", model=name, client=object(), cache_key=name)


def test_generate_as_does_not_mutate_default_model_during_concurrency() -> None:
    async def scenario() -> None:
        router = router_mod.LLMRouter()
        router._cache = {name: _cloud(name) for name in ("default", "model-b", "model-c")}
        router._current = "default"
        started = {name: asyncio.Event() for name in ("model-b", "model-c")}
        release = {name: asyncio.Event() for name in ("model-b", "model-c")}

        async def fake_generate(cloud, *_args, **_kwargs):
            started[cloud.model].set()
            await release[cloud.model].wait()
            return cloud.model

        router._cloud_generate = fake_generate

        task_b = asyncio.create_task(router.generate_as("model-b", "hello"))
        task_c = asyncio.create_task(router.generate_as("model-c", "hello"))
        await asyncio.wait_for(
            asyncio.gather(started["model-b"].wait(), started["model-c"].wait()),
            timeout=2,
        )

        assert router.current_model_id == "default"
        release["model-b"].set()
        assert await task_b == "model-b"
        assert router.current_model_id == "default"
        release["model-c"].set()
        assert await task_c == "model-c"
        assert router.current_model_id == "default"

    asyncio.run(scenario())


def test_stream_generate_as_uses_requested_snapshot() -> None:
    async def scenario() -> None:
        router = router_mod.LLMRouter()
        router._cache = {name: _cloud(name) for name in ("default", "requested")}
        router._current = "default"

        async def fake_stream(cloud, *_args, **_kwargs):
            yield cloud.model

        router._cloud_stream_generate = fake_stream

        chunks = [chunk async for chunk in router.stream_generate_as("requested", "hello")]

        assert chunks == ["requested"]
        assert router.current_model_id == "default"

    asyncio.run(scenario())


def test_requesting_unloaded_model_fails_without_fallback() -> None:
    async def scenario() -> None:
        router = router_mod.LLMRouter()
        router._cache = {"default": _cloud("default")}
        router._current = "default"

        try:
            await router.generate_as("missing", "hello")
        except ValueError as exc:
            assert "missing" in str(exc)
        else:
            raise AssertionError("an unloaded explicit model must not use the default")

        assert router.current_model_id == "default"

    asyncio.run(scenario())
