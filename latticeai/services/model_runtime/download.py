"""Hugging Face model presence, download, and download progress.

Answers one question — "are the weights actually on this disk?" — and, when
they are not and the caller has consent, fetches them while emitting a progress
payload per file. The readiness check is deliberately format-aware: a GGUF
repo, an MLX 4-bit repo and a vLLM bf16 repo each prove completeness
differently.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set

from latticeai.core.quiet import quiet
from latticeai.models.router import hf_cache_model_dir, hf_model_dir
from latticeai.models.router.errors import MODEL_RUNTIME_BOUNDARY_ERRORS
from latticeai.services.embedding_recommendation import (
    get_public_mirror_repos,
    has_hf_auth_token,
    resolve_hf_endpoint,
)
from latticeai.services.model_errors import ModelRuntimeError

_AUXILIARY_GGUF_PREFIXES = ("mmproj-", "mtp-", "dflash-", "eagle3-")
GGUF_QUANT_PREFERENCE = ("q4_k_m", "q4_0", "mxfp4", "q4_k_s", "q3_k_m", "q2_k", "q8_0")
_LATEST_DOWNLOAD_PROGRESS: Dict[str, Dict[str, Any]] = {}
_CANCEL_LOCK = threading.Lock()
_CANCELLED_DOWNLOADS: Set[str] = set()


def _normalize_cancel_key(model_id: Optional[str]) -> str:
    raw = str(model_id or "").strip().lower()
    if ":" in raw and raw.split(":", 1)[0] in {
        "local_mlx",
        "mlx",
        "omlx",
        "huggingface",
        "ollama",
        "vllm",
        "llamacpp",
    }:
        return raw.split(":", 1)[1].strip()
    return raw


def request_download_cancel(model_id: Optional[str] = None) -> None:
    """Signal cooperative cancellation for ``model_id`` (or ``*`` for all in-flight downloads)."""
    key = _normalize_cancel_key(model_id) or "*"
    with _CANCEL_LOCK:
        _CANCELLED_DOWNLOADS.add(key)
        if model_id:
            _CANCELLED_DOWNLOADS.add(str(model_id).strip().lower())


def clear_download_cancel(model_id: Optional[str] = None) -> None:
    """Clear cancellation state before starting a fresh download."""
    with _CANCEL_LOCK:
        if not model_id:
            _CANCELLED_DOWNLOADS.clear()
            return
        _CANCELLED_DOWNLOADS.discard(_normalize_cancel_key(model_id))
        _CANCELLED_DOWNLOADS.discard(str(model_id).strip().lower())
        _CANCELLED_DOWNLOADS.discard("*")


def is_download_cancelled(*candidates: Optional[str]) -> bool:
    with _CANCEL_LOCK:
        if "*" in _CANCELLED_DOWNLOADS:
            return True
        for item in candidates:
            if not item:
                continue
            if str(item).strip().lower() in _CANCELLED_DOWNLOADS:
                return True
            norm = _normalize_cancel_key(item)
            if norm and norm in _CANCELLED_DOWNLOADS:
                return True
    return False


def check_download_cancelled(*candidates: Optional[str]) -> None:
    if is_download_cancelled(*candidates):
        raise ModelRuntimeError(
            status_code=499, detail="모델 다운로드가 취소되었습니다."
        )


def is_gated_or_auth_error_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "gated",
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "access to model",
            "cannot access gated repo",
            "restricted",
            "authentication",
            "token is required",
        )
    )


def is_gated_or_auth_error(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status_code in {401, 403}:
        return True
    type_name = type(exc).__name__.lower()
    if "gated" in type_name or "unauthorized" in type_name or "forbidden" in type_name:
        return True
    return is_gated_or_auth_error_text(str(exc))


def resolve_download_candidates(
    repo_id: str,
    provider: str = "local_mlx",
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    mirrors = get_public_mirror_repos(repo_id, provider)
    authenticated = has_hf_auth_token(env)
    endpoint = resolve_hf_endpoint(env)
    if mirrors and not authenticated:
        candidates = [*mirrors, repo_id]
        proactive_fallback = True
    else:
        candidates = [repo_id, *[m for m in mirrors if m != repo_id]]
        proactive_fallback = False
    return {
        "requested_repo": repo_id,
        "primary_repo": candidates[0],
        "candidates": candidates,
        "public_mirror_repos": mirrors,
        "authenticated": authenticated,
        "proactive_fallback": proactive_fallback,
        "hf_endpoint": endpoint,
    }


def record_download_progress_snapshot(
    payload: Mapping[str, Any],
    *,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot = dict(payload)
    key = (
        str(
            model_id
            or snapshot.get("fallback_repo")
            or snapshot.get("detail")
            or "_latest"
        ).strip()
        or "_latest"
    )
    _LATEST_DOWNLOAD_PROGRESS[key] = snapshot
    _LATEST_DOWNLOAD_PROGRESS["_latest"] = snapshot
    return snapshot


def get_download_progress_snapshot(model_id: Optional[str] = None) -> Dict[str, Any]:
    if model_id and model_id in _LATEST_DOWNLOAD_PROGRESS:
        return dict(_LATEST_DOWNLOAD_PROGRESS[model_id])
    latest = _LATEST_DOWNLOAD_PROGRESS.get("_latest")
    return dict(latest) if latest else {}


def clear_download_progress_snapshots() -> None:
    _LATEST_DOWNLOAD_PROGRESS.clear()


def is_auxiliary_gguf(filename: str) -> bool:
    base = Path(str(filename)).name.lower()
    return base.startswith(_AUXILIARY_GGUF_PREFIXES) or "imatrix" in base


def _shards_complete(model_dir: Path) -> bool:
    index_file = model_dir / "model.safetensors.index.json"
    if not index_file.exists():
        return True
    try:
        payload = json.loads(index_file.read_text(encoding="utf-8"))
        weight_map = payload.get("weight_map") if isinstance(payload, dict) else None
        if isinstance(weight_map, dict) and weight_map:
            shards = {str(shard) for shard in weight_map.values() if shard}
            return all((model_dir / shard).exists() for shard in shards)
    except (OSError, ValueError, TypeError, AttributeError):
        quiet()
    return True


def hf_model_ready(repo_id: str, provider: str = "local_mlx") -> bool:
    if provider == "omlx" and "/" not in repo_id and "--" in repo_id:
        repo_id = repo_id.replace("--", "/", 1)
    model_dir = hf_model_dir(repo_id)
    if provider in {"local_mlx", "omlx", "vllm"} and (
        not model_dir.exists() or not model_dir.is_dir()
    ):
        hf_cache_repo = (
            Path.home()
            / ".cache"
            / "huggingface"
            / "hub"
            / f"models--{repo_id.replace('/', '--')}"
        )
        if hf_cache_repo.exists() and any(hf_cache_repo.glob("snapshots/*")):
            if provider == "vllm":
                return True
            return hf_cache_model_dir(repo_id) is not None
        return False
    if not model_dir.exists() or not model_dir.is_dir():
        return False
    if provider == "llamacpp":
        return any(not is_auxiliary_gguf(p.name) for p in model_dir.rglob("*.gguf"))
    has_config = (model_dir / "config.json").exists()
    has_weights = (
        any(model_dir.glob("*.safetensors")) or any(model_dir.glob("*.bin"))
    ) and _shards_complete(model_dir)
    has_tokenizer = (
        (model_dir / "tokenizer.json").exists()
        or (model_dir / "tokenizer.model").exists()
        or (model_dir / "tokenizer_config.json").exists()
    )
    return has_config and has_weights and has_tokenizer


def model_download_progress_payload(
    stage: str,
    message: str,
    *,
    percent: Optional[float] = None,
    detail: Optional[str] = None,
    downloaded_bytes: Optional[int] = None,
    total_bytes: Optional[int] = None,
    speed_bps: Optional[float] = None,
    eta_seconds: Optional[float] = None,
    file: Optional[str] = None,
    current_file: Optional[str] = None,
    gated_fallback: Optional[bool] = None,
    fallback_repo: Optional[str] = None,
    indeterminate: bool = False,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "stage": stage,
        "message": message,
        "indeterminate": indeterminate,
        "ts": time.time(),
    }
    if percent is not None:
        payload["percent"] = max(0, min(100, round(float(percent), 1)))
    if detail:
        payload["detail"] = detail
    if downloaded_bytes is not None:
        payload["downloaded_bytes"] = max(0, int(downloaded_bytes))
    if total_bytes is not None:
        payload["total_bytes"] = max(0, int(total_bytes))
    if speed_bps is not None:
        clean_speed = max(0.0, round(float(speed_bps), 2))
        payload["speed_bps"] = clean_speed
        payload["speed_mbps"] = round(clean_speed / (1024.0 * 1024.0), 2)
    if eta_seconds is not None:
        payload["eta_seconds"] = max(0, round(float(eta_seconds)))
    resolved_file = current_file or file
    if resolved_file:
        payload["file"] = resolved_file
        payload["current_file"] = resolved_file
    if gated_fallback is not None:
        payload["gated_fallback"] = bool(gated_fallback)
    if fallback_repo:
        payload["fallback_repo"] = str(fallback_repo)
    record_download_progress_snapshot(payload, model_id=fallback_repo or detail)
    return payload


def estimate_speed_bps(
    started_at: float,
    downloaded_bytes: Optional[int],
    *,
    now: Optional[float] = None,
) -> Optional[float]:
    if downloaded_bytes is None or downloaded_bytes <= 0:
        return None
    current_time = now if now is not None else time.time()
    elapsed = max(0.0, current_time - started_at)
    if elapsed <= 0:
        return None
    return round(float(downloaded_bytes) / elapsed, 2)


def estimate_eta_seconds(
    started_at: float,
    percent: Optional[float],
    *,
    downloaded_bytes: Optional[int] = None,
    total_bytes: Optional[int] = None,
    speed_bps: Optional[float] = None,
) -> Optional[float]:
    if percent is not None and (percent <= 0 or percent >= 100):
        return None
    if (
        speed_bps is not None
        and speed_bps > 0
        and total_bytes is not None
        and downloaded_bytes is not None
        and 0 < downloaded_bytes < total_bytes
    ):
        return max(
            0.0, (float(total_bytes) - float(downloaded_bytes)) / float(speed_bps)
        )
    if percent is None or percent <= 0 or percent >= 100:
        return None
    elapsed = max(0.0, time.time() - started_at)
    return elapsed * (100.0 - percent) / percent


def _build_hf_api() -> Any:
    from huggingface_hub import HfApi

    env_endpoint = (
        os.environ.get("HF_ENDPOINT")
        or os.environ.get("HUGGINGFACE_HUB_ENDPOINT")
        or ""
    ).strip()
    env_token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACE_TOKEN")
        or ""
    ).strip()
    if env_endpoint or env_token:
        kwargs: Dict[str, Any] = {}
        if env_endpoint:
            kwargs["endpoint"] = env_endpoint.rstrip("/")
        if env_token:
            kwargs["token"] = env_token
        try:
            return HfApi(**kwargs)
        except TypeError:
            quiet()
    return HfApi()


def _call_hf_hub_download(
    hf_hub_download_fn: Any,
    *,
    repo_id: str,
    filename: str,
    local_dir: str,
    tqdm_class: Any,
) -> str:
    os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    env_endpoint = (
        os.environ.get("HF_ENDPOINT")
        or os.environ.get("HUGGINGFACE_HUB_ENDPOINT")
        or ""
    ).strip()
    env_token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACE_TOKEN")
        or ""
    ).strip()
    if env_endpoint or env_token:
        extra: Dict[str, Any] = {}
        if env_endpoint:
            extra["endpoint"] = env_endpoint.rstrip("/")
        if env_token:
            extra["token"] = env_token
        try:
            return hf_hub_download_fn(
                repo_id=repo_id,
                filename=filename,
                local_dir=local_dir,
                tqdm_class=tqdm_class,
                **extra,
            )
        except TypeError:
            quiet()
    return hf_hub_download_fn(
        repo_id=repo_id,
        filename=filename,
        local_dir=local_dir,
        tqdm_class=tqdm_class,
    )


def hf_repo_files_with_sizes(repo_id: str) -> List[Dict[str, Any]]:
    api = _build_hf_api()
    try:
        info = api.model_info(repo_id, files_metadata=True)
        files = []
        for sibling in getattr(info, "siblings", []) or []:
            name = str(getattr(sibling, "rfilename", "") or "").strip()
            if not name or name.endswith("/"):
                continue
            files.append({"name": name, "size": int(getattr(sibling, "size", 0) or 0)})
        if files:
            return files
    except TypeError:
        quiet()
    except Exception as e:
        if is_gated_or_auth_error(e):
            raise
        logging.warning("huggingface model_info failed for %s: %s", repo_id, e)

    return [
        {"name": str(name), "size": 0}
        for name in api.list_repo_files(repo_id)
        if str(name).strip()
    ]


def _select_repo_files_for_provider(
    all_files: List[Dict[str, Any]],
    provider: str,
) -> List[Dict[str, Any]]:
    if provider != "llamacpp":
        return all_files
    ggufs = sorted(
        [item for item in all_files if str(item["name"]).lower().endswith(".gguf")],
        key=lambda item: str(item["name"]),
    )
    primary_ggufs = [item for item in ggufs if not is_auxiliary_gguf(str(item["name"]))]
    if not primary_ggufs:
        raise RuntimeError("GGUF 파일을 찾지 못했습니다.")
    selected_files = [
        next(
            (
                item
                for pref in GGUF_QUANT_PREFERENCE
                for item in primary_ggufs
                if pref in str(item["name"]).lower()
            ),
            primary_ggufs[0],
        )
    ]
    mmprojs = [
        item
        for item in ggufs
        if Path(str(item["name"])).name.lower().startswith("mmproj-")
    ]
    if mmprojs:
        preferred_mmproj = next(
            (
                item
                for pref in ("bf16", "f16", "f32")
                for item in mmprojs
                if pref in str(item["name"]).lower()
            ),
            mmprojs[0],
        )
        selected_files.append(preferred_mmproj)
    return selected_files


def _download_single_hf_repo(
    active_repo: str,
    *,
    requested_repo: str,
    provider: str,
    progress_emit: Any,
    used_fallback: bool,
) -> Dict[str, Any]:
    from huggingface_hub import hf_hub_download

    target_dir = hf_model_dir(active_repo)
    target_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    fallback_kwargs: Dict[str, Any] = (
        {"gated_fallback": True, "fallback_repo": active_repo} if used_fallback else {}
    )
    if used_fallback and progress_emit:
        progress_emit(
            model_download_progress_payload(
                "download",
                f"인증 토큰 없이 사용 가능한 공개 미러({active_repo})로 전환합니다.",
                percent=0,
                detail=active_repo,
                downloaded_bytes=0,
                indeterminate=True,
                **fallback_kwargs,
            )
        )

    all_files = hf_repo_files_with_sizes(active_repo)
    selected_files = _select_repo_files_for_provider(all_files, provider)
    total_bytes = sum(int(item.get("size") or 0) for item in selected_files) or None
    downloaded_bytes = 0
    total_files = max(1, len(selected_files))
    if progress_emit:
        progress_emit(
            model_download_progress_payload(
                "download",
                "모델 파일 정보를 확인했습니다.",
                percent=0,
                downloaded_bytes=0,
                total_bytes=total_bytes,
                indeterminate=total_bytes is None,
                **fallback_kwargs,
            )
        )

    for index, item in enumerate(selected_files, start=1):
        check_download_cancelled(requested_repo, active_repo)
        filename = str(item["name"])
        size = int(item.get("size") or 0)
        tqdm_class = None
        if progress_emit:
            current_percent = (
                (downloaded_bytes / total_bytes) * 100
                if total_bytes
                else ((index - 1) / total_files) * 100
            )
            current_speed = estimate_speed_bps(started_at, downloaded_bytes)
            progress_emit(
                model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    speed_bps=current_speed,
                    eta_seconds=estimate_eta_seconds(
                        started_at,
                        current_percent,
                        downloaded_bytes=downloaded_bytes,
                        total_bytes=total_bytes,
                        speed_bps=current_speed,
                    ),
                    file=filename,
                    current_file=filename,
                    indeterminate=total_bytes is None and total_files <= 1,
                    **fallback_kwargs,
                )
            )
            try:
                from tqdm.auto import tqdm as base_tqdm

                downloaded_before = downloaded_bytes
                last_emit = {"at": 0.0, "percent": -1.0}

                def emit_byte_progress(
                    done_bytes: float,
                    downloaded_before: int = downloaded_before,
                    size: Any = size,
                    index: int = index,
                    last_emit: dict = last_emit,
                    filename: str = filename,
                ) -> None:
                    done = max(0, int(done_bytes or 0))
                    if total_bytes:
                        aggregate = min(total_bytes, downloaded_before + done)
                        percent = (aggregate / total_bytes) * 100
                    else:
                        file_total = size or done
                        file_ratio = min(1.0, done / file_total) if file_total else 0.0
                        aggregate = downloaded_before + done
                        percent = ((index - 1) + file_ratio) / total_files * 100
                    now = time.time()
                    if (
                        percent < 100
                        and now - last_emit["at"] < 0.5
                        and percent - last_emit["percent"] < 0.3
                    ):
                        return
                    last_emit["at"] = now
                    last_emit["percent"] = percent
                    speed_now = estimate_speed_bps(started_at, aggregate, now=now)
                    progress_emit(
                        model_download_progress_payload(
                            "download",
                            "모델 다운로드 중입니다.",
                            percent=percent,
                            detail=filename,
                            downloaded_bytes=aggregate,
                            total_bytes=total_bytes,
                            speed_bps=speed_now,
                            eta_seconds=estimate_eta_seconds(
                                started_at,
                                percent,
                                downloaded_bytes=aggregate,
                                total_bytes=total_bytes,
                                speed_bps=speed_now,
                            ),
                            file=filename,
                            current_file=filename,
                            indeterminate=total_bytes is None and total_files <= 1,
                            **fallback_kwargs,
                        )
                    )

                class ProgressTqdm(base_tqdm):
                    def update(self, n=1):
                        check_download_cancelled(requested_repo, active_repo)
                        result = super().update(n)
                        emit_byte_progress(float(getattr(self, "n", 0) or 0))
                        return result

                tqdm_class = ProgressTqdm
            except (ImportError, OSError, RuntimeError, TypeError, AttributeError):
                tqdm_class = None
        else:
            try:
                from tqdm.auto import tqdm as base_tqdm

                class CancelOnlyTqdm(base_tqdm):
                    def update(self, n=1):
                        check_download_cancelled(requested_repo, active_repo)
                        return super().update(n)

                tqdm_class = CancelOnlyTqdm
            except (ImportError, OSError, RuntimeError, TypeError, AttributeError):
                tqdm_class = None

        local_path = _call_hf_hub_download(
            hf_hub_download,
            repo_id=active_repo,
            filename=filename,
            local_dir=str(target_dir),
            tqdm_class=tqdm_class,
        )
        if size <= 0:
            try:
                size = Path(local_path).stat().st_size
            except OSError:
                size = 0
        downloaded_bytes += size
        if progress_emit:
            current_percent = (
                (downloaded_bytes / total_bytes) * 100
                if total_bytes
                else (index / total_files) * 100
            )
            current_speed = estimate_speed_bps(started_at, downloaded_bytes)
            progress_emit(
                model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    speed_bps=current_speed,
                    eta_seconds=estimate_eta_seconds(
                        started_at,
                        current_percent,
                        downloaded_bytes=downloaded_bytes,
                        total_bytes=total_bytes,
                        speed_bps=current_speed,
                    ),
                    file=filename,
                    current_file=filename,
                    indeterminate=False,
                    **fallback_kwargs,
                )
            )

    final_speed = estimate_speed_bps(started_at, downloaded_bytes)
    if progress_emit:
        progress_emit(
            model_download_progress_payload(
                "download",
                "모델 다운로드가 완료되었습니다.",
                percent=100,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes or downloaded_bytes,
                speed_bps=final_speed,
                eta_seconds=0,
                **fallback_kwargs,
            )
        )
    result: Dict[str, Any] = {
        "model": active_repo,
        "path": str(target_dir),
        "cached": False,
    }
    if used_fallback:
        result.update(
            {
                "requested_model": requested_repo,
                "gated_fallback": True,
                "fallback_repo": active_repo,
            }
        )
    return result


def download_hf_model(
    repo_id: str,
    provider: str = "local_mlx",
    progress_emit=None,
) -> Dict[str, Any]:
    if importlib.util.find_spec("huggingface_hub") is None:
        raise ModelRuntimeError(
            status_code=400,
            detail="huggingface_hub가 없습니다. 먼저 MLX runtime 설치를 진행해 주세요.",
        )

    target_dir = hf_model_dir(repo_id)
    if hf_model_ready(repo_id, provider):
        cached_dir = hf_cache_model_dir(repo_id) if provider == "local_mlx" else None
        resolved_dir = cached_dir or target_dir
        if progress_emit:
            progress_emit(
                model_download_progress_payload(
                    "download",
                    "이미 다운로드된 모델을 확인했습니다.",
                    percent=100,
                    downloaded_bytes=0,
                    total_bytes=0,
                    eta_seconds=0,
                )
            )
        return {"model": repo_id, "path": str(resolved_dir), "cached": True}

    resolution = resolve_download_candidates(repo_id, provider)
    mirrors = resolution["public_mirror_repos"]
    candidates = resolution["candidates"]

    if resolution["proactive_fallback"]:
        for mirror_repo in mirrors:
            if hf_model_ready(mirror_repo, provider):
                mirror_dir = (
                    hf_cache_model_dir(mirror_repo) if provider == "local_mlx" else None
                )
                resolved_mirror = mirror_dir or hf_model_dir(mirror_repo)
                if progress_emit:
                    progress_emit(
                        model_download_progress_payload(
                            "download",
                            "이미 다운로드된 공개 미러 모델을 확인했습니다.",
                            percent=100,
                            downloaded_bytes=0,
                            total_bytes=0,
                            eta_seconds=0,
                            gated_fallback=True,
                            fallback_repo=mirror_repo,
                        )
                    )
                return {
                    "model": mirror_repo,
                    "requested_model": repo_id,
                    "path": str(resolved_mirror),
                    "cached": True,
                    "gated_fallback": True,
                    "fallback_repo": mirror_repo,
                    "public_mirror_repos": mirrors,
                }

    last_error: Optional[Exception] = None
    active_repo = repo_id
    download_result: Optional[Dict[str, Any]] = None
    for idx, candidate_repo in enumerate(candidates):
        active_repo = candidate_repo
        used_fallback = candidate_repo != repo_id
        try:
            download_result = _download_single_hf_repo(
                candidate_repo,
                requested_repo=repo_id,
                provider=provider,
                progress_emit=progress_emit,
                used_fallback=used_fallback,
            )
            if used_fallback and mirrors:
                download_result["public_mirror_repos"] = mirrors
            last_error = None
            break
        except ModelRuntimeError as exc:
            if exc.status_code == 499:
                raise
            last_error = exc
            break
        except MODEL_RUNTIME_BOUNDARY_ERRORS as exc:
            last_error = exc
            has_more_candidates = idx + 1 < len(candidates)
            if has_more_candidates and (is_gated_or_auth_error(exc) or bool(mirrors)):
                logging.warning(
                    "HF download failed for %s (%s); trying public mirror fallback",
                    candidate_repo,
                    exc,
                )
                continue
            break

    if last_error is not None or download_result is None:
        err_msg = str(last_error)[-2000:] if last_error is not None else "unknown error"
        raise ModelRuntimeError(
            status_code=500, detail=f"{repo_id} 다운로드 실패: {err_msg}"
        )

    if not hf_model_ready(active_repo, provider):
        raise ModelRuntimeError(
            status_code=500,
            detail=f"{active_repo} 다운로드가 완료되지 않았습니다. 모델 파일을 찾지 못했습니다.",
        )

    return download_result
