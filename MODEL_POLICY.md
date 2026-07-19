# Model Policy

Lattice AI v9.5.0 uses a multimodal-first model policy. The Brain is the
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

- Gemma 4
- Qwen3-VL
- Llama 4

## Removed From Current Recommendation Catalogs

- MLX-LM as a local text-only execution path
- Gemma 2 and Gemma 3
- Qwen2.5-VL
- SmolLM
- Phi
- Mistral
- DeepSeek
- GPT-OSS
- Llama 3.x

Existing user data is not deleted by this policy. The current recommendation
surface changes so new users are not asked to choose between old and new model
generations.
