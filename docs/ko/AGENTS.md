[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../AGENTS.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./AGENTS.md)

# LatticeAI 프로젝트 AGENTS.md

현재 릴리스: **14.7.0 — Private Scope, Verified Index**.

이 파일은 릴리스 문서 게이트(`scripts/check_current_release_docs.mjs`)의 일부입니다. 위 현재 릴리스 표기는 버전 상향(version bump)과 동일한 커밋에서 반드시 함께 업데이트되어야 하며, 이를 통해 릴리스 이후 `AGENTS.md`가 뒤처지는 현상을 방지합니다.

## 프로젝트 미션

LatticeAI는 로컬 우선(Local-first) AI 워크스페이스 플랫폼입니다.

주요 목표:

- 로컬 LLM 우선(Local LLM first)
- 지식 그래프(Knowledge Graph) 기반 워크플로
- 에이전트 런타임(Agent Runtime) 아키텍처
- 도구 레지스트리(Tool Registry) 아키텍처
- 개인 워크스페이스(Personal Workspace)
- 조직 워크스페이스(Organization Workspace)
- 보안 우선(Security-first) 설계
- 유지보수 가능한 모듈형 아키텍처

---

## 권장 리팩터링 우선순위

아키텍처 부채가 존재할 때 다음 순서로 우선순위를 둡니다.

1. `AgentRuntime` 추출
2. `ToolRegistry` 분리
3. 설정(Config) 중앙화
4. 서버 분해(Server decomposition)
5. 지식 그래프 안정화
6. 문서 동기화
7. UI 및 기능 고도화

---

## 사용자 작업 스타일

사용자는 자율적 실행(autonomous execution)을 선호합니다.

기본 행동 원칙:

- 질문하지 않고 합리적인 결정을 내립니다.
- 구현, 테스트, 빌드, 문서 동기화, 리뷰, 커밋, 푸시, 태그 생성, GitHub Release, CD 배포까지 중단 없이 진행합니다.
- 안전한 기본값이 존재할 때는 여러 선택지를 제시하지 않습니다.
- 일상적인 엔지니어링 작업에 대해 허가를 구하지 않습니다.
- 논의보다 행동을 우선합니다.
- 계획보다 구현을 우선합니다.
- 진행 상황 보고보다 작업 완수를 우선합니다.
- 최종 보고서만 제공합니다.

다음 경우에만 중단하고 확인을 요청합니다.

- 시크릿(Secrets)
- 자격 증명(Credentials)
- 프로덕션 서비스 인프라 배포
- 강제 푸시(Force push)
- 파괴적 작업(Destructive actions)
- 되돌릴 수 없는 작업(Irreversible operations)
- 안전한 기본값이 존재하지 않는 경우

---

## 실행 워크플로

기본 워크플로:

1. 분석(Analyze)
2. 설계(Design)
3. 구현(Implement)
4. 테스트(Test)
5. 빌드(Build)
6. 문서 동기화(Documentation Sync)
7. 리뷰(Review)
8. 커밋(Commit)
9. 푸시(Push)
10. 최종 보고서 작성(Final report only)

구현, 테스트, 빌드 검증, 관련 문서 동기화, 커밋, 푸시가 모두 끝나기 전까지 작업은 완료된 것이 아닙니다.

---

## 아키텍처 규칙

권장 사항:

- 의존성 주입(Dependency injection)
- 명시적 인터페이스(Explicit interfaces)
- 작고 집중된 모듈
- 런타임 컨텍스트 객체
- 레지스트리 기반 디스패치
- 설정 객체(Configuration objects)
- 전역 상태 대신 합성(Composition over global state)
- 테스트 가능한 경계(Testable boundaries)

지양 사항:

- 전역 가변 상태(Global mutable state)
- 거대한 단일 파일(Large monolithic files)
- 숨겨진 부수 효과(Hidden side effects)
- 중복된 비즈니스 로직
- 순환 의존성(Circular dependencies)
- 강한 결합(Tight coupling)

---

## 리팩터링 규칙

리팩터링 시 준수 사항:

- 코드를 새로 작성하기보다 이동하는 것을 우선합니다.
- 동작 변경이 명시적 목표가 아니라면 기존 동작을 보존합니다.
- 실용적인 범위 내에서 공개 API를 보존합니다.
- 안전할 때 죽은 코드(dead code)를 제거합니다.
- 더 이상 필요하지 않은 구형 호환성 계층을 제거합니다.
- 코드베이스를 처음보다 더 깨끗한 상태로 남깁니다.

---

## 지식 그래프 규칙

지식 그래프(Knowledge Graph) 변경 시 반드시 다음을 준수해야 합니다.

- 레거시 호환성 보존
- 파괴적 마이그레이션 지양
- 직접 변이(mutation)보다 재투영(reprojection) 우선
- 롤백 경로 유지(`rollback_entity_consolidation`, 되돌릴 수 있는 폴더 재구성)
- 동치성 및 이중 시간(bi-temporal) / 엔티티 해결 불변성 유지(`rust/lattice-core/src/{temporal.rs,entity_resolution.rs}`)
- 주장(claim) 단위 그라운딩, 대체된(superseded) 사실 배제, 분리된 RAG 평가 게이트 유지(`rust/lattice-retrieval/src/{grounding.rs,rag_eval_grounded.rs}`)
- 청크 단위 증분 동기화 및 구조화된 표/페이지/OCR 수집 불변성 유지(`rust/lattice-ingest/src/{incremental_watch.rs,incremental_sync.rs,document_structure.rs}`)
- 읽기 호환성 보존
- 마이그레이션 안전성 보존
- 단일 작성자 `GraphWriter` 보장 유지(`rust/lattice-core/src/graph_write/`)

---

## 에이전트 루프 및 내구성 워크플로 규칙 (9.6.0 / 13.2.0)

단일 에이전트 추론 루프(`latticeai/core/agent/`)와 내구성 DAG 워크플로 엔진(`rust/lattice-agent/src/kernel/workflow.rs`)은 관측 가능하고 평가됩니다. 이를 변경할 때는 다음 불변성을 유지해야 합니다.

- 모든 실행은 `LoopTrace`(`latticeai/core/agent_trace.py`)를 포함합니다: LLM 호출, 파싱 오류(복구 여부 포함), 포맷 복구, 교정, 도구 실행 결과, 재시도. API는 각 에이전트 응답에 `loop`(트레이스 요약)를 반환하므로 이를 제거하거나 우회하지 마십시오.
- 내구성 다단계 워크플로(`rust/lattice-agent/src/kernel/workflow.rs`의 `DurableWorkflowStore`)는 모든 단계 전후에 원자적으로 체크포인트를 기록하고, 재시작 간 SHA-256 `idempotency_ledger` 리플레이를 강제하며, `requires_approval` 단계를 상수 시간 `WaitingApproval` 토큰 뒤에 게이트하고, 최종 실패 시 변이 이전 파일 스냅샷을 복원하며, `USED_BY_AGENT` / `GENERATED_BY` Brain 출처(provenance) 엣지를 방출합니다.
- 무상태 워커 연산 재시도는 `latticeai/api/worker_resilience.py`(`x-lattice-idempotency-key` 병합 및 단항 타임아웃 격리)와 짝을 이루는 `WorkerSeamClient::post_idempotent_json`(`rust/lattice-core/src/worker.rs`)을 사용하며, `rust/lattice-host/tests/roadmap_section16_suite.rs`가 섹션 16.1~16.4 엔드투엔드 RAG, 이중 시간 진화, 클라우드 시크릿 파일 경계, 장애 복구 불변성을 고정합니다.
- 소형/약한 모델 허용 오차(tolerance)는 `extract_action_details`에 위치합니다: think 블록 제거, 코드 펜스 추출, 객체 슬라이싱, 후행 쉼표 복구, Python 리터럴 복구. 새로운 복구 규칙은 결정론적이어야 하며 `repairs`에 보고되어야 합니다.
- 루프 검증기(verifier)는 실패 시 안전하게 닫힙니다(fail-closed): 검증할 수 없는 결과는 성공으로 보고되는 대신 `NEEDS_REVIEW`로 귀결되어 검토 대기열(review queue)로 들어갑니다. 이 로직은 `lattice-agent`(`kernel/agentloop/verification.rs`)에 있으며 해당 크레이트의 자체 테스트로 검증됩니다. `scripts/agent_eval.py`는 존재하지 않습니다(스크립트 모델 하니스는 CI 게이트 역할이 종료되어 11.8.0에서 삭제됨).
- 12.0.0부터 가장 큰 두 크레이트는 목적별로 그룹화되었으며, 모든 변경은 해당 주제를 나타내는 그룹에 속해야 합니다. `lattice-agent`는 `kernel` / `parse` / `content` / `tools` / `surface` / `prompts`로, `lattice-platform`은 `workspaceos` / `toolsurface` / `governance` / `adminops` / `knowledge` / `modelops` / `shell`로 구성됩니다. 파일을 추가하기 전에 해당 그룹의 `mod.rs`를 읽어 무엇이 속하고 무엇이 절대 들어오면 안 되는지 확인하십시오. 크레이트 맵은 [`rust/lattice-agent/ARCHITECTURE.md`](./crates/lattice-agent-ARCHITECTURE.md)와 [`rust/lattice-platform/ARCHITECTURE.md`](./crates/lattice-platform-ARCHITECTURE.md)에 있으며, 기여자 가이드는 [`docs/DEVELOPMENT.md`](./DEVELOPMENT.md), 미해결 과제는 [`docs/ROADMAP.md`](./ROADMAP.md)에 있습니다.
- 에이전트 프로필(`standard` / `compact` / `guided`)은 모델 이름에서 추론하지 않고 `kernel/probe.rs`를 통해 **실측**합니다. 루프 어디에도 모델별 분기(per-model branch)를 추가하지 마십시오. 새로운 허용 오차는 동일하게 동작하는 모든 모델에서 작동해야 하며, 프로필 선택 다이얼은 루프 자체 파서가 실제 완성 결과를 채점하여 결정해야 합니다.
- 변경 거버넌스는 제안 우선(proposal-first, `latticeai/core/tool_governor.py` + `latticeai/services/change_proposals.py`)입니다. 추가적인 생성(additive create)은 최소한의 마찰로 실행되지만, 기존 파일의 수정/삭제는 검토 제안(출처 `change_proposal`)으로 스테이징되어 승인 시에만 검토된 내용 그대로 적용됩니다. 자율 실행 경로가 기존 사용자 콘텐츠를 직접 수정하거나 삭제하게 두지 마십시오.

---

## 커밋 전 문서 동기화

사소하지 않은 모든 git 커밋 및 푸시 전에 문서 동기화를 수행합니다.

다음 작업에는 문서 동기화가 필수입니다.

- 버전 상향(Version bumps)
- 릴리스 작업
- 아키텍처 변경
- API 변경
- CLI 변경
- UI 변경
- VS Code 확장 변경
- 보안 / 권한 / 워크스페이스 변경
- 패키징 / CI / 릴리스 워크플로 변경

### 항상 확인할 문서

- `README.md`
- `RELEASE.md`
- `docs/CHANGELOG.md`

### 관련 시 확인할 문서

- `AGENTS.md`
- `SECURITY.md`
- `docs/ENTERPRISE.md`
- `docs/EDITION_STRATEGY.md`
- `vscode-extension/README.md`
- `docs/*.md`

### 버전 참조 규칙

버전 참조를 다음 세 가지로 분류합니다.

1. 현재 릴리스 참조
2. 과거 변경 로그 / 릴리스 이력
3. 예시 플레이스홀더

규칙:

- 현재 릴리스 참조는 대상 버전과 일치해야 합니다.
- 과거 변경 로그 항목은 이전 버전을 언급한다는 이유만으로 다시 작성하지 않습니다.
- 예시 명령어는 하드코딩된 구버전 대신 `X.Y.Z` 플레이스홀더를 우선 사용합니다.
- 구버전을 가리키는 오래된 `Current release`, `Latest`, `New in` 섹션을 남기지 않습니다.
- `dist/*`를 사용하는 안전하지 않은 배포 명령어를 남기지 않습니다.
- VSIX 배포 명령어는 `dist/ltcai-X.Y.Z.vsix`를 사용해야 합니다.

### 필수 문서 점검 명령어

문서나 릴리스/버전 동작이 영향을 받을 수 있을 때 커밋 전에 다음 점검을 실행합니다.

```bash
grep -R "Current release" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "Latest" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "New in 1\." -n README.md RELEASE.md docs/*.md vscode-extension/README.md 2>/dev/null || true
grep -R "dist/\*" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "ovsx publish ltcai-" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "ltcai-[0-9]\+\.[0-9]\+\.[0-9]\+\.vsix" -n README.md RELEASE.md docs/*.md vscode-extension/README.md 2>/dev/null || true
```

릴리스 대상 버전이 정해진 경우, 이전 버전을 가리키는 오래된 현재 릴리스 참조가 없는지도 함께 점검합니다.

### 문서 업데이트 정책

코드 변경이 문서에 영향을 줄 때는 동일한 커밋에서 문서를 업데이트합니다.

명시적인 요청이 없는 한 별도의 문서 정리용 커밋을 따로 만들지 않습니다.

릴리스 커밋의 경우:

- `README.md`는 대상 버전을 현재/최신 버전으로 표시해야 합니다.
- `RELEASE.md`는 대상 릴리스를 설명해야 합니다.
- `docs/CHANGELOG.md`는 대상 릴리스 항목을 포함해야 합니다.
- GitHub Release 노트는 `RELEASE.md` / `CHANGELOG.md`와 일치해야 합니다.
- 패키지 배포 안내문은 대상 버전의 정확한 아티팩트 파일명을 사용해야 합니다.
- `twine upload dist/*`를 절대 문서화하지 마십시오.
- `dist/` 경로가 빠진 `ovsx publish ltcai-X.Y.Z.vsix`를 절대 문서화하지 마십시오.
- VS Code Marketplace 및 Open VSX 예시에는 `dist/ltcai-X.Y.Z.vsix`를 사용하십시오.

---

## 테스트 요구사항

완료 전 필수 사항:

- 영향받는 테스트 실행
- 유닛 테스트 실행
- 검증 체크 실행
- 빌드 검증 실행
- 완료 전 실패 항목 수정
- 중요한 신규 동작에 대한 테스트 추가

---

## 빌드 요구사항

해당되는 경우:

- Python 검증/빌드 체크 실행
- npm 검증/빌드 체크 실행
- 패키지 빌드 실행
- 아티팩트 정상 생성 확인
- 완료 보고 전 빌드 실패 수정

---

## 릴리스 요구사항

릴리스 작업 시:

- 모든 필수 버전 파일을 일관되게 상향
- 정확한 버전의 아티팩트만 생성
- 완료 보고 전 릴리스 아티팩트 검증
- 정확한 아티팩트 경로 사용
- 업로드 또는 릴리스 명령어에 `dist/*` 사용 금지
- 릴리스 커밋에서 `main` CI(Visual Smoke 포함)가 통과하면 `.github/workflows/release.yml`이 해당 정확한 SHA의 그린 CI 여부를 자동 검증하고, `bump_version.py --check` + `docs:check-current` + `docs:check-languages`를 실행한 뒤 `vX.Y.Z` 태그를 생성/푸시하며, 정확한 버전 아티팩트를 GitHub Release에 첨부하고 `.github/workflows/publish.yml`(CD)을 연쇄 실행합니다. 사전 릴리스 버전 상향이 자동 릴리스/CD를 즉시 트리거하지 않아야 하는 경우 커밋 메시지에 `[skip release]`를 포함하십시오.
- GitHub Markdown 문서를 언어별로 엄격히 분리하십시오(`npm run docs:check-languages`): 표준 루트 및 `docs/*.md` 파일은 순수 영어(한국어 언어 전환 배지 외부에는 한글 0자)여야 하며, 한국어 미러는 `README.ko.md` 및 `docs/ko/*.md`에 위치합니다.

예상 아티팩트 명명 규칙:

- `dist/ltcai-X.Y.Z-py3-none-any.whl`
- `dist/ltcai-X.Y.Z.tar.gz`
- `dist/ltcai-X.Y.Z.vsix`
- `ltcai-X.Y.Z.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_X.Y.Z_aarch64.dmg`

수동 패키지 배포 명령어는 대상 버전의 정확한 파일명을 사용해야 합니다.

---

## Git 요구사항

작업이 완료되면:

- `git add`
- `git commit`
- `git push`

의미 있는 커밋 메시지를 사용하십시오.

완료된 작업을 커밋하지 않은 채 남겨두지 마십시오.

커밋된 작업을 푸시하지 않은 채 남겨두지 마십시오.

---

## 금지 사항

다음 행위는 금지됩니다.

- 명시적 요청 없이 라이브 프로덕션 서버 인프라 배포
- 강제 푸시(Force push)
- 정당한 사유 없이 롤백 경로 제거
- 대규모 리팩터링 후 테스트 생략
- 대규모 리팩터링 후 빌드 생략
- 릴리스/버전 작업 후 오래된 현재 릴리스 문서 방치
- 안전하지 않은 `dist/*` 배포 명령어 문서화
- 소스 코드에 어떤 종류의 주석도 작성하지 마세요(인라인 주석, 블록 주석, 독스트링, JSDoc, `# noqa`, `# type: ignore`, `// @ts-expect-error`, `// eslint-disable` 등 린터/타입 체커 억제 지시문 포함). 항상 근본 원인이 되는 코드를 직접 수정하세요 (`npm run check:no-comments`로 강제됨).

---

## 최종 보고서

최종 보고서만 제공하십시오.

포함할 항목:

1. 완료된 작업 요약
2. 아키텍처 변경 사항
3. 변경된 파일 목록
4. 실행한 테스트
5. 빌드 결과
6. 문서 동기화 결과
7. 점검한 Markdown 파일
8. 업데이트한 Markdown 파일
9. 수정한 오래된 현재 버전 참조
10. 의도적으로 보존한 과거 버전 참조
11. 커밋 해시
12. 푸시 결과
13. 남은 기술 부채
14. 리스크 또는 후속 조치 항목
15. 다음 권장 리팩터링
