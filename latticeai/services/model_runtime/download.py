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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.core.quiet import quiet
from latticeai.models.router import hf_cache_model_dir, hf_model_dir
from latticeai.services.model_errors import ModelRuntimeError

_AUXILIARY_GGUF_PREFIXES = ("mmproj-", "mtp-", "dflash-", "eagle3-")
GGUF_QUANT_PREFERENCE = ("q4_k_m", "q4_0", "mxfp4", "q4_k_s", "q3_k_m", "q2_k", "q8_0")


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
    except Exception:
        quiet()
    return True


def hf_model_ready(repo_id: str, provider: str = "local_mlx") -> bool:
    model_dir = hf_model_dir(repo_id)
    if provider in {"local_mlx", "vllm"} and (not model_dir.exists() or not model_dir.is_dir()):
        hf_cache_repo = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo_id.replace('/', '--')}"
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
    eta_seconds: Optional[float] = None,
    file: Optional[str] = None,
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
    if eta_seconds is not None:
        payload["eta_seconds"] = max(0, round(float(eta_seconds)))
    if file:
        payload["file"] = file
    return payload


def estimate_eta_seconds(started_at: float, percent: Optional[float]) -> Optional[float]:
    if percent is None or percent <= 0 or percent >= 100:
        return None
    elapsed = max(0.0, time.time() - started_at)
    return elapsed * (100.0 - percent) / percent


def hf_repo_files_with_sizes(repo_id: str) -> List[Dict[str, Any]]:
    from huggingface_hub import HfApi

    api = HfApi()
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
        logging.warning("huggingface model_info failed for %s: %s", repo_id, e)

    return [{"name": str(name), "size": 0} for name in api.list_repo_files(repo_id) if str(name).strip()]


def download_hf_model(
    repo_id: str,
    provider: str = "local_mlx",
    progress_emit=None,
) -> Dict[str, Any]:
    if importlib.util.find_spec("huggingface_hub") is None:
        raise ModelRuntimeError(status_code=400, detail="huggingface_hub가 없습니다. 먼저 MLX runtime 설치를 진행해 주세요.")

    target_dir = hf_model_dir(repo_id)
    if hf_model_ready(repo_id, provider):
        cached_dir = hf_cache_model_dir(repo_id) if provider == "local_mlx" else None
        resolved_dir = cached_dir or target_dir
        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "이미 다운로드된 모델을 확인했습니다.",
                percent=100,
                downloaded_bytes=0,
                total_bytes=0,
                eta_seconds=0,
            ))
        return {"model": repo_id, "path": str(resolved_dir), "cached": True}

    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download

        started_at = time.time()
        all_files = hf_repo_files_with_sizes(repo_id)
        if provider == "llamacpp":
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
        else:
            selected_files = all_files

        total_bytes = sum(int(item.get("size") or 0) for item in selected_files) or None
        downloaded_bytes = 0
        total_files = max(1, len(selected_files))
        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "모델 파일 정보를 확인했습니다.",
                percent=0,
                downloaded_bytes=0,
                total_bytes=total_bytes,
                indeterminate=total_bytes is None,
            ))

        for index, item in enumerate(selected_files, start=1):
            filename = str(item["name"])
            size = int(item.get("size") or 0)
            tqdm_class = None
            if progress_emit:
                current_percent = (
                    (downloaded_bytes / total_bytes) * 100 if total_bytes else ((index - 1) / total_files) * 100
                )
                progress_emit(model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    eta_seconds=estimate_eta_seconds(started_at, current_percent),
                    file=filename,
                    indeterminate=total_bytes is None and total_files <= 1,
                ))
                try:
                    from tqdm.auto import tqdm as base_tqdm

                    downloaded_before = downloaded_bytes
                    last_emit = {"at": 0.0, "percent": -1.0}

                    def emit_byte_progress(
                        done_bytes: float,
                        # Bound per iteration: this callback outlives the loop
                        # body when a download runs long, and late binding
                        # would report every file's progress against the last
                        # file's offsets.
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
                        if percent < 100 and now - last_emit["at"] < 0.5 and percent - last_emit["percent"] < 0.3:
                            return
                        last_emit["at"] = now
                        last_emit["percent"] = percent
                        progress_emit(model_download_progress_payload(
                            "download",
                            "모델 다운로드 중입니다.",
                            percent=percent,
                            detail=filename,
                            downloaded_bytes=aggregate,
                            total_bytes=total_bytes,
                            eta_seconds=estimate_eta_seconds(started_at, percent),
                            file=filename,
                            indeterminate=total_bytes is None and total_files <= 1,
                        ))

                    class ProgressTqdm(base_tqdm):
                        def update(self, n=1):
                            result = super().update(n)
                            emit_byte_progress(float(getattr(self, "n", 0) or 0))
                            return result

                    tqdm_class = ProgressTqdm
                except Exception:
                    tqdm_class = None
            local_path = hf_hub_download(
                repo_id=repo_id,
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
                    (downloaded_bytes / total_bytes) * 100 if total_bytes else (index / total_files) * 100
                )
                progress_emit(model_download_progress_payload(
                    "download",
                    "모델 다운로드 중입니다.",
                    percent=current_percent,
                    detail=filename,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    eta_seconds=estimate_eta_seconds(started_at, current_percent),
                    file=filename,
                    indeterminate=False,
                ))

        if progress_emit:
            progress_emit(model_download_progress_payload(
                "download",
                "모델 다운로드가 완료되었습니다.",
                percent=100,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes or downloaded_bytes,
                eta_seconds=0,
            ))
    except Exception as e:
        raise ModelRuntimeError(status_code=500, detail=f"{repo_id} 다운로드 실패: {str(e)[-2000:]}")

    if not hf_model_ready(repo_id, provider):
        raise ModelRuntimeError(status_code=500, detail=f"{repo_id} 다운로드가 완료되지 않았습니다. 모델 파일을 찾지 못했습니다.")

    return {"model": repo_id, "path": str(target_dir), "cached": False}
