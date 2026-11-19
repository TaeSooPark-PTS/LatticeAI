[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.4.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.4.0.md)

# Lattice AI 13.4.0 — Living Synthesis

> **상태: 정본(canonical)** — 현재 최신 릴리스 노트 (범용 적응형 하네스 & 100% 한/영 대등 문서화).

2026-11-20

**13.4.0 Living Synthesis**는 모델별 하드코딩 분기(**Zero per-model code branches**) 없이 소형 로컬 인스트럭션 튜닝 모델이 프론티어 급의 안정성을 발휘할 수 있도록 지원하는 **범용 적응형 에이전트 하네스(Universal Adaptive Agent Harness)**를 제공합니다. 실환경 로컬 모델 `Qwen2.5-0.5B-Instruct-Q4_K_M`을 대상으로 25개 태스크에 걸쳐 실측한 결과, 전체 작업 완수율이 **28.0%(Raw) / 24.0%(비보정 Standard)에서 84.0%로 대폭 향상(+56.0 pp / 상대 개선율 +200.0%)**되었으며, **도구 호출 80.0% (+60.0 pp)**, **웹 탐색 80.0% (+80.0 pp)**, **컴퓨터 유즈 100.0% (+100.0 pp)**, **양방향 시간 RAG 그라운딩 100.0% (+40.0 pp)**을 기록하고 비보정 표준 루프 대비 평균 지연 시간을 **63.3% 단축**했습니다. 또한 13.4.0은 대화형 언어 전환 배지를 통해 모든 GitHub 마크다운 가이드에서 **100% 영문 ↔ 한국어 대등성**을 달성했습니다.

## 주요 변경 사항

- **범용 적응형 에이전트 하네스 (`rust/lattice-agent/src/kernel/{probe.rs,agentloop/,transcript/,parse/}`):**
  - **3턴 마이크로 프로브 (`probe.rs`):** 첫 호출 시 JSON 액션 준수율, 가이드 메뉴 선택, 양방향 시간 그라운딩을 실측해 `agent_profile_probe.json`에 캐싱하고 런타임 실패 시 자동 강등을 수행합니다.
  - **관찰 우선 도구 순위화 & 도메인 분류기 (`guided/ranking.rs`, `guided/domain.rs`, `guided/args.rs`, `guided/suggestions.rs`):** 한/영 이중 언어 의도 분류와 결합해 맹목적 실행 전에 검사 도구(`computer_ui_inspect`, `web_search`, `web_fetch`, `read_file`)를 먼저 배치합니다.
  - **결정론적 UI 요소 중앙 좌표 해석 (`execution/ui_resolve.rs`):** 명명된 UI 버튼/입력창의 바운딩 박스 중앙 좌표 `(x + w/2, y + h/2)`를 정확히 산출하여 컴퓨터 유즈 성공률을 `0.0%` → `100.0%`로 끌어올렸습니다.
  - **스키마 강제 변환 & ChatML 프리필 (`execution/{repair.rs,mod.rs}`, `parse/action_salvage_text.rs`):** 깨진 JSON 구문을 복구하고 도구 별칭(`local_read` → `read_file`)을 매핑하며 토큰 생성 프리필을 지원합니다.
  - **활성 vs. 폐기 증거 카드 분리 (`transcript/rag_grounding.rs`, `transcript/summaries.rs`):** `[ACTIVE GROUNDED EVIDENCE]` 활성 사실을 추출하고 `is_superseded: true` 노드를 배제하며, 근거 부재 시 폐쇄형 기권(Abstention)을 강제합니다.
- **네이티브 웹 하네스 & 컴퓨터 유즈 브리지 (`rust/lattice-agent/src/tools/web/{mod.rs,html.rs,tests.rs}`, `latticeai/tools/{web.py,computer.py}`):**
  - Rust 웹 추출/HTML 파서를 모듈화하고 Python 워커 웹·컴퓨터 유즈 스키마 정합성을 확보했습니다(`tests/unit/test_web_and_cu_tools.py`, `rust/lattice-platform/tests/mcp_replay.rs`).
- **실측 벤치마크 스위트 & 결과 보고서 (`scripts/benchmark_adaptive_harness.py`, `rust/lattice-host/examples/harness_bench.rs`, `docs/ko/ADAPTIVE_HARNESS_BENCHMARK.md`):**
  - 로컬 `Qwen2.5-0.5B-Instruct-Q4_K_M` 대상 25개 태스크 3군 실증 벤치마크를 수록했으며, 단위 테스트(`tests/unit/test_adaptive_harness_benchmark.py`) 및 Rust 통합 테스트(`cargo test -p lattice-agent --test adaptive_harness`)로 검증됩니다.
- **100% 영문 ↔ 한국어 문서 대등성 (`docs/` & `docs/ko/`):**
  - 루트, 크레이트, GitHub 가이드, 벤치마크 보고서의 한국어 미러(`docs/ko/**`)와 양방향 언어 전환 배지를 완성했으며 `npm run docs:check-current`, `npm run docs:check-languages`, `npm run docs:check-links` 게이트로 보호됩니다.

## 릴리스 아티팩트

- `dist/ltcai-13.4.0-py3-none-any.whl`
- `dist/ltcai-13.4.0.tar.gz`
- `ltcai-13.4.0.tgz`
- `dist/ltcai-13.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.0_aarch64.dmg`
