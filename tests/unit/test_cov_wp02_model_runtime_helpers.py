"""model_runtime helper surface — the small decisions the load path is built on.

Everything here is exercised with fakes injected into the module's own
namespace (``_patch_runtime(monkeypatch, ...)``) or into
``sys.modules``, so no engine binary, MLX wheel, HTTP server or subprocess is
required. That matters twice over: the CI coverage leg is ubuntu, where the
Apple-Silicon runtimes this module drives do not exist at all, and a test that
depended on one of them would be a coin flip rather than a gate.
"""

from __future__ import annotations

import types
import urllib.error
import urllib.request

import pytest

from latticeai.services import model_runtime
from latticeai.services.model_runtime import cloud as mr_cloud
from latticeai.services.model_runtime import download as mr_download
from latticeai.services.model_runtime import engines as mr_engines
from latticeai.services.model_runtime import loading as mr_loading
from latticeai.services.model_runtime import service as mr_service
from latticeai.services.model_runtime import state as mr_state
from latticeai.services.model_runtime import status as mr_status

# ── v11.3.0 split shim ────────────────────────────────────────────────────────
# ``latticeai/services/model_runtime.py`` became a package (state / engines /
# download / status / loading / cloud / service). Reading a name through the
# package still works, so the calls below are unchanged — but *patching* a name
# on the package ``__init__`` does not reach a submodule's own global. Every
# stub is therefore installed on every module that binds the name, which is
# exactly the one binding the single-file module used to have.
_RUNTIME_MODULES = (
    model_runtime,
    mr_cloud,
    mr_download,
    mr_engines,
    mr_loading,
    mr_service,
    mr_state,
    mr_status,
)


def _patch_runtime(monkeypatch, name, value):
    targets = [module for module in _RUNTIME_MODULES if hasattr(module, name)]
    assert targets, f"no model_runtime module binds {name!r}"
    for module in targets:
        monkeypatch.setattr(module, name, value)
from latticeai.services.model_errors import ModelRuntimeError


class _Response:
    """The minimal ``urlopen`` result ``_json_request`` consumes."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _install_fake_urlopen(monkeypatch, urlopen) -> None:
    """Replace only ``model_runtime``'s view of urllib, never the stdlib's."""
    _patch_runtime(
        monkeypatch,
        "urllib",
        types.SimpleNamespace(
            request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=urlopen),
            error=urllib.error,
        ),
    )


def _fake_time(monkeypatch, *, now: float = 1_000.0) -> None:
    _patch_runtime(
        monkeypatch,
        "time",
        types.SimpleNamespace(time=lambda: now, monotonic=lambda: now, sleep=lambda _s: None),
    )


# ── the "nothing is wired yet" defaults ──────────────────────────────────────


def test_unconfigured_state_resolves_no_user_and_no_api_key():
    """A default ModelRuntimeState must answer "nobody", not raise or guess."""
    state = model_runtime.ModelRuntimeState()

    assert state.get_current_user(object()) is None
    assert state.get_user_api_key("someone@example.com", "openai") is None


def test_engine_install_block_is_a_409_with_the_capability_named():
    with pytest.raises(ModelRuntimeError) as err:
        model_runtime._engine_install_block("ollama")

    assert err.value.status_code == 409
    detail = err.value.detail
    assert isinstance(detail, dict)
    assert detail["capability"] == "engine_install"
    assert detail["engine"] == "ollama"
    assert detail["status"] == "unavailable"


# ── _update_env_file ─────────────────────────────────────────────────────────


def test_update_env_file_replaces_the_key_and_keeps_every_other_line(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=keep\nLATTICEAI_KEY=old\nTRAILING=1\n", encoding="utf-8")

    model_runtime._update_env_file(env_file, "LATTICEAI_KEY", "new")

    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "OTHER=keep",
        "LATTICEAI_KEY=new",
        "TRAILING=1",
    ]


def test_update_env_file_appends_when_the_key_is_absent(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=keep\n", encoding="utf-8")

    model_runtime._update_env_file(env_file, "NEW_KEY", "value")

    assert env_file.read_text(encoding="utf-8") == "OTHER=keep\nNEW_KEY=value\n"


def test_update_env_file_creates_the_file_when_it_does_not_exist(tmp_path):
    env_file = tmp_path / "missing" / ".env"
    env_file.parent.mkdir()

    model_runtime._update_env_file(env_file, "ONLY", "one")

    assert env_file.read_text(encoding="utf-8") == "ONLY=one\n"


# ── thin delegators to model_engines ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("public_name", "private_name", "args", "kwargs", "expected_call"),
    [
        ("windows_binary_candidates", "_windows_binary_candidates", ("ollama",), {}, (("ollama",), {})),
        ("local_binary", "_local_binary", ("ollama",), {}, (("ollama",), {})),
        ("find_lmstudio_cli", "_find_lmstudio_cli", (), {}, ((), {})),
        ("vllm_executable", "_vllm_executable", (), {}, ((), {})),
        ("vllm_metal_python", "_vllm_metal_python", (), {}, ((), {})),
        ("ensure_lmstudio_server", "_ensure_lmstudio_server", (), {}, ((), {})),
        ("engine_support_status", "_engine_support_status", ("vllm",), {}, (("vllm",), {})),
        (
            "pull_ollama_model_with_progress",
            "_pull_ollama_model_with_progress",
            ("llama3",),
            {},
            (("llama3", None), {}),
        ),
        ("get_ollama_pulled_models", "_get_ollama_pulled_models", (), {}, ((), {})),
        (
            "get_openai_compatible_server_models",
            "_get_openai_compatible_server_models",
            ("vllm",),
            {},
            (("vllm",), {}),
        ),
        ("ensure_ollama_server", "_ensure_ollama_server", (), {}, ((), {})),
        (
            "wait_for_openai_compatible_server",
            "_wait_for_openai_compatible_server",
            ("vllm",),
            {"model_name": "qwen", "timeout": 3},
            (("vllm",), {"model_name": "qwen", "timeout": 3}),
        ),
        ("ensure_vllm_server", "_ensure_vllm_server", ("qwen",), {}, (("qwen",), {})),
        ("ensure_llamacpp_server", "_ensure_llamacpp_server", ("qwen",), {}, (("qwen",), {})),
    ],
)
def test_engine_helpers_forward_their_arguments_unchanged(
    monkeypatch, public_name, private_name, args, kwargs, expected_call
):
    """These wrappers exist only to keep the historical import path alive.

    The one thing that can break is the hand-off, so that is what is asserted:
    the arguments arrive as given and the engine layer's answer comes back.
    """
    seen: list = []
    sentinel = object()

    def _record(*call_args, **call_kwargs):
        seen.append((call_args, call_kwargs))
        return sentinel

    _patch_runtime(monkeypatch, private_name, _record)

    assert getattr(model_runtime, public_name)(*args, **kwargs) is sentinel
    assert seen == [expected_call]


def test_install_engine_passes_the_applications_base_dir_not_the_process_cwd(tmp_path, monkeypatch):
    seen: list = []
    _patch_runtime(
        monkeypatch,
        "_install_engine",
        lambda engine, **kwargs: seen.append((engine, kwargs)) or {"returncode": 0},
    )
    state = model_runtime.ModelRuntimeState(BASE_DIR=tmp_path)

    result = model_runtime.install_engine("ollama", "token-123", state=state)

    assert result == {"returncode": 0}
    assert seen == [("ollama", {"confirmation_token": "token-123", "base_dir": tmp_path})]


def test_sse_event_frames_the_payload_without_escaping_non_ascii():
    frame = model_runtime.sse_event("progress", {"stage": "다운로드", "percent": 12})

    assert frame.startswith("event: progress\ndata: ")
    assert frame.endswith("\n\n")
    assert "다운로드" in frame


# ── _json_request ────────────────────────────────────────────────────────────


def test_json_request_get_parses_the_body_and_sends_no_payload(monkeypatch):
    captured: list = []

    def _urlopen(req, timeout=None):
        captured.append((req.full_url, req.get_method(), req.data, dict(req.headers), timeout))
        return _Response(b'{"models": [{"key": "a"}]}')

    _install_fake_urlopen(monkeypatch, _urlopen)

    assert model_runtime._json_request(
        "http://127.0.0.1:1234/api/v1/models",
        headers={"Authorization": "Bearer x"},
        timeout=2.5,
    ) == {"models": [{"key": "a"}]}

    url, method, data, headers, timeout = captured[0]
    assert (url, method, data, timeout) == ("http://127.0.0.1:1234/api/v1/models", "GET", None, 2.5)
    assert headers["Authorization"] == "Bearer x"
    assert "Content-type" not in headers


def test_json_request_post_encodes_json_and_sets_the_content_type(monkeypatch):
    captured: list = []

    def _urlopen(req, timeout=None):
        captured.append((req.get_method(), req.data, dict(req.headers)))
        return _Response(b'{"status": "loaded"}')

    _install_fake_urlopen(monkeypatch, _urlopen)

    assert model_runtime._json_request(
        "http://127.0.0.1:1234/api/v1/models/load",
        method="POST",
        payload={"model": "qwen"},
        timeout=5,
    ) == {"status": "loaded"}

    method, data, headers = captured[0]
    assert method == "POST"
    assert data == b'{"model": "qwen"}'
    assert headers["Content-type"] == "application/json"


def test_json_request_treats_a_blank_body_as_an_empty_object(monkeypatch):
    """A 204-style empty body is a valid answer, not a JSON decode failure."""
    _install_fake_urlopen(monkeypatch, lambda req, timeout=None: _Response(b"   \n"))

    assert model_runtime._json_request("http://127.0.0.1:1234/x") == {}


# ── LM Studio base URLs ──────────────────────────────────────────────────────


def test_lmstudio_base_urls_follow_the_env_override_and_strip_the_v1_suffix(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:9999/v1/")

    assert model_runtime.lmstudio_api_base() == "http://127.0.0.1:9999/v1"
    assert model_runtime.lmstudio_native_api_base() == "http://127.0.0.1:9999"


def test_lmstudio_base_urls_fall_back_to_the_provider_catalog(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)

    base = model_runtime.lmstudio_api_base()

    assert base == model_runtime.OPENAI_COMPATIBLE_PROVIDERS["lmstudio"]["base_url"].rstrip("/")
    assert model_runtime.lmstudio_native_api_base() == base.removesuffix("/v1")


def test_lmstudio_native_base_keeps_a_url_that_has_no_v1_suffix(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:9999")

    assert model_runtime.lmstudio_native_api_base() == "http://127.0.0.1:9999"


# ── progress payload + ETA ───────────────────────────────────────────────────


def test_progress_payload_includes_every_optional_field_that_was_supplied(monkeypatch):
    _fake_time(monkeypatch, now=1_700.0)

    payload = model_runtime.model_download_progress_payload(
        "download",
        "모델 다운로드 중입니다.",
        percent=42.349,
        detail="model.safetensors",
        downloaded_bytes=1024,
        total_bytes=4096,
        eta_seconds=12.6,
        file="model.safetensors",
        indeterminate=False,
    )

    assert payload == {
        "stage": "download",
        "message": "모델 다운로드 중입니다.",
        "indeterminate": False,
        "ts": 1_700.0,
        "percent": 42.3,
        "detail": "model.safetensors",
        "downloaded_bytes": 1024,
        "total_bytes": 4096,
        "eta_seconds": 13,
        "file": "model.safetensors",
    }


def test_progress_payload_clamps_percent_and_omits_what_was_not_given(monkeypatch):
    _fake_time(monkeypatch, now=1_700.0)

    over = model_runtime.model_download_progress_payload("download", "x", percent=140)
    under = model_runtime.model_download_progress_payload("download", "x", percent=-5)
    bare = model_runtime.model_download_progress_payload("engine", "확인 중", indeterminate=True)

    assert over["percent"] == 100
    assert under["percent"] == 0
    assert set(bare) == {"stage", "message", "indeterminate", "ts"}
    assert bare["indeterminate"] is True


def test_progress_payload_floors_negative_byte_counters(monkeypatch):
    _fake_time(monkeypatch, now=1_700.0)

    payload = model_runtime.model_download_progress_payload(
        "download", "x", downloaded_bytes=-10, total_bytes=-1, eta_seconds=-3
    )

    assert payload["downloaded_bytes"] == 0
    assert payload["total_bytes"] == 0
    assert payload["eta_seconds"] == 0


@pytest.mark.parametrize("percent", [None, 0, -1, 100, 140])
def test_eta_is_unknown_when_progress_cannot_predict_anything(monkeypatch, percent):
    _fake_time(monkeypatch, now=1_110.0)

    assert model_runtime.estimate_eta_seconds(1_100.0, percent) is None


def test_eta_extrapolates_the_remaining_time_from_elapsed_progress(monkeypatch):
    _fake_time(monkeypatch, now=1_110.0)

    # 10s bought 50% ⇒ the other 50% should cost about another 10s.
    assert model_runtime.estimate_eta_seconds(1_100.0, 50.0) == pytest.approx(10.0)
    # 10s bought 25% ⇒ three quarters left, so three times the elapsed time.
    assert model_runtime.estimate_eta_seconds(1_100.0, 25.0) == pytest.approx(30.0)


def test_eta_never_goes_negative_when_the_clock_moved_backwards(monkeypatch):
    _fake_time(monkeypatch, now=1_090.0)

    assert model_runtime.estimate_eta_seconds(1_100.0, 50.0) == 0.0


# ── hf_model_ready ───────────────────────────────────────────────────────────


def _hf_cache_repo(home, repo_id: str):
    return home / ".cache" / "huggingface" / "hub" / f"models--{repo_id.replace('/', '--')}"


def test_vllm_accepts_a_bare_huggingface_cache_snapshot(monkeypatch, tmp_path):
    """vLLM resolves repo ids itself, so a cache snapshot is enough for it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _patch_runtime(monkeypatch, "hf_model_dir", lambda repo: tmp_path / "absent" / repo)
    (_hf_cache_repo(tmp_path, "org/model") / "snapshots" / "abc123").mkdir(parents=True)

    assert model_runtime.hf_model_ready("org/model", "vllm") is True


def test_a_model_with_neither_a_local_dir_nor_a_cache_entry_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _patch_runtime(monkeypatch, "hf_model_dir", lambda repo: tmp_path / "absent" / repo)

    assert model_runtime.hf_model_ready("org/model", "local_mlx") is False


def test_llamacpp_needs_a_gguf_file_and_nothing_else(monkeypatch, tmp_path):
    model_dir = tmp_path / "models" / "org__model"
    (model_dir / "nested").mkdir(parents=True)
    _patch_runtime(monkeypatch, "hf_model_dir", lambda _repo: model_dir)

    assert model_runtime.hf_model_ready("org/model", "llamacpp") is False

    (model_dir / "nested" / "weights.gguf").write_bytes(b"gguf")
    assert model_runtime.hf_model_ready("org/model", "llamacpp") is True


def test_llamacpp_without_any_directory_is_not_ready(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, "hf_model_dir", lambda _repo: tmp_path / "nope")

    assert model_runtime.hf_model_ready("org/model", "llamacpp") is False


def test_a_local_mlx_dir_is_ready_only_with_config_weights_and_tokenizer(monkeypatch, tmp_path):
    model_dir = tmp_path / "models" / "org__model"
    model_dir.mkdir(parents=True)
    _patch_runtime(monkeypatch, "hf_model_dir", lambda _repo: model_dir)

    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    assert model_runtime.hf_model_ready("org/model", "local_mlx") is False, "weights are still missing"

    (model_dir / "model.safetensors").write_bytes(b"w")
    assert model_runtime.hf_model_ready("org/model", "local_mlx") is False, "tokenizer is still missing"

    (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    assert model_runtime.hf_model_ready("org/model", "local_mlx") is True


# ── engine_installed ─────────────────────────────────────────────────────────


def _fake_importlib(monkeypatch, present: set) -> None:
    _patch_runtime(
        monkeypatch,
        "importlib",
        types.SimpleNamespace(
            util=types.SimpleNamespace(
                find_spec=lambda name: object() if name in present else None
            )
        ),
    )


def test_local_mlx_needs_mlx_plus_one_of_the_two_model_runtimes(monkeypatch):
    _fake_importlib(monkeypatch, {"mlx", "mlx_lm"})
    assert model_runtime.engine_installed("local_mlx") is True

    _fake_importlib(monkeypatch, {"mlx", "mlx_vlm"})
    assert model_runtime.engine_installed("local_mlx") is True

    _fake_importlib(monkeypatch, {"mlx"})
    assert model_runtime.engine_installed("local_mlx") is False, "mlx alone cannot load a model"

    _fake_importlib(monkeypatch, {"mlx_vlm"})
    assert model_runtime.engine_installed("local_mlx") is False


def test_ollama_is_installed_when_its_binary_is_on_the_path(monkeypatch):
    _patch_runtime(monkeypatch, "local_binary", lambda name: "/usr/bin/ollama" if name == "ollama" else None)
    assert model_runtime.engine_installed("ollama") is True

    _patch_runtime(monkeypatch, "local_binary", lambda _name: None)
    assert model_runtime.engine_installed("ollama") is False


def test_vllm_counts_any_of_the_metal_venv_the_binary_or_the_wheel(monkeypatch):
    _fake_importlib(monkeypatch, set())
    _patch_runtime(monkeypatch, "vllm_executable", lambda: None)

    _patch_runtime(monkeypatch, "vllm_metal_python", lambda: "/home/u/.venv-vllm-metal/bin/python")
    assert model_runtime.engine_installed("vllm") is True

    _patch_runtime(monkeypatch, "vllm_metal_python", lambda: None)
    _patch_runtime(monkeypatch, "vllm_executable", lambda: "/usr/bin/vllm")
    assert model_runtime.engine_installed("vllm") is True

    _patch_runtime(monkeypatch, "vllm_executable", lambda: None)
    assert model_runtime.engine_installed("vllm") is False

    _fake_importlib(monkeypatch, {"vllm"})
    assert model_runtime.engine_installed("vllm") is True


def test_lmstudio_is_installed_when_the_cli_is_found(monkeypatch):
    _patch_runtime(monkeypatch, "find_lmstudio_cli", lambda: "/usr/local/bin/lms")
    assert model_runtime.engine_installed("lmstudio") is True


def test_llamacpp_is_installed_when_llama_server_is_on_the_path(monkeypatch):
    _patch_runtime(
        monkeypatch,
        "shutil",
        types.SimpleNamespace(which=lambda name: "/usr/bin/llama-server" if name == "llama-server" else None),
    )
    assert model_runtime.engine_installed("llamacpp") is True

    _patch_runtime(monkeypatch, "shutil", types.SimpleNamespace(which=lambda _name: None))
    assert model_runtime.engine_installed("llamacpp") is False


@pytest.mark.parametrize("provider", ["openai", "openrouter", "groq", "together", "xai"])
def test_cloud_providers_are_installed_exactly_when_the_openai_sdk_is(monkeypatch, provider):
    _patch_runtime(monkeypatch, "AsyncOpenAI", object())
    assert model_runtime.engine_installed(provider) is True

    _patch_runtime(monkeypatch, "AsyncOpenAI", None)
    assert model_runtime.engine_installed(provider) is False


def test_an_unknown_engine_is_never_reported_as_installed():
    assert model_runtime.engine_installed("wp02-not-an-engine") is False


# ── install plan ─────────────────────────────────────────────────────────────


def test_safe_install_plan_returns_the_plan_and_swallows_a_planning_failure(monkeypatch, tmp_path):
    seen: list = []
    _patch_runtime(
        monkeypatch,
        "_engine_install_plan",
        lambda engine, **kwargs: seen.append((engine, kwargs)) or {"name": f"engine:{engine}"},
    )
    assert model_runtime._safe_engine_install_plan("ollama", base_dir=tmp_path) == {"name": "engine:ollama"}
    assert seen == [("ollama", {"base_dir": tmp_path})]

    def _boom(_engine, **_kwargs):
        raise RuntimeError("no installer for this platform")

    _patch_runtime(monkeypatch, "_engine_install_plan", _boom)
    assert model_runtime._safe_engine_install_plan("ollama", base_dir=tmp_path) is None, (
        "an unplannable engine must degrade to 'no plan', never break the whole listing"
    )


# ── alias resolution ─────────────────────────────────────────────────────────


def test_a_prefixed_id_resolves_through_that_providers_alias_table(monkeypatch):
    _patch_runtime(
        monkeypatch,
        "MODEL_ENGINE_ALIASES",
        {"aliased-model": {"local_mlx": "org/aliased-model-4bit", "ollama": "org/aliased:q4"}},
    )

    assert model_runtime._resolve_model_alias("local_mlx:aliased-model") == "org/aliased-model-4bit"
    assert model_runtime._resolve_model_alias("ollama:aliased-model") == "ollama:org/aliased:q4"


def test_an_id_with_no_alias_entry_is_returned_untouched(monkeypatch):
    _patch_runtime(monkeypatch, "MODEL_ENGINE_ALIASES", {"aliased-model": {"local_mlx": "org/x"}})

    assert model_runtime._resolve_model_alias("wp02-unknown-model") == "wp02-unknown-model"


def test_an_alias_without_an_entry_for_this_engine_is_returned_untouched(monkeypatch):
    _patch_runtime(monkeypatch, "MODEL_ENGINE_ALIASES", {"aliased-model": {"local_mlx": "org/x"}})

    assert model_runtime._resolve_model_alias("aliased-model", "ollama") == "aliased-model"


def test_normalize_strips_the_local_prefix_and_adds_a_remote_one():
    assert model_runtime.normalize_local_model_request("mlx:wp02-model", "mlx") == "wp02-model"
    assert model_runtime.normalize_local_model_request("local_mlx:wp02-model", "local_mlx") == "wp02-model"
    assert model_runtime.normalize_local_model_request("wp02-model", "ollama") == "ollama:wp02-model"
    assert model_runtime.normalize_local_model_request("ollama:wp02-model", "ollama") == "ollama:wp02-model"
    assert model_runtime.normalize_local_model_request("wp02-model", None) == "wp02-model"


# ── ensure_engine_ready ──────────────────────────────────────────────────────


def _state(**kwargs) -> model_runtime.ModelRuntimeState:
    return model_runtime.ModelRuntimeState(**kwargs)


def test_an_unknown_engine_is_refused_before_any_install_is_considered():
    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_engine_ready("wp02-not-an-engine", state=_state())

    assert err.value.status_code == 400
    assert "wp02-not-an-engine" in str(err.value.detail)


def test_an_unsupported_platform_is_refused_with_the_support_reason(monkeypatch):
    _patch_runtime(
        monkeypatch,
        "engine_support_status",
        lambda _engine: {"supported": False, "reason": "vLLM Metal 자동 설치는 Apple Silicon macOS에서만 지원됩니다."},
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_engine_ready("vllm", state=_state())

    assert err.value.status_code == 400
    assert "Apple Silicon" in str(err.value.detail)


def test_an_already_installed_mlx_engine_warms_the_runtime_and_installs_nothing(monkeypatch):
    warmed: list = []
    _patch_runtime(monkeypatch, "engine_installed", lambda _engine: True)
    _patch_runtime(monkeypatch, "ensure_mlx_runtime", lambda: warmed.append("warm"))
    _patch_runtime(
        monkeypatch,
        "install_engine",
        lambda *_a, **_k: pytest.fail("an installed engine must not be installed again"),
    )

    # "mlx" is the legacy spelling and must canonicalise to local_mlx.
    result = model_runtime.ensure_engine_ready("mlx", state=_state())

    assert result == {"engine": "local_mlx", "installed": True, "installed_now": False}
    assert warmed == ["warm"]


def test_a_known_provider_with_no_installer_recipe_is_refused(monkeypatch):
    _patch_runtime(monkeypatch, "ENGINE_INSTALLERS", {})
    _patch_runtime(monkeypatch, "engine_installed", lambda _engine: False)

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_engine_ready("openai", state=_state())

    assert err.value.status_code == 400
    assert "설치 방법" in str(err.value.detail)


def test_a_failed_install_is_reported_with_the_installers_stderr(monkeypatch):
    _patch_runtime(monkeypatch, "engine_installed", lambda _engine: False)
    _patch_runtime(
        monkeypatch,
        "install_engine",
        lambda _engine, **_kwargs: {"returncode": 1, "stderr": "brew: command not found"},
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_engine_ready("ollama", state=_state())

    assert err.value.status_code == 500
    assert "brew: command not found" in str(err.value.detail)


def test_an_install_that_leaves_the_engine_absent_is_still_a_failure(monkeypatch):
    """returncode 0 is the installer's opinion; presence is the fact."""
    _patch_runtime(monkeypatch, "engine_installed", lambda _engine: False)
    _patch_runtime(
        monkeypatch,
        "install_engine",
        lambda _engine, **_kwargs: {"returncode": 0, "stdout": "nothing to do"},
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.ensure_engine_ready("ollama", state=_state())

    assert err.value.status_code == 500
    assert "nothing to do" in str(err.value.detail)


def test_a_successful_install_warms_mlx_and_reports_installed_now(monkeypatch):
    installed = {"value": False}
    warmed: list = []
    _patch_runtime(monkeypatch, "engine_installed", lambda _engine: installed["value"])
    _patch_runtime(monkeypatch, "ensure_mlx_runtime", lambda: warmed.append("warm"))

    def _install(_engine, **_kwargs):
        installed["value"] = True
        return {"returncode": 0, "stdout": "installed"}

    _patch_runtime(monkeypatch, "install_engine", _install)

    result = model_runtime.ensure_engine_ready("local_mlx", state=_state())

    assert result["installed_now"] is True
    assert result["install"] == {"returncode": 0, "stdout": "installed"}
    assert warmed == ["warm"]


# ── resolution + smoke test seams ────────────────────────────────────────────


def test_build_model_resolution_normalizes_first_and_keeps_the_clicked_label():
    resolution = model_runtime.build_model_resolution(
        "wp02-model", "ollama", user_email="me@example.com", display_name=None
    )

    assert resolution.load_id == "ollama:wp02-model"
    assert resolution.engine == "ollama"
    assert resolution.display_name == "wp02-model", "the label must stay what the user clicked"


def test_a_smoke_test_without_a_router_is_skipped_rather_than_declared_healthy():
    import asyncio

    resolution = model_runtime.build_model_resolution("wp02-model", "local_mlx")

    result = asyncio.run(
        model_runtime._smoke_test_loaded_model(resolution, state=_state(router=None))
    )

    assert result["ok"] is False
    assert result["skipped"] is True
    assert "router" in result["reason"]
