# v6.4.0 Digital Brain Quality Hardening Baseline

## 개요
이 문서는 v6.4.0 Digital Brain Quality Hardening 작업의 baseline, 작업 분해, 검증 항목을 정리한 근거 중심 문서입니다. 초기 baseline은 코드 변경 없이 작성되었고, 이후 구현 단계의 위험 등록부와 검증 기준을 함께 추적합니다.

## Baseline Scope
- Digital Brain 품질 강화의 핵심 영역: Knowledge Graph 안정성, Memory provenance, Brain Home/Review Center 연동 품질, Local-first Brain loop 완전성
- 이전 릴리스(v6.3.1)에서 남은 Brain 관련 기술 부채와 품질 갭 식별
- Evidence-based 접근: RELEASE.md, docs/v6/* 품질 스코어카드, 아키텍처 리뷰 문서 기반

## Baseline Findings (조사된 근거 요약)
- Brain Core 구조는 v6 QUALITY_SCORECARD에서 78점 수준으로 평가됨. provenance, archive, first-memory loop에 대한 명확한 UX/데이터 계약 필요
- Review Center와 Brain Home 간의 Run Now / memory routing evidence가 부분적으로 존재하나, Digital Brain 품질 관점에서 end-to-end 검증 부족
- Knowledge Graph reprojection 및 dual-write 보장 메커니즘이 legacy 호환을 유지하면서 v6.4에서 hardening 대상
- 출력 증거(output/release/) 및 스크린샷/GIF는 v6.4.0 release evidence로 갱신되었으며, Brain 품질 특화 시나리오(빈 Brain 상태, provenance 추적, Graph routing)는 후속 deep evidence 대상

## v6.4.0 작업 분해 (Work Breakdown)
1. **Brain Quality Baseline 수립**
   - Digital Brain 동작의 핵심 지표 정의 (memory save-recall-backup loop, provenance 표시, Graph/Review 연동)
   - 기존 QUALITY_SCORECARD를 Brain 특화 버전으로 확장

2. **Knowledge Graph Hardening**
   - reprojection 우선 전략 문서화
   - rollback path 및 equivalence test coverage 확인
   - legacy read compatibility 보장 증거 수집

3. **Memory & Provenance 품질 강화**
   - source type, retry/failure reason, Brain/Graph routing 결과의 명시적 표시
   - empty Brain first-loop 시나리오 검증 항목 정리

4. **Review Center / Brain Home 연동 품질**
   - Run Now가 preview/regenerate 계약을 유지하면서 Brain 상태에 미치는 영향 검증
   - snooze/unsnooze 및 filter 동작의 Brain 데이터 일관성 확인

5. **검증 및 문서 동기화**
   - docs/CHANGELOG.md, RELEASE.md에 v6.4.0 Brain Quality 항목 반영
   - stale version reference 정리 (v6.3.x → v6.4.0)

## 검증 항목 (Verification Items)
- [ ] Knowledge Graph legacy compatibility 및 migration safety 테스트 결과 문서 첨부
- [ ] Brain Home empty state + first memory save-recall-backup loop 시각 증거
- [x] Review Center → Brain routing surface 확인 (screenshot/GIF)
- [x] RELEASE.md 및 docs/v6.4/ 하위 문서의 Current release reference가 6.4.0으로 일치
- [ ] No-Fake-100 규칙 준수: 모든 항목이 evidence-backed일 때만 완료 선언
- [ ] Architecture Review / UX Review에서 Brain 품질 관련 피드백 반영 여부

## Remaining Gaps (초기 식별)
- Brain 품질 메트릭 자동 수집 파이프라인 부재
- Multi-agent runtime과 Digital Brain 간의 명시적 계약 문서화 필요
- End-to-end Brain Quality E2E 시나리오 스크립트/증거 부족

## Risk Register

- **Workspace scoping leakage**: Graph, search, context, and memory mutation
  paths must carry the same `allowed_workspaces` / owner boundary as the
  existing scoped search API. Otherwise one workspace can read or delete another
  workspace's Brain data.
- **Memory deletion consistency**: Memory Manager prune/clear operations must
  intersect explicit ids and kind-based deletes with the caller's scoped memory
  set. Global graph clearing from Memory Manager is unsafe until a scoped graph
  delete path exists.
- **Embedding/index consistency**: Provider/model/dimension changes must be
  surfaced as drift or stale-index signals rather than silently returning empty
  vector results. Hash embeddings must remain labelled fallback.
- **Context injection and attribution**: Retrieved titles/summaries must remain
  source-attributed and confidence-labelled so unsupported or stale facts are
  not presented as certain.
- **Regression coverage gap**: Multi-workspace graph reads, graph relationship
  reads, memory prune/clear ownership, quality benchmark metrics, and structured
  context guardrails require direct unit coverage before release completion.

## Implemented Evidence

- `lattice_brain.quality` adds a local-first quality layer for fallback
  embedding labels, drift/re-index plans, BM25 and hybrid fusion, local rerank
  hooks, memory candidate quality, graph edge quality, structured context
  guardrails, and retrieval benchmark metrics.
- Workspace-scoped graph/search/memory paths now pass caller workspace
  boundaries through graph, node, relationship, hybrid retrieval, and memory
  mutation operations.
- Regression tests cover Brain quality primitives, multi-workspace graph reads,
  graph node/relationship filtering, scoped knowledge-graph routes, and scoped
  Memory Manager prune/clear behavior.

## 참고 문서
- docs/v6/QUALITY_SCORECARD.md
- docs/v6/ARCHITECTURE_REVIEW.md
- RELEASE.md (v6.3.1 섹션)
- docs/CHANGELOG.md

이 문서는 baseline과 구현 증거를 함께 추적하며, 완료 선언은 테스트/빌드/문서 검증 결과에 근거해야 합니다.
