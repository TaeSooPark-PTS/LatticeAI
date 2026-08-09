"""Hugging Face download path — file selection, progress reporting, failures.

`download_hf_model` is the only place in the runtime that moves gigabytes, and
its progress reporting is what the Model Load screen renders. Both the
`huggingface_hub` client and the `tqdm` bar it hooks are injected through
``sys.modules`` and the clock is frozen, so the byte-accounting arithmetic and
the emit throttle are asserted exactly rather than observed approximately, and
nothing here depends on the network or on which optional wheels the runner has.
"""

from __future__ import annotations

import importlib.machinery
import sys
import types

import pytest

from latticeai.services import model_runtime
from latticeai.services.model_errors import ModelRuntimeError

FROZEN_NOW = 2_000.0


class _Sibling:
    def __init__(self, rfilename, size=0):
        self.rfilename = rfilename
        self.size = size


class _RecordingApi:
    """Stands in for ``huggingface_hub.HfApi``."""

    instances: list = []

    def __init__(self):
        self.model_info_calls: list = []
        self.list_calls: list = []
        _RecordingApi.instances.append(self)

    info_result: object = None
    info_error: BaseException | None = None
    repo_files: tuple = ()

    def model_info(self, repo_id, files_metadata=False):
        self.model_info_calls.append((repo_id, files_metadata))
        if type(self).info_error is not None:
            raise type(self).info_error
        return type(self).info_result

    def list_repo_files(self, repo_id):
        self.list_calls.append(repo_id)
        return list(type(self).repo_files)


class _BaseTqdm:
    """A minimal stand-in for ``tqdm.auto.tqdm``: it only has to count."""

    def __init__(self, *_args, **_kwargs):
        self.n = 0

    def update(self, n=1):
        self.n += n
        return "base-update"


def _install_hub(monkeypatch, *, api_cls=None, hf_hub_download=None, tqdm_cls=_BaseTqdm):
    """Put fake huggingface_hub / tqdm modules in front of the real ones."""
    hub = types.ModuleType("huggingface_hub")
    hub.__spec__ = importlib.machinery.ModuleSpec("huggingface_hub", loader=None)
    hub.HfApi = api_cls or _RecordingApi
    hub.hf_hub_download = hf_hub_download or (lambda **_kwargs: "")
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    if tqdm_cls is None:
        monkeypatch.setitem(sys.modules, "tqdm.auto", None)
        return hub

    tqdm_auto = types.ModuleType("tqdm.auto")
    tqdm_auto.__spec__ = importlib.machinery.ModuleSpec("tqdm.auto", loader=None)
    tqdm_auto.tqdm = tqdm_cls
    tqdm_pkg = types.ModuleType("tqdm")
    tqdm_pkg.__spec__ = importlib.machinery.ModuleSpec("tqdm", loader=None)
    tqdm_pkg.auto = tqdm_auto
    monkeypatch.setitem(sys.modules, "tqdm", tqdm_pkg)
    monkeypatch.setitem(sys.modules, "tqdm.auto", tqdm_auto)
    return hub


def _freeze_clock(monkeypatch, now=FROZEN_NOW):
    monkeypatch.setattr(
        model_runtime,
        "time",
        types.SimpleNamespace(time=lambda: now, monotonic=lambda: now, sleep=lambda _s: None),
    )


@pytest.fixture(autouse=True)
def _fresh_api_class():
    _RecordingApi.instances = []
    _RecordingApi.info_result = None
    _RecordingApi.info_error = None
    _RecordingApi.repo_files = ()
    yield
    _RecordingApi.instances = []


# ── hf_repo_files_with_sizes ─────────────────────────────────────────────────


def test_repo_listing_uses_sibling_metadata_and_drops_directory_entries(monkeypatch):
    _install_hub(monkeypatch)
    _RecordingApi.info_result = types.SimpleNamespace(
        siblings=[
            _Sibling("config.json", 120),
            _Sibling("subdir/", 0),
            _Sibling("  ", 0),
            _Sibling("model.safetensors", 4096),
        ]
    )

    files = model_runtime.hf_repo_files_with_sizes("org/model")

    assert files == [
        {"name": "config.json", "size": 120},
        {"name": "model.safetensors", "size": 4096},
    ]
    assert _RecordingApi.instances[0].model_info_calls == [("org/model", True)]
    assert _RecordingApi.instances[0].list_calls == [], "the metadata call already answered it"


def test_repo_listing_falls_back_to_plain_names_when_metadata_is_empty(monkeypatch):
    _install_hub(monkeypatch)
    _RecordingApi.info_result = types.SimpleNamespace(siblings=[])
    _RecordingApi.repo_files = ("config.json", "   ", "model.safetensors")

    files = model_runtime.hf_repo_files_with_sizes("org/model")

    assert files == [{"name": "config.json", "size": 0}, {"name": "model.safetensors", "size": 0}]


def test_repo_listing_survives_a_client_that_rejects_files_metadata(monkeypatch):
    """Older hub clients have no ``files_metadata`` keyword — a TypeError."""
    _install_hub(monkeypatch)
    _RecordingApi.info_error = TypeError("model_info() got an unexpected keyword argument")
    _RecordingApi.repo_files = ("config.json",)

    assert model_runtime.hf_repo_files_with_sizes("org/model") == [{"name": "config.json", "size": 0}]


def test_repo_listing_logs_and_falls_back_when_the_hub_call_fails(monkeypatch, caplog):
    _install_hub(monkeypatch)
    _RecordingApi.info_error = RuntimeError("hub is down")
    _RecordingApi.repo_files = ("config.json",)

    with caplog.at_level("WARNING"):
        files = model_runtime.hf_repo_files_with_sizes("org/model")

    assert files == [{"name": "config.json", "size": 0}]
    assert "hub is down" in caplog.text


# ── download_hf_model ────────────────────────────────────────────────────────


def test_a_download_without_huggingface_hub_asks_for_the_runtime_first(monkeypatch):
    monkeypatch.setattr(
        model_runtime,
        "importlib",
        types.SimpleNamespace(util=types.SimpleNamespace(find_spec=lambda _name: None)),
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.download_hf_model("org/model")

    assert err.value.status_code == 400
    assert "MLX runtime" in str(err.value.detail)


def test_an_already_present_model_reports_the_cache_snapshot_and_downloads_nothing(monkeypatch, tmp_path):
    _install_hub(
        monkeypatch,
        hf_hub_download=lambda **_kwargs: pytest.fail("a cached model must not be re-fetched"),
    )
    _freeze_clock(monkeypatch)
    snapshot = tmp_path / "snapshots" / "abc"
    monkeypatch.setattr(model_runtime, "hf_model_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(model_runtime, "hf_cache_model_dir", lambda _repo: snapshot)
    emitted: list = []

    result = model_runtime.download_hf_model("org/model", "local_mlx", progress_emit=emitted.append)

    assert result == {"model": "org/model", "path": str(snapshot), "cached": True}
    assert [item["percent"] for item in emitted] == [100]
    assert emitted[0]["eta_seconds"] == 0


def test_a_present_non_mlx_model_reports_the_managed_directory(monkeypatch, tmp_path):
    _install_hub(monkeypatch)
    monkeypatch.setattr(model_runtime, "hf_model_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(
        model_runtime,
        "hf_cache_model_dir",
        lambda _repo: pytest.fail("only the MLX path consults the HF cache"),
    )

    result = model_runtime.download_hf_model("org/model", "vllm")

    assert result == {"model": "org/model", "path": str(tmp_path / "target"), "cached": True}


def _readiness_toggle(monkeypatch, *, ready_after=True):
    """hf_model_ready: False until the download ran, then `ready_after`."""
    state = {"downloaded": False}

    def _ready(*_a, **_k):
        return ready_after if state["downloaded"] else False

    monkeypatch.setattr(model_runtime, "hf_model_ready", _ready)
    return state


def test_a_sized_download_reports_aggregate_byte_progress_and_throttles_it(monkeypatch, tmp_path):
    """Byte progress is aggregate across files, and near-duplicate ticks drop.

    Without the throttle a multi-gigabyte file emits thousands of SSE frames a
    second; without the aggregate offset every file restarts the bar at zero.
    """
    _freeze_clock(monkeypatch)
    target = tmp_path / "target"
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: target)
    monkeypatch.setattr(
        model_runtime,
        "hf_repo_files_with_sizes",
        lambda _repo: [{"name": "config.json", "size": 10}, {"name": "model.safetensors", "size": 90}],
    )
    state = _readiness_toggle(monkeypatch)

    def _download(*, repo_id, filename, local_dir, tqdm_class):
        assert repo_id == "org/model"
        bar = tqdm_class(total=100)
        bar.update(5)
        bar.update(0)  # same percent, same instant ⇒ throttled away
        state["downloaded"] = True
        path = tmp_path / "target" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return str(path)

    _install_hub(monkeypatch, hf_hub_download=_download)
    emitted: list = []

    result = model_runtime.download_hf_model("org/model", "local_mlx", progress_emit=emitted.append)

    assert result == {"model": "org/model", "path": str(target), "cached": False}
    # The second file's bar restarts at 0 but its percentages continue from 10,
    # and each file contributes exactly one byte-progress frame — the second
    # `update` landed on the same instant and the same percent, so it was
    # dropped rather than re-sent.
    assert [(item.get("file"), item["percent"]) for item in emitted] == [
        (None, 0.0),
        ("config.json", 0.0),
        ("config.json", 5.0),
        ("config.json", 10.0),
        ("model.safetensors", 10.0),
        ("model.safetensors", 15.0),
        ("model.safetensors", 100.0),
        (None, 100.0),
    ]
    assert [item["downloaded_bytes"] for item in emitted] == [0, 0, 5, 10, 10, 15, 100, 100]
    assert emitted[-1]["total_bytes"] == 100


def test_an_unsized_download_falls_back_to_counting_files(monkeypatch, tmp_path):
    """With no sizes the bar has to be per-file, and a vanished file counts 0."""
    _freeze_clock(monkeypatch)
    target = tmp_path / "target"
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: target)
    monkeypatch.setattr(
        model_runtime,
        "hf_repo_files_with_sizes",
        lambda _repo: [{"name": "a.bin", "size": 0}, {"name": "b.bin", "size": 0}],
    )
    state = _readiness_toggle(monkeypatch)

    def _download(*, repo_id, filename, local_dir, tqdm_class):
        bar = tqdm_class()
        bar.update(4)
        state["downloaded"] = True
        if filename == "b.bin":
            return str(tmp_path / "gone" / filename)
        path = tmp_path / "target" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"1234567")
        return str(path)

    _install_hub(monkeypatch, hf_hub_download=_download)
    emitted: list = []

    result = model_runtime.download_hf_model("org/model", "local_mlx", progress_emit=emitted.append)

    assert result["cached"] is False
    # a.bin is measured on disk (7 bytes); b.bin disappeared, so it counts 0
    # rather than aborting the download that already succeeded.
    assert emitted[-1]["downloaded_bytes"] == 7
    assert emitted[-1]["total_bytes"] == 7
    assert [(item.get("file"), item["percent"]) for item in emitted] == [
        (None, 0.0),
        ("a.bin", 0.0),
        ("a.bin", 50.0),
        ("a.bin", 50.0),
        ("b.bin", 50.0),
        ("b.bin", 100.0),
        ("b.bin", 100.0),
        (None, 100.0),
    ]
    assert emitted[0]["indeterminate"] is True, "an unsized repo cannot claim a real percentage"


def test_a_download_without_a_progress_sink_still_completes(monkeypatch, tmp_path):
    _freeze_clock(monkeypatch)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(model_runtime, "hf_repo_files_with_sizes", lambda _repo: [{"name": "a.bin", "size": 4}])
    state = _readiness_toggle(monkeypatch)

    def _download(*, repo_id, filename, local_dir, tqdm_class):
        assert tqdm_class is None, "no sink means no progress bar to hook"
        state["downloaded"] = True
        return str(tmp_path / "target" / filename)

    _install_hub(monkeypatch, hf_hub_download=_download)

    assert model_runtime.download_hf_model("org/model", "local_mlx")["cached"] is False


def test_a_missing_tqdm_degrades_to_a_download_without_a_bar(monkeypatch, tmp_path):
    _freeze_clock(monkeypatch)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(model_runtime, "hf_repo_files_with_sizes", lambda _repo: [{"name": "a.bin", "size": 4}])
    state = _readiness_toggle(monkeypatch)
    seen: list = []

    def _download(*, repo_id, filename, local_dir, tqdm_class):
        seen.append(tqdm_class)
        state["downloaded"] = True
        return str(tmp_path / "target" / filename)

    _install_hub(monkeypatch, hf_hub_download=_download, tqdm_cls=None)

    assert model_runtime.download_hf_model("org/model", "local_mlx", progress_emit=lambda _p: None)["cached"] is False
    assert seen == [None], "an unavailable tqdm must not abort the download"


def test_llamacpp_picks_the_preferred_quantisation_and_downloads_only_that(monkeypatch, tmp_path):
    _freeze_clock(monkeypatch)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(
        model_runtime,
        "hf_repo_files_with_sizes",
        lambda _repo: [
            {"name": "README.md", "size": 1},
            {"name": "model-f16.gguf", "size": 900},
            {"name": "model-Q4_K_M.gguf", "size": 300},
        ],
    )
    state = _readiness_toggle(monkeypatch)
    requested: list = []

    def _download(*, repo_id, filename, local_dir, tqdm_class):
        requested.append(filename)
        state["downloaded"] = True
        return str(tmp_path / "target" / filename)

    _install_hub(monkeypatch, hf_hub_download=_download)

    result = model_runtime.download_hf_model("org/model", "llamacpp")

    assert requested == ["model-Q4_K_M.gguf"], "a GGUF repo must not pull every quantisation"
    assert result["cached"] is False


def test_a_llamacpp_repo_with_no_gguf_is_a_clear_download_failure(monkeypatch, tmp_path):
    _freeze_clock(monkeypatch)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(model_runtime, "hf_repo_files_with_sizes", lambda _repo: [{"name": "README.md", "size": 1}])
    _readiness_toggle(monkeypatch)
    _install_hub(monkeypatch, hf_hub_download=lambda **_kwargs: pytest.fail("nothing to download"))

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.download_hf_model("org/model", "llamacpp")

    assert err.value.status_code == 500
    assert "GGUF" in str(err.value.detail)


def test_a_transport_failure_mid_download_is_reported_against_the_repo(monkeypatch, tmp_path):
    _freeze_clock(monkeypatch)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(model_runtime, "hf_repo_files_with_sizes", lambda _repo: [{"name": "a.bin", "size": 4}])
    _readiness_toggle(monkeypatch)

    def _download(**_kwargs):
        raise OSError("connection reset")

    _install_hub(monkeypatch, hf_hub_download=_download)

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.download_hf_model("org/model", "local_mlx")

    assert err.value.status_code == 500
    assert "org/model 다운로드 실패" in str(err.value.detail)
    assert "connection reset" in str(err.value.detail)


def test_a_download_that_leaves_the_model_unusable_is_not_reported_as_success(monkeypatch, tmp_path):
    """Bytes arriving is not the same as a loadable model on disk."""
    _freeze_clock(monkeypatch)
    monkeypatch.setattr(model_runtime, "hf_model_dir", lambda _repo: tmp_path / "target")
    monkeypatch.setattr(model_runtime, "hf_repo_files_with_sizes", lambda _repo: [{"name": "a.bin", "size": 4}])
    _readiness_toggle(monkeypatch, ready_after=False)
    _install_hub(monkeypatch, hf_hub_download=lambda **kwargs: str(tmp_path / "target" / kwargs["filename"]))

    with pytest.raises(ModelRuntimeError) as err:
        model_runtime.download_hf_model("org/model", "local_mlx")

    assert err.value.status_code == 500
    assert "완료되지 않았습니다" in str(err.value.detail)
