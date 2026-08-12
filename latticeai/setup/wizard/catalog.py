"""The wizard's model catalogue and the per-engine "best model" tiers.

Data plus the two pure decisions that read it: hide superseded model
*generations* (:func:`_filter_lower_family_versions`) and pick the best model a
machine with this much RAM can actually hold (:func:`_best_model_for_engine`).
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from latticeai.services.model_catalog import _version_tuple as _catalog_version_tuple

# ── Model Catalog ─────────────────────────────────────────────────────────────
# (model_id, display_name, size_gb, tag, description, min_ram_gb)
# 11.2.0: 모든 repo id 를 2026-08-10 Hugging Face API 로 확인했다 — 존재 여부,
# gated 여부, 정확한 대소문자, siblings 합계 크기. 크기 값은 측정값이다.
# 삭제: Qwen3-VL 전 라인업(2025-10, Qwen3.5/3.6 이 상위 세대), Llama 4 Scout
# (vLLM/LM Studio 경로가 gated 인 meta-llama 저장소를 가리켰다).
_MODEL_CATALOG = [
    ("mlx-community/LFM2.5-2.6B-4bit",               "LFM2.5 2.6B",          1.5,  "LLM",     "가장 가벼움 · 한국어 대화 (사진은 못 읽음)", 4),
    ("mlx-community/gemma-4-e2b-it-4bit",            "Gemma 4 E2B",          3.6,  "VLM",     "사진을 읽는 가장 작은 모델",        8),
    ("mlx-community/gemma-4-e4b-it-4bit",            "Gemma 4 E4B",          5.2,  "VLM",     "E2B 보다 한 단계 위 · 여전히 가벼움", 10),
    ("mlx-community/Qwen3.5-9B-MLX-4bit",            "Qwen3.5 9B",           6.0,  "VLM",     "중형 멀티모달 · 균형 추천",         12),
    ("mlx-community/gemma-4-12B-it-4bit",            "Gemma 4 12B",          6.8,  "VLM",     "Gemma 4 기본 추천 · 4bit",          16),
    ("mlx-community/gpt-oss-20b-MXFP4-Q8",           "GPT-OSS 20B",         12.1,  "LLM",     "범용 · 다운로드 최다 (사진은 못 읽음)", 24),
    ("mlx-community/gemma-4-26b-a4b-it-4bit",        "Gemma 4 26B A4B",     15.4,  "VLM",     "MoE · 대형 추천",                   32),
    ("mlx-community/Qwen3.6-27B-4bit",               "Qwen3.6 27B",         16.1,  "VLM+",    "dense 최상위 · 느리지만 일정함",     48),
    ("mlx-community/gemma-4-31b-it-4bit",            "Gemma 4 31B",         18.4,  "VLM+",    "Gemma 4 최대 모델",                 48),
    ("mlx-community/Qwen3.6-35B-A3B-4bit",           "Qwen3.6 35B A3B",     20.4,  "VLM+",    "MoE 최상위",                        48),
]

_CROSS_PLATFORM_MODEL_CATALOG: Dict[str, List[Tuple[str, str, float, str, str, int]]] = {
    "ollama": [
        ("ollama:hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M", "LFM2.5 2.6B Q4", 1.7, "LLM", "가장 가벼움 · 한국어 대화", 4),
        ("ollama:hf.co/ggml-org/gemma-4-E2B-it-GGUF:Q4_K_M", "Gemma 4 E2B Q4", 3.8, "VLM", "Hugging Face GGUF 기반 Gemma 4", 8),
        ("ollama:hf.co/ggml-org/gemma-4-E4B-it-GGUF:Q4_K_M", "Gemma 4 E4B Q4", 5.4, "VLM", "Hugging Face GGUF 기반 Gemma 4", 10),
        ("ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", "Gemma 4 12B Q4", 7.9, "VLM", "Hugging Face GGUF 기반 Gemma 4", 16),
        ("ollama:hf.co/ggml-org/gpt-oss-20b-GGUF:Q4_K_M", "GPT-OSS 20B Q4", 12.5, "LLM", "범용 · 다운로드 최다", 24),
        ("ollama:hf.co/ggml-org/gemma-4-26B-A4B-it-GGUF:Q4_K_M", "Gemma 4 26B Q4", 16.0, "VLM", "MoE · 대형 추천", 32),
        ("ollama:hf.co/ggml-org/Qwen3.6-27B-GGUF:Q4_K_M", "Qwen3.6 27B Q4", 16.6, "VLM+", "dense 최상위", 48),
        ("ollama:hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M", "Gemma 4 31B Q4", 18.7, "VLM+", "Gemma 4 최대 모델", 48),
    ],
    "lmstudio": [
        ("lmstudio:LiquidAI/LFM2.5-2.6B-GGUF", "LFM2.5 2.6B", 1.7, "LLM", "가장 가벼움 · 한국어 대화", 4),
        ("lmstudio:ggml-org/gemma-4-E2B-it-GGUF", "Gemma 4 E2B", 3.8, "VLM", "LM Studio GGUF Gemma 4", 8),
        ("lmstudio:ggml-org/gemma-4-E4B-it-GGUF", "Gemma 4 E4B", 5.4, "VLM", "LM Studio GGUF Gemma 4", 10),
        ("lmstudio:ggml-org/gemma-4-12B-it-GGUF", "Gemma 4 12B", 7.9, "VLM", "LM Studio GGUF Gemma 4", 16),
        ("lmstudio:ggml-org/gpt-oss-20b-GGUF", "GPT-OSS 20B", 12.5, "LLM", "범용 · 다운로드 최다", 24),
        ("lmstudio:ggml-org/gemma-4-26B-A4B-it-GGUF", "Gemma 4 26B", 16.0, "VLM", "MoE · 대형 추천", 32),
        ("lmstudio:ggml-org/Qwen3.6-27B-GGUF", "Qwen3.6 27B", 16.6, "VLM+", "dense 최상위", 48),
        ("lmstudio:ggml-org/gemma-4-31B-it-GGUF", "Gemma 4 31B", 18.7, "VLM+", "Gemma 4 최대 모델", 48),
    ],
    "vllm": [
        ("vllm:LiquidAI/LFM2.5-2.6B", "LFM2.5 2.6B", 5.2, "LLM", "가장 가벼움 · 한국어 대화", 8),
        ("vllm:google/gemma-4-E2B-it", "Gemma 4 E2B", 6.0, "VLM", "내 컴퓨터 GPU 실행 도구 권장", 12),
        ("vllm:google/gemma-4-E4B-it", "Gemma 4 E4B", 9.0, "VLM", "내 컴퓨터 GPU 실행 도구 권장", 16),
        ("vllm:Qwen/Qwen3.5-9B", "Qwen3.5 9B", 18.0, "VLM", "중형 멀티모달 · 균형 추천", 24),
        ("vllm:google/gemma-4-12B-it", "Gemma 4 12B", 24.0, "VLM", "Gemma 4 기본 추천", 32),
        ("vllm:openai/gpt-oss-20b", "GPT-OSS 20B", 13.5, "LLM", "범용 · 다운로드 최다", 24),
        ("vllm:Qwen/Qwen3.6-27B", "Qwen3.6 27B", 54.0, "VLM+", "dense 최상위 · 24GB+ VRAM 권장", 64),
        ("vllm:Qwen/Qwen3.6-35B-A3B", "Qwen3.6 35B A3B", 70.0, "VLM+", "MoE 최상위 · 24GB+ VRAM 권장", 80),
    ],
    "llamacpp": [
        ("llamacpp:LiquidAI/LFM2.5-2.6B-GGUF", "LFM2.5 2.6B GGUF", 1.7, "GGUF", "CPU/Vulkan 백업 · 가장 가벼움", 4),
        ("llamacpp:ggml-org/gemma-4-E2B-it-GGUF", "Gemma 4 E2B GGUF", 3.8, "GGUF", "Gemma 4 E2B Q4_K_M", 8),
        ("llamacpp:ggml-org/gemma-4-E4B-it-GGUF", "Gemma 4 E4B GGUF", 5.4, "GGUF", "Gemma 4 E4B Q4_K_M", 10),
        ("llamacpp:ggml-org/gemma-4-12B-it-GGUF", "Gemma 4 12B GGUF", 7.9, "GGUF", "Gemma 4 12B Q4_K_M", 16),
        ("llamacpp:ggml-org/gpt-oss-20b-GGUF", "GPT-OSS 20B GGUF", 12.5, "GGUF", "범용 · 다운로드 최다", 24),
        ("llamacpp:ggml-org/gemma-4-26B-A4B-it-GGUF", "Gemma 4 26B GGUF", 16.0, "GGUF", "MoE · 대형 추천", 32),
        ("llamacpp:ggml-org/Qwen3.6-27B-GGUF", "Qwen3.6 27B GGUF", 16.6, "GGUF", "dense 최상위", 48),
        ("llamacpp:ggml-org/gemma-4-31B-it-GGUF", "Gemma 4 31B GGUF", 18.7, "GGUF", "Gemma 4 31B Q4_K_M", 48),
    ],
}

_VERSIONED_MODEL_PATTERNS = (
    ("gemma", re.compile(r"\bgemma[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("qwen", re.compile(r"\bqwen[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
    ("llama", re.compile(r"\bllama[-\s]?(\d+(?:\.\d+)?)", re.IGNORECASE)),
)

# RAM(GB) 내림차순 티어. 첫 번째로 조건을 만족하는 항목이 "이 컴퓨터에서 가장
# 좋은 모델"이 된다. 초경량(≤8) / 경량(16) / 중형(24) / MoE(32) / 대형(48).
_BEST_MODEL_TIERS: Dict[str, List[Tuple[int, str]]] = {
    "local_mlx": [
        (48, "mlx-community/gemma-4-31b-it-4bit"),
        (32, "mlx-community/gemma-4-26b-a4b-it-4bit"),
        (24, "mlx-community/gemma-4-12B-it-4bit"),
        (16, "mlx-community/Qwen3.5-9B-MLX-4bit"),
        (8, "mlx-community/gemma-4-e2b-it-4bit"),
        (4, "mlx-community/LFM2.5-2.6B-4bit"),
    ],
    "ollama": [
        (48, "ollama:hf.co/ggml-org/gemma-4-31B-it-GGUF:Q4_K_M"),
        (32, "ollama:hf.co/ggml-org/gemma-4-26B-A4B-it-GGUF:Q4_K_M"),
        (24, "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"),
        (16, "ollama:hf.co/ggml-org/gemma-4-E4B-it-GGUF:Q4_K_M"),
        (8, "ollama:hf.co/ggml-org/gemma-4-E2B-it-GGUF:Q4_K_M"),
        (4, "ollama:hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M"),
    ],
    "lmstudio": [
        (48, "lmstudio:ggml-org/gemma-4-31B-it-GGUF"),
        (32, "lmstudio:ggml-org/gemma-4-26B-A4B-it-GGUF"),
        (24, "lmstudio:ggml-org/gemma-4-12B-it-GGUF"),
        (16, "lmstudio:ggml-org/gemma-4-E4B-it-GGUF"),
        (8, "lmstudio:ggml-org/gemma-4-E2B-it-GGUF"),
        (4, "lmstudio:LiquidAI/LFM2.5-2.6B-GGUF"),
    ],
    # vLLM serves the upstream bf16 repos, which are 3-4x the MLX 4-bit builds
    # (Qwen3.6-27B is 54GB, not 16GB), so its thresholds sit far higher than the
    # local_mlx ones. Picking by RAM alone would nominate a model no consumer
    # GPU can hold.
    "vllm": [
        (96, "vllm:Qwen/Qwen3.6-27B"),
        (32, "vllm:google/gemma-4-12B-it"),
        (24, "vllm:Qwen/Qwen3.5-9B"),
        (16, "vllm:google/gemma-4-E4B-it"),
        (8, "vllm:LiquidAI/LFM2.5-2.6B"),
    ],
    "llamacpp": [
        (48, "llamacpp:ggml-org/gemma-4-31B-it-GGUF"),
        (32, "llamacpp:ggml-org/gemma-4-26B-A4B-it-GGUF"),
        (24, "llamacpp:ggml-org/gemma-4-12B-it-GGUF"),
        (16, "llamacpp:ggml-org/gemma-4-E4B-it-GGUF"),
        (8, "llamacpp:ggml-org/gemma-4-E2B-it-GGUF"),
        (4, "llamacpp:LiquidAI/LFM2.5-2.6B-GGUF"),
    ],
}



#: The engine catalog owns this parse; the wizard compares the same strings.
_version_tuple = _catalog_version_tuple


def _catalog_row_family_version(row: Tuple[str, str, float, str, str, int]) -> Tuple[str, Tuple[int, ...]] | None:
    text = f"{row[0]} {row[1]}"
    for family, pattern in _VERSIONED_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            # Major version only — see model_catalog._model_family_version. The
            # filter hides superseded *generations*; Qwen3.5 and Qwen3.6 are one
            # generation filling two different RAM tiers and must coexist.
            version = _version_tuple(match.group(1))[:1]
            if version:
                return family, version
    return None


def _filter_lower_family_versions(
    rows: List[Tuple[str, str, float, str, str, int]],
) -> List[Tuple[str, str, float, str, str, int]]:
    max_versions: Dict[str, Tuple[int, ...]] = {}
    detected: List[Tuple[Tuple[str, str, float, str, str, int], Tuple[str, Tuple[int, ...]] | None]] = []
    for row in rows:
        version_info = _catalog_row_family_version(row)
        detected.append((row, version_info))
        if not version_info:
            continue
        family, version = version_info
        if version > max_versions.get(family, (0,)):
            max_versions[family] = version
    return [
        row for row, version_info in detected
        if not version_info or version_info[1] >= max_versions.get(version_info[0], version_info[1])
    ]


def _best_model_for_engine(engine: str, ram_gb: float, rows: List[Tuple[str, str, float, str, str, int]]) -> str:
    available_ids = {row[0] for row in rows}
    for min_ram, model_id in _BEST_MODEL_TIERS.get(engine, []):
        if ram_gb >= min_ram and model_id in available_ids:
            return model_id
    return rows[0][0] if rows else ""
