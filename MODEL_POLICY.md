# Model Policy

Lattice AI v9.6.0 uses a multimodal-first model policy. The Brain is the
durable product surface; model choice is a replaceable execution voice that must
preserve source facts, memory proof, and model-continuity evidence.

## Recommendation Rules

- Recommend current-generation multimodal models only.
- Do not recommend text-only local models.
- Do not keep old same-family generations as compatibility fallback when a newer
  generation is available.
- Use small or quantized multimodal models for low-spec machines.
- Explain each recommendation in plain language.
- Show source facts before the model is used.

## Source Facts

Every recommended model must expose these fields in this order:

1. 제작 국가
2. 제작 회사
3. 실행 방식
4. 인터넷 사용 여부
5. 모델명

## Current Families

- Gemma 4 (E2B / E4B / 12B / 26B A4B / 31B)
- Qwen3.6 (27B dense, 35B A3B MoE)
- Qwen3.5 (9B)
- GPT-OSS (20B, text only)
- LFM2.5 (2.6B, text only, multilingual)

Text-only families are listed because two RAM tiers need them: LFM2.5 is the
only entry that runs comfortably on 8GB, and GPT-OSS 20B is the most-downloaded
model in the catalog. Each entry states its own modality, so "reads pictures"
is never implied.

## Removed From Current Recommendation Catalogs

- Gemma 2 and Gemma 3 (Gemma 3's own repos are gated)
- Qwen2.5-VL and Qwen3-VL
- Llama 3.x and Llama 4 (Meta's repos are gated)
- Pixtral (vLLM-only, no `config.json` published)
- Phi Vision and Moondream2 (both repos are **gone from the Hub**)
- SmolLM, DeepSeek

## Two Lists, Not One

A model can be *offered* or merely *understood*:

- **Recommended** — current generation. Appears in the catalog, the RAM tiers
  and the download paths.
- **Recognised** — superseded but still real and still loadable. Never offered,
  never recommended, but kept in the registry so weights a user already
  downloaded keep their name, size and runtime profile instead of appearing as
  an unknown blob.

Models that no longer exist on the Hub, or that need credentials to fetch, are
deleted from both lists. Recognising something nobody can obtain is not
compatibility, it is noise.

Existing user data is not deleted by this policy. The current recommendation
surface changes so new users are not asked to choose between old and new model
generations.

## Verification

`scripts/verify_hf_model_registry.py` re-measures every entry against the public
Hugging Face API — existence, gated flag, canonical casing, `library_name`/tags,
config architecture, sibling files and their exact byte sum. It **never
downloads weights and never loads a model**; there is no flag that could.

Its loadability verdict is therefore *static*: MLX library signal + a config
architecture with a loader in mlx-lm / mlx-vlm + community downloads. That means
"nothing published about this repo rules out a load", not "this loaded". The
loader plus the on-device smoke test remain the only authority on whether a
model really runs.
