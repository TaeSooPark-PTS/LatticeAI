"""Finding a locally downloaded model on disk.

Three places a model may already be: an explicit path, this app's own
``~/.ltcai/hf-models`` directory, or the shared Hugging Face cache. A directory
only counts when it actually holds a config, weights and a tokenizer — a
half-finished download must not be handed to the loader as if it were a model.
"""

import json
import re
from pathlib import Path
from typing import Optional

HF_MODELS_ROOT = Path.home() / ".ltcai" / "hf-models"


def hf_model_dir(repo_id: str) -> Path:
    return HF_MODELS_ROOT / repo_id.replace("/", "__")


def hf_cache_model_dir(repo_id: str) -> Optional[Path]:
    """Return a usable Hugging Face cache snapshot for an already-downloaded model."""
    cache_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo_id.replace('/', '--')}"
    snapshots = cache_root / "snapshots"
    if not snapshots.exists():
        return None
    candidates = sorted(
        (item for item in snapshots.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for snapshot in candidates:
        if _looks_like_hf_model_dir(snapshot):
            return snapshot
    return None


def _looks_like_hf_model_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    has_config = (path / "config.json").exists()
    has_weights = any(path.glob("*.safetensors")) or any(path.glob("*.bin"))
    has_tokenizer = (
        (path / "tokenizer.json").exists()
        or (path / "tokenizer.model").exists()
        or (path / "tokenizer_config.json").exists()
    )
    return has_config and has_weights and has_tokenizer


def _resolve_local_hf_model(model_id: str) -> str:
    explicit_path = Path(model_id).expanduser()
    if explicit_path.exists():
        return str(explicit_path)
    local_dir = hf_model_dir(model_id)
    if _looks_like_hf_model_dir(local_dir):
        return str(local_dir)
    cached_dir = hf_cache_model_dir(model_id)
    if cached_dir is not None:
        return str(cached_dir)
    return model_id


def _is_gemma4_model_id(model_id: str) -> bool:
    raw = str(model_id or "").lower()
    return bool(re.search(r"gemma[-_/ ]?4|gemma4", raw))


def _local_model_type(path_or_model_id: str) -> Optional[str]:
    raw = str(path_or_model_id or "").strip()
    candidates = []
    explicit = Path(raw).expanduser()
    if raw and explicit.exists():
        candidates.append(explicit / "config.json")
    candidates.append(hf_model_dir(raw) / "config.json")
    for config_path in candidates:
        try:
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                model_type = str(data.get("model_type") or "").strip().lower()
                if model_type:
                    return model_type
        except Exception as e:
            print(f"⚠️ Model config read skipped for {config_path}: {e}")
    return None
