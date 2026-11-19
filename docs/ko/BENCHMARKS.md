[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../BENCHMARKS.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./BENCHMARKS.md)

# 모델 강건성 벤치마크 (Model Robustness Benchmarks)

> 상태: 기준 스냅샷 (2026-07-21)
>
> **활성 CI 검색 베이스라인 게이트:** `scripts/bench_retrieval_baseline.py --check-budgets`가 모든 CI 워크플로(`ubuntu-latest`, Python 3.11 + `hnswlib`)에서 실행되어 HNSW recall@10(`>= 0.90`), p95 쿼리 지연 시간(`<= 50 ms`), 증분 삽입/삭제 사이드카 갱신 지연 시간(`<= 250 ms`)을 강제합니다. 로컬에서는 다음 명령어로 실행할 수 있습니다:
> `.venv/bin/python scripts/bench_retrieval_baseline.py --check-budgets --json bench-retrieval.json`.
>
> **아래 내용은 과거 시점의 기록 스냅샷입니다.** 이 페이지의 나머지 부분에서 설명하는 모델 출력 복구 하니스(`scripts/bench_models.py`)는 11.5.1에서 Python 에이전트 루프가 Rust로 포팅될 때 함께 정리되었으며, `latticeai.core.agent_eval` 및 `scripts/agent_eval.py`도 뒤이어 제거되었습니다(11.8.0에서 최종 삭제). 아래의 측정 방법론과 정직성 노트는 당시 수치가 어떻게 산출되었는지 보여주는 기록으로 보존됩니다.

이 문서는 모델 품질 티어별로 에이전트 루프가 모델 출력을 얼마나 잘 실행 가능한 액션으로 변환하는지 측정했던 하니스인 `scripts/bench_models.py`의 방법론과 결과를 설명합니다. [CI 및 릴리스 게이트](./CI_AND_RELEASE_GATES.md)의 보조 문서입니다.

## 측정 항목

제품의 핵심 주장은 Brain이 "어떤 AI 모델에서도" 영속성을 유지한다는 것입니다. 약한 모델은 다소 부정확한 형식의 출력을 내놓더라도 루프가 이를 복구하여 작업을 완수합니다. 하니스는 이를 티어별 세 가지 수치로 정량화합니다.

- **success_rate** — 유효한 에이전트 액션으로 변환되어 작업을 계속 진행할 수 있게 된 모델 출력의 비율.
- **repair_rate** — 파싱에 성공한 출력 중 루프의 관대한 파서(tolerant parser)가 출력을 복구했기 *때문에* 성공한 비율. 이 값이 높을수록 해당 모델 티어가 루프의 강건성에 크게 의존함을 의미합니다.
- **latency_ms** — 아래의 지연 시간 정직성 노트를 참조하십시오.

모든 출력은 프로덕션 루프가 실제로 사용하는 파서인 `latticeai.core.agent.extract_action_details`(계획/실행/검증 단계에서 `agent.py`가 호출)에 그대로 전달됩니다. 하니스는 파싱 로직을 별도로 재구현하지 않고 실제 배포 코드를 측정합니다. 알려진 복구 허용 오차(tolerance)는 다음과 같습니다: `think_strip`(`<think>` 블록 제거), `fence`(```` ```json ```` 언래핑), `slice`(산문 텍스트에서 `{...}` 구간 추출), `trailing_comma`(후행 쉼표 제거), `python_literal`(작은따옴표 딕셔너리에 대한 `ast.literal_eval`).

추가로 `agent-loop` 참조 행은 20개의 스크립트 시나리오에 대해 실제 `latticeai.core.agent_eval.run_agent_eval` 상태 머신을 실행하고 성공률과 복구율을 보고합니다.

## 지연 시간(latency)에 대한 정직성 노트

**scripted** 모드에서 `latency_ms`는 오직 **파싱 및 복구에 걸린 비용**(마이크로초 단위)만을 의미합니다. 이는 모델 추론 시간이 **아니며** 그렇게 해석되어서는 안 됩니다. 실제 생성 지연 시간은 실제 엔드포인트를 대상으로 하는 **live** 모드에서만 의미가 있습니다.

## 실행 모드

1. **scripted** (기본값; 모델 및 네트워크 불필요): 티어별로 실제 모델 출력에 가깝게 엄선된 코퍼스를 사용합니다. 결정론적(deterministic)으로 동작하며 하니스의 정상 작동 여부를 증명하고 루프의 복구 경계를 드러냅니다. 일부 약한 티어(weak-tier) 항목은 복구 경계를 넘도록 *의도적으로 손상된* 형태를 포함하므로 `success_rate < 1.0`은 조작된 데모가 아니라 실제 복구 한계를 보여주는 결과입니다. 자체 점검(self-check)은 모든 코퍼스 항목의 예상 파싱/실패 결과가 실제 결과와 일치하는지 검증합니다(불일치 0 = 정상).
2. **live** (옵트인): 고정된 프롬프트 세트를 OpenAI 호환 로컬 엔드포인트(LM Studio / llama.cpp / vLLM)로 전송하고 실제 완성 결과를 동일한 파서에 통과시켜 **실제** 생성 지연 시간을 보고합니다. 엔드포인트에 연결할 수 없는 경우 명확한 `"SKIPPED"` 줄로 폴백합니다.

## 실행 방법

```bash
# 스크립트 매트릭스 실행 (항상 동작):
.venv/bin/python scripts/bench_models.py

# 기계 판독 가능한 JSON 출력 저장:
.venv/bin/python scripts/bench_models.py --json bench.json

# 실제 로컬 모델 대상 실행 (OpenAI 호환 엔드포인트):
.venv/bin/python scripts/bench_models.py \
    --live-endpoint http://127.0.0.1:1234/v1 --model my-local-model
```

종료 코드(exit code)는 하니스 자체 점검에서 불일치(실제 버그)가 발견된 경우에만 0이 아닌 값을 반환합니다. 약한 모델 티어에서의 낮은 점수는 측정 결과일 뿐 실행 실패가 아닙니다.

## 기준 결과 (scripted, 2026-07-21)

메인테이너 머신에서 측정된 결과이며 지연 시간은 파싱 전용 비용입니다.

| 티어 | success_rate | repair_rate | n | 작동한 복구 규칙 |
|------|-------------:|------------:|--:|-------------------|
| frontier (깨끗한 JSON) | 1.00 | 0.00 | 6 | — |
| mid-local (코드 펜스/산문/쉼표) | 1.00 | 1.00 | 6 | fence×3, slice×1, trailing_comma×2 |
| weak-local (think/py-literal/손상됨) | 0.625 | 1.00 | 8 | think_strip×2, python_literal×2, fence×1, trailing_comma×1 |

에이전트 루프 참조(실제 상태 머신): 20개 시나리오에서 **success_rate 1.00**, **recovery_rate 0.70**. 하니스 자체 점검: **불일치 0건(0 mismatches)**.

매트릭스 해석: 중간 티어(mid-local)는 100% 복구 가능합니다. 모든 출력에 복구가 필요했지만 전부 성공했으며, 이는 "다양한 모델에서 영속적으로 작동한다"는 주장이 실제로 어떻게 작동하는지를 보여줍니다. 약한 티어(weak-local)의 0.625는 정직한 경계선입니다. 실제로 손상된 3개의 출력(순수 산문, `action` 필드 누락, 잘린 중괄호)은 파싱될 수 없고 파싱되어서도 안 됩니다.

## 측정 가능한 것과 측정하지 않는 것 (정직한 범위)

**이 하니스가 측정할 수 있는 것:**
- 주어진 모델의 원시 출력이 루프 파서를 통과하는지 여부(`success_rate`).
- 루프의 복구 계층이 얼마나 개입하고 있는지(`repair_rate` 및 작동한 복구 규칙 종류).
- `live` 모드에서의 실제 엔드투엔드 생성 지연 시간.
- 스크립트 시나리오 스위트 전반의 엔드투엔드 루프 완료율.

**이 하니스가 측정하지 않는 것:**
- 작업 *정확도* 또는 답변 품질 — 액션의 형식이 올바르고 실행 가능한지만 측정하며 그 액션이 *올바른* 선택인지는 평가하지 않습니다.
- 스크립트 시나리오를 넘어서는 다중 턴 추론 품질이나 도구 선택 정확도.
- 동시성 환경 또는 다른 하드웨어에서의 처리량/지연 시간.
- 클라우드 모델(API 키를 사용하지 않으며 `live` 모드는 로컬 엔드포인트만 대상으로 합니다).
- "모든 모델"을 대표하는 통계적 표본 — 스크립트 티어는 대표 전형(archetype)일 뿐 통계적 모집단이 아닙니다. 특정 모델의 실제 수치를 얻으려면 `live` 모드를 사용하십시오.
