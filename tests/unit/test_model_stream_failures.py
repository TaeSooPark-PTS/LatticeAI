"""`ModelStreamError` — a backend failure is an error, never the model's answer.

Every streaming backend used to hand its own failure to the caller as a chunk
of text (``"⚠️ Error: ..."``). A chunk is indistinguishable from generated
content, so the SSE endpoints echoed the failure to the browser *as the
assistant's reply* and then persisted it as a successful turn: a crashed
backend produced a saved "answer" the user could not tell apart from a real
one, and no consumer could branch on failure because there was nothing to
branch on.

The failure now travels as a typed exception. These tests pin the invariant
from both ends:

* the producer never puts the warning text on the wire (queue or yield), and
* the only way for a consumer to observe the failure is to catch it —
  while output produced *before* the failure is still delivered.

The last two tests are the regression fence: they read every module of the
``latticeai/models/router/`` package and fail if a future edit reintroduces a
warning string as stream content, in any formatting.

`document_output_target` (``latticeai/tools/documents.py``) is covered here
too — it had no test anywhere in the suite, and the governance overwrite
guard in ``services/tool_dispatch.py`` and ``core/agent/`` is only
fail-closed if this helper reports the path the creators actually write.
"""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from pathlib import Path

import pytest

from latticeai import tools
from latticeai.models import router as router_mod
from latticeai.tools import documents

# Every module of the router package: the entry point alone would leave the
# streaming halves unguarded after the v11.3.0 split.
ROUTER_SOURCES = sorted(Path(router_mod.__file__).resolve().parent.glob("*.py"))

# Real work is faked; nothing here should ever approach these ceilings. They
# exist so a broken terminator fails the suite instead of hanging it.
STREAM_TIMEOUT = 10.0


class _BackendExploded(Exception):
    """A backend-specific failure.

    Deliberately *not* a `RuntimeError`: `ModelStreamError` is one, so an
    identity/type assertion on `__cause__` would pass for the wrong reason if
    the fake raised a `RuntimeError`.
    """


class _FakeChunk:
    """The mlx generation-result shape: the router reads ``.text``."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeTokenizer:
    def apply_chat_template(self, _messages, tokenize=False, add_generation_prompt=True):
        return "prompt"


def _install_fake_mlx(monkeypatch: pytest.MonkeyPatch, stream_generate) -> None:
    """Point the worker thread's imports at fakes.

    ``_stream`` imports ``mlx.core`` and ``mlx_lm`` *inside* the thread body,
    so the fakes have to live in ``sys.modules``. This keeps the test about
    the router's failure transport rather than a real GPU, and keeps it
    runnable where MLX is not installed at all.
    """
    fake_core = types.ModuleType("mlx.core")
    fake_core.gpu = object()
    fake_core.set_default_device = lambda _device: None
    fake_mlx = types.ModuleType("mlx")
    fake_mlx.core = fake_core
    fake_lm = types.ModuleType("mlx_lm")
    fake_lm.stream_generate = stream_generate

    monkeypatch.setitem(sys.modules, "mlx", fake_mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", fake_core)
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_lm)


def _local_router() -> router_mod.LLMRouter:
    """A router holding one loaded local model (the `mlx_lm` text path)."""
    router = router_mod.LLMRouter()
    router._cache = {"local-test": (object(), _FakeTokenizer(), None, "mlx_lm")}
    router._current = "local-test"
    return router


def _failing_cloud_client(error: BaseException):
    async def _create(**_kwargs):
        raise error

    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=_create))
    )


def _cloud_model(error: BaseException, provider: str = "test") -> router_mod.CloudModel:
    return router_mod.CloudModel(
        provider=provider,
        model="cloud-test",
        client=_failing_cloud_client(error),
        cache_key="cloud-test",
    )


def _cloud_router(error: BaseException, provider: str = "test") -> router_mod.LLMRouter:
    router = router_mod.LLMRouter()
    router._cache = {"cloud-test": _cloud_model(error, provider)}
    router._current = "cloud-test"
    return router


def _consume(stream_factory, chunks: list[str], *, timeout: float = STREAM_TIMEOUT) -> None:
    """Drive ``stream_factory()`` to exhaustion, appending everything yielded.

    Returns normally on a clean stream and lets the failure propagate, so a
    caller can wrap this in ``pytest.raises`` and still inspect exactly what
    the consumer received before the stream broke.
    """

    async def _scenario() -> None:
        async def _drain() -> None:
            async for chunk in stream_factory():
                chunks.append(chunk)

        await asyncio.wait_for(_drain(), timeout)

    asyncio.run(_scenario())


def _drain_queue(items: list, chunks: list[str] | None = None) -> list[str]:
    """Feed ``items`` to `_drain_stream_queue` and return what a consumer saw."""
    collected = chunks if chunks is not None else []

    async def _scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        for item in items:
            queue.put_nowait(item)

        async def _consume_queue() -> None:
            async for chunk in router_mod.LLMRouter._drain_stream_queue(queue):
                collected.append(chunk)

        await asyncio.wait_for(_consume_queue(), STREAM_TIMEOUT)

    asyncio.run(_scenario())
    return collected


# ── _drain_stream_queue: the single seam every MLX stream passes through ──


def test_drain_yields_chunks_until_the_none_terminator():
    # The trailing item proves the drain *stops* at None rather than reading
    # whatever a later stream left behind on the same queue.
    assert _drain_queue(["첫 ", "번째", None, "after-terminator"]) == ["첫 ", "번째"]


def test_drain_normalizes_branding_on_every_chunk():
    assert _drain_queue(["Connect AI ", "입니다", None]) == ["Lattice AI ", "입니다"]


def test_drain_treats_only_none_as_the_terminator():
    """An empty token is content, not the end of the stream.

    The check is `is None`; a truthiness test here would silently truncate
    every answer at its first empty delta.
    """
    assert _drain_queue(["a", "", "b", None]) == ["a", "", "b"]


def test_drain_raises_a_queued_stream_error_instead_of_yielding_it():
    error = router_mod.ModelStreamError("MLX chat stream failed: backend down")
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError) as excinfo:
        _drain_queue([error, None], chunks)

    assert chunks == [], "a failure must never reach the caller as content"
    assert excinfo.value is error, "the original error object must survive the hand-off"


def test_drain_yields_partial_output_before_raising():
    """Tokens the model really produced are not discarded by a later failure."""
    error = router_mod.ModelStreamError("MLX chat stream failed: backend down")
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError):
        _drain_queue(["부분 ", "출력", error, None], chunks)

    assert chunks == ["부분 ", "출력"]
    assert not any("backend down" in chunk for chunk in chunks)
    assert not any("⚠️" in chunk for chunk in chunks)


# ── MLX chat stream: the worker thread cannot raise, so it enqueues ───────


def test_mlx_chat_stream_failure_reaches_the_consumer_as_an_exception(monkeypatch):
    boom = _BackendExploded("mlx generator died")

    def fake_stream_generate(*_args, **_kwargs):
        yield _FakeChunk("안녕")
        yield _FakeChunk("하세요")
        raise boom

    _install_fake_mlx(monkeypatch, fake_stream_generate)
    router = _local_router()
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError) as excinfo:
        _consume(lambda: router.stream_generate("질문"), chunks)

    assert chunks == ["안녕", "하세요"], "tokens produced before the failure still count"
    assert not any("⚠️ Error" in chunk for chunk in chunks)
    assert "MLX chat stream failed" in str(excinfo.value)
    assert excinfo.value.__cause__ is boom


def test_mlx_chat_stream_that_fails_before_any_token_yields_nothing(monkeypatch):
    boom = _BackendExploded("model unloaded mid-request")

    def fake_stream_generate(*_args, **_kwargs):
        raise boom
        yield  # pragma: no cover — keeps this a generator function

    _install_fake_mlx(monkeypatch, fake_stream_generate)
    router = _local_router()
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError) as excinfo:
        _consume(lambda: router.stream_generate("질문"), chunks)

    assert chunks == [], "a failed stream must not produce a single character of 'answer'"
    assert excinfo.value.__cause__ is boom


# ── MLX document stream: same seam, different stage label ────────────────


def test_mlx_document_stream_failure_reaches_the_consumer_as_an_exception(monkeypatch):
    boom = _BackendExploded("document generator died")

    def fake_stream_generate(*_args, **_kwargs):
        yield _FakeChunk("# 보고서")
        raise boom

    _install_fake_mlx(monkeypatch, fake_stream_generate)
    router = _local_router()
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError) as excinfo:
        _consume(lambda: router.stream_generate_document("질문", "system"), chunks)

    assert chunks == ["# 보고서"]
    assert not any("⚠️ Error" in chunk for chunk in chunks)
    assert "MLX document stream failed" in str(excinfo.value)
    assert excinfo.value.__cause__ is boom


# ── Cloud streams: the client call fails before a single token ───────────


def test_cloud_chat_stream_raises_instead_of_yielding_a_warning_chunk():
    boom = _BackendExploded("Connection refused")
    router = _cloud_router(boom)
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError) as excinfo:
        _consume(lambda: router.stream_generate("질문"), chunks)

    assert chunks == []
    assert excinfo.value.__cause__ is boom
    assert "Connection refused" in str(excinfo.value)
    assert "⚠️" not in str(excinfo.value)


def test_cloud_chat_stream_failure_keeps_the_operator_hint_in_the_exception():
    """The LM Studio hint was the payload of the old warning chunk.

    It is guidance about a broken local server, so it must still reach the
    operator — through the error channel, not as the assistant's reply.
    """
    router = _cloud_router(_BackendExploded("Connection error."), provider="lmstudio")
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError) as excinfo:
        _consume(lambda: router.stream_generate("질문"), chunks)

    assert chunks == []
    assert "LM Studio 연결 실패" in str(excinfo.value)


def test_cloud_document_stream_raises_instead_of_yielding_a_warning_chunk():
    boom = _BackendExploded("gateway timeout")
    router = router_mod.LLMRouter()
    cloud = _cloud_model(boom)
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError) as excinfo:
        _consume(
            lambda: router._cloud_stream_document(cloud, "질문", "system", 128, 0.3),
            chunks,
        )

    assert chunks == []
    assert excinfo.value.__cause__ is boom


def test_cloud_document_stream_failure_propagates_through_the_public_api():
    """`stream_generate_document` must not swallow or re-text the failure."""
    boom = _BackendExploded("gateway timeout")
    router = _cloud_router(boom)
    chunks: list[str] = []

    with pytest.raises(router_mod.ModelStreamError):
        _consume(lambda: router.stream_generate_document("질문", "system"), chunks)

    assert chunks == []


# ── _stream_failure: the envelope carried across the thread boundary ──────


def test_stream_failure_prefixes_the_stage_and_attaches_the_cause():
    boom = _BackendExploded("out of memory")

    error = router_mod._stream_failure("MLX chat stream failed", boom)

    assert isinstance(error, router_mod.ModelStreamError)
    assert str(error) == "MLX chat stream failed: out of memory"
    assert error.__cause__ is boom


def test_stream_failure_is_an_error_not_a_chunk():
    """Shape checks the consumers depend on.

    `ModelStreamError` subclasses `RuntimeError`, so the `except Exception`
    handlers already wrapping both SSE endpoints keep catching it; and the
    message carries no warning glyph, because it is never rendered as content.
    """
    error = router_mod._stream_failure("MLX document stream failed", ValueError("bad"))

    assert issubclass(router_mod.ModelStreamError, RuntimeError)
    assert isinstance(error, Exception) and not isinstance(error, str)
    assert "⚠️" not in str(error)


# ── Regression fence: the text-as-answer path must not come back ──────────


def _router_trees() -> list[ast.Module]:
    """Every module of the router package.

    v11.3.0 turned the router into a package, so a fence that parsed only the
    entry point would quietly stop guarding the two streaming halves — which
    are exactly the code this fence exists for.
    """
    return [
        ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(ROUTER_SOURCES)
    ]


def _executable_string_literals(tree: ast.Module) -> list[str]:
    """Every string the module *runs*, docstrings excluded.

    The `ModelStreamError` docstring quotes the old `"⚠️ Error: ..."` text on
    purpose, so a plain substring scan of the file would fail for the wrong
    reason. f-string pieces are `Constant` nodes inside `JoinedStr`, so they
    are collected here too — the reverted code was an f-string.
    """
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_router_no_longer_builds_the_error_as_stream_text():
    offenders = [
        literal
        for tree in _router_trees()
        for literal in _executable_string_literals(tree)
        if "⚠️ Error" in literal
    ]

    assert offenders == [], (
        "a backend failure must be raised as ModelStreamError, not formatted "
        f"into a chunk of text: {offenders}"
    )


def test_no_router_stream_yields_a_warning_string():
    yields = [
        ast.unparse(node)
        for tree in _router_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Yield) and node.value is not None
    ]

    assert yields, "sanity: the router still has streaming generators"
    offenders = [text for text in yields if "⚠️" in text]
    assert offenders == [], f"failures must be raised, never yielded: {offenders}"


# ── document_output_target: where a creator actually writes ───────────────


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("create_docx", "generated_documents/report.docx"),
        ("create_xlsx", "generated_spreadsheets/report.xlsx"),
        ("create_pptx", "generated_presentations/report.pptx"),
        ("create_pdf", "generated_pdfs/report.pdf"),
    ],
)
def test_document_output_target_maps_each_creator_to_its_directory(tool_name, expected):
    # A bare name also proves the suffix is enforced, not merely accepted.
    assert documents.document_output_target(tool_name, "report") == expected


def test_document_output_target_appends_the_enforced_suffix_to_a_wrong_one():
    """The creator writes a .docx no matter what extension the caller asked
    for, so the reported target has to say .docx too."""
    assert (
        documents.document_output_target("create_docx", "notes.txt")
        == "generated_documents/notes.txt.docx"
    )


def test_document_output_target_is_none_for_tools_that_write_anywhere():
    """`None` means "no enforced target" — callers fall back to the raw path."""
    assert documents.document_output_target("write_file", "notes.md") is None
    assert documents.document_output_target("", "notes.md") is None


def test_document_output_target_falls_back_when_no_filename_is_given():
    assert (
        documents.document_output_target("create_docx", "")
        == "generated_documents/artifact.docx"
    )


def test_document_output_target_sanitizes_a_traversal_filename(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    hostile = "../../etc/passwd"

    target = documents.document_output_target("create_docx", hostile)

    assert target == "generated_documents/passwd.docx"
    # The raw argument escapes the workspace; the reported target cannot.
    with pytest.raises(tools.ToolError):
        tools._resolve_path(hostile)
    resolved = tools._resolve_path(target)
    assert resolved == root / "generated_documents" / "passwd.docx"
    assert "etc" not in resolved.parts


def test_document_output_target_neutralizes_windows_separators():
    """`PurePosixPath.name` does not split on backslashes, so the character
    sanitizer is the only thing standing between a Windows-style traversal
    and a nested write."""
    target = documents.document_output_target("create_docx", "..\\..\\windows\\sys.ini")

    assert target == "generated_documents/.._.._windows_sys.ini.docx"
    assert target.count("/") == 1
