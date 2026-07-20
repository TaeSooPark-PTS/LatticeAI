# LatticeAI 전체 코드·문서·하네스·루프·사용자 경험 리뷰

> 검토일: 2026-07-20
> 대상: `main` @ `3bf0653d6c56` (`v9.8.0`, *Honest Knowledge Pipeline*)
> 범위: Python/FastAPI·React/Vite·Tauri·VS Code 확장·CI, 제품/운영 문서,
> 에이전트 루프와 평가 하네스, 설치·Brain·검토 큐의 사용자 흐름

## 결론

LatticeAI는 이미 단순한 데모를 넘어선, **로컬 우선 AI 워크스페이스의
제품 골격과 검증 문화가 매우 강한 코드베이스**다. 의존성 주입 기반의
런타임 경계, 가시적인 `LoopTrace`, 결정론적 에이전트 평가, 격리된 통합
하네스, 실제 제품 흐름을 보는 시각 회귀 테스트는 특히 좋은 기반이다.

다만 현재의 “신뢰 가능한 에이전트/정직한 지식 파이프라인” 약속에는 두
가지 P0 보완이 필요하다. 승인 대기 중 바뀐 파일을 오래된 제안이 덮어쓸 수
있고, critic 출력 파싱 실패가 `PASS`로 처리되어 실제 검증 실패를 성공으로
표시할 수 있다. 이 둘을 해소하기 전에는 자동 변경과 완료 신호를 완전히
신뢰 가능한 것으로 홍보하거나 확대하지 않는 편이 맞다.

이 문서는 기능 변경 제안이 아니라, 현 상태의 근거와 우선순위를 남기는
리뷰 기록이다. 외부 침투 테스트, 실제 모델별 장기 벤치마크, 실사용자
인터뷰, 라이브 PostgreSQL 서비스 검증은 범위 밖이었다.

## 검토 근거와 실행 결과

| 항목 | 결과 |
| --- | --- |
| 코드 규모 | 추적된 Python 393개, `latticeai/` 약 45,245 LOC, Markdown 100개 |
| 원격 상태 | GitHub 기본 브랜치 `main`; 검토 시점의 열린 PR/이슈 없음 |
| 문서 게이트 | 링크 검사와 현재 릴리스 검사 통과 |
| Python 정적 검사 | 1,168 모듈 문법 검사 통과 |
| 에이전트 평가 | 16/16 시나리오 통과; 파싱 오류 6건 중 5건 복구 |
| Brain/제품 게이트 | 합성 Brain corpus 및 product-readiness 모두 통과 |
| 테스트 | frontend 27개, Python unit 1,263개, integration 13개 통과; PostgreSQL 경로 1개 skip |
| 빌드/시각 회귀 | 패키지·frontend build 통과, Playwright 시각 시나리오 18개 통과 |

`npm run build`의 번들은 성공했지만, 초기 `index` 청크는 약 632.9 kB
(gzip 180.3 kB)이며 Vite의 500 kB 경고가 남았다. Python unit 실행에는
12개의 레거시 호환 shim 관련 경고가 남았고, 실패는 아니었다.

## 강점

| 영역 | 강점 | 사용자/운영 가치 |
| --- | --- | --- |
| 로컬 우선 제품 경계 | FastAPI sidecar, React UI, Tauri, 확장 기능이 분리되고 `app_factory`/런타임 의존성이 명시적이다. | 네트워크 서비스에 종속되지 않고 개인 작업 공간에서 시작할 수 있다. |
| 에이전트 관측성 | `LoopTrace`가 LLM 호출, repair, tool outcome, retry를 응답에 남긴다. | “왜 이렇게 했는가”를 사후에 추적하고 지원할 수 있다. |
| 약한 모델 내성 | think block/fence/object slicing/trailing comma/Python literal 등 결정론적 repair와 평가 시나리오가 있다. | 완벽하지 않은 로컬 모델도 즉시 실패시키지 않고 안전하게 다룬다. |
| 변경 거버넌스 방향 | 기존 파일 변경을 proposal/review로 보낼 구조와 review UI가 있다. | 사용자가 에이전트 변경을 확인할 수 있다는 핵심 신뢰 모델을 만든다. |
| 데이터 경계 | scoped retrieval, 기본 deny Telegram, 서명·TTL invite cookie가 이전 취약한 경로를 개선했다. | 개인·조직 workspace 혼동과 원격 채널 오남용을 줄인다. |
| 테스트 하네스 | temp sandbox, loopback-only integration, scripted model, visual product-flow 회귀가 함께 있다. | 기능만이 아니라 실행 환경과 UI 계약도 반복 검증한다. |
| 제품 언어 | “Brain”, capture, proof, graph, review라는 사용자의 정신 모델이 비교적 일관된다. | 파일/채팅/에이전트 기능이 하나의 작업 공간으로 읽힌다. |

## 우선 개선 사항

우선순위는 P0(신뢰·데이터 보존을 먼저 막음), P1(출시 품질과 사용자
기대를 맞춤), P2(확장성과 측정 성숙도) 순서다.

### P0 — 오래된 변경 제안이 최신 사용자 파일을 덮어쓸 수 있음

`ChangeProposalService.propose_file_update`는 diff를 만들 때의 원본을 읽지만
(`latticeai/services/change_proposals.py:156-195`), proposal에는 새 내용과 byte
수만 남긴다. 승인 시 `approve_and_apply`는 현재 파일이 달라졌는지 확인하지
않고 `write_text`/삭제를 수행한다(`:273-297`). 따라서 사용자가 제안 생성 후
직접 파일을 수정하면, 나중의 “승인”이 사용자의 새 수정을 조용히 덮어쓸 수
있다. 중복 승인 요청도 서비스 자체에서 원자적으로 막지 못한다.

**개선:** proposal 생성 시 원본 SHA-256과 파일 존재 상태를 저장하고, 승인
직전에 현재 상태와 비교한다. 다르면 409 conflict와 재베이스 제안을 반환한다.
승인 상태 예약·안전한 임시 파일/원자적 replace·최종 상태 전이를 하나의
보호된 작업으로 묶는다.

**수락 기준:** 생성 뒤 수정·삭제·재생성, 동시 두 번 승인, 원본이 없는 새
파일의 각 경우를 테스트한다. 어떤 경우에도 승인자가 검토하지 않은 내용이
기존 파일을 바꾸지 않아야 한다.

### P0 — critic 파싱 실패가 검증 통과로 처리됨

에이전트가 `VERIFYING` 상태일 때 critic JSON 파싱이 실패하면 trace에는
recovered parse error를 남기면서도 `PASS`/`DONE`을 만들어 낸다
(`latticeai/core/agent.py:599-602`). 이후 `PASS` 또는 `DONE`이면 완료로
처리한다(`:619-622`). 즉 verifier가 응답하지 못해도 사용자에게 작업 완료가
표시될 수 있다.

**개선:** critic 파싱 실패는 최대 한 번의 엄격한 repair/retry 뒤
`NEEDS_REVIEW` 또는 `VERIFICATION_UNAVAILABLE`로 종료한다. 완료 상태는
결정론적 plan/tool evidence와 유효한 critic verdict가 함께 있을 때만 낸다.

**수락 기준:** garbage critic, timeout, 형식은 맞지만 evidence가 없는 PASS,
완료 전 tool failure를 포함한 평가를 추가한다. 이 경우 `DONE`이 아니라
명확한 비성공 상태와 사용자 행동(재시도/검토)을 반환해야 한다.

### P1 — proposal-first 보장이 모든 변경 도구에 닿지 않음

중앙 `tool_governor`는 `write_file`, `local_write`, 문서 생성 도구 등을
변경 도구로 분류하지만, `ChangeProposalService`가 직접 다루는 도구는
`write_file`과 `edit_file`뿐이다. 도구 레지스트리에는 `local_write`,
`create_docx`/`xlsx`/`pptx`/`pdf`, 웹 프로젝트 생성 등 다른 mutator도 있다.
일반 정책이 사람 승인을 요구하는 경우가 있더라도, README의 “기존 콘텐츠
편집은 항상 검토 가능한 proposal”이라는 문구와 실제 적용 범위가 같다는
보장은 아직 없다.

**개선:** 모든 mutating tool의 inventory를 CI에서 유지하고 각 도구를
`new artifact`, `existing-content update`, `delete`, `external side effect`로
분류한다. 기존 콘텐츠의 update/delete는 단일 proposal 경로, 새 artifact는
명시된 workspace policy, 외부 side effect는 별도 confirmation으로 강제한다.
지원하지 않는 도구는 fail-closed로 막는다.

### P1 — 설치 분석 실패가 “지원되는 준비된 모델” 추천으로 보임

`ProductFlow`는 분석 오류를 state에 저장하지만 화면에 전달하지 않는다
(`frontend/src/components/ProductFlow.tsx:33,44-59`). 추천 목록이 비면
`RecommendationScreen`과 설치 동작이 고정 fallback 모델을 만든다. 그 fallback은
`Qwen3 8B`, `supported: true`, `downloadRequired: false`로 표시된다
(`frontend/src/lib/recommendationModel.ts:68-87`). API probe 실패, 부분 실패,
MLX 미지원 장비에서도 사용자는 “내 장비에서 준비됨”으로 오해할 수 있다.

**개선:** 분석 상태를 `loading | ready | unavailable`로 모델링하고, 실패 시
오류 원인·재시도·“지금은 모델 없이 계속”만 보여 준다. 알 수 없는 환경에서
지원/설치 완료/성능 시간을 추정한 recommendation을 만들지 않는다.

**수락 기준:** 모든 endpoint 실패, 부분 결과, MLX 미지원 호스트, 빈 결과를
UI 테스트로 고정한다. 실패 상태에는 `supported: true` fallback이 렌더되지
않아야 한다.

### P1 — 현행 문서와 문서 게이트의 범위가 어긋남

릴리스 marker 검사는 통과했지만 고정된 파일의 “Current release”만 확인한다.
링크 검사는 README에서 직접 연결된 한 단계 문서만 순회한다. 그래서 다음과
같은 실제 운영 혼선을 잡지 못한다.

| 문서 | 관찰 | 조치 |
| --- | --- | --- |
| `docs/OPERATIONS.md` | v9.6 헤더와 Markdown graph/비격리 storage 설명이 현 SQLite·workspace scope 구조와 다르다. | 실행 절차를 현재 구조로 갱신하거나 historical 문서로 명시한다. |
| `docs/FEATURE_STATUS.md` | 헤더와 release narrative가 9.6 중심이다. | 현재 상태 표와 release reference를 9.8에 맞춘다. |
| `docs/DEVELOPMENT.md` | 9.6 artifact 안내와 “history through 9.6” 표현이 남아 있다. | 현재 개발/패키징 지침으로 갱신한다. |
| `docs/spec-vs-impl.md` | Tauri 부재 등 이미 바뀐 구현 상태를 live gap처럼 쓴다. | historical snapshot 표기를 하거나 현행 gap ledger로 다시 쓴다. |
| `docs/PERFORMANCE.md` | 2026-07-20 측정에 v9.6 working tree라고 적혀 있다. | 수치의 측정 provenance를 9.8로 정정한다. |
| `SECURITY.md` | 보안 모델 버전이 9.6에 머문다. | 현 보안 모델 또는 historical 범위를 명시한다. |

`docs/architecture.md`는 스스로 v3.6 historical snapshot이라고 밝히므로
오류가 아니라 보존 대상이다. 반대로 `ARCHITECTURE.md`는 현행 canonical
architecture로 유지하는 현재 방식이 좋다.

**개선:** 문서마다 `canonical/current`, `reference`, `historical` 상태를
front matter 또는 상단 배지로 정하고, 현재 문서는 전체 `docs/**/*.md`의
상대 링크와 version/architecture assertion을 검사한다. historical 문서는
의도적으로 version 보존을 허용한다.

### P2 — 하네스가 “안전한 종료”와 “정답 완료”를 같은 성공으로 셈

`scripts/agent_eval.py`는 16개의 좋은 결정론 시나리오를 갖지만 scripted
model과 fake change governor를 쓴다. 특히 `unrecoverable-garbage-still-terminates`
시나리오는 tool outcome 없이 `DONE`을 기대한다(`:278-282`). 이 측정은
루프가 멈춘다는 점은 증명하지만, 요청을 올바르게 완료했는지는 증명하지
않는다.

**개선:** 결과를 `correct_completion`, `safe_termination`, `needs_review`,
`failed`로 분리한다. 실제 `ChangeProposalService`를 임시 workspace에 연결한
통합 시나리오, versioned task corpus, 로컬 모델별 성공률/지연/repair율
matrix를 추가한다. 100% 게이트는 적어도 destructive safety와 성공 주장
정확성을 분리해 보고해야 한다.

### P2 — 모듈 응집도와 초기 번들 예산

`workspace_os.py`(1,402줄), `app_factory.py`(1,347줄), setup wizard(1,264줄),
model runtime(1,232줄), memory service(1,127줄), Telegram bot(1,065줄)는
테스트가 있어도 변경 충돌과 회귀 표면을 키운다. frontend의 초기 번들 경고도
저사양 장비나 첫 실행에서 체감될 수 있다.

**개선:** 동작 변경 없이 `app_factory` 조립 slice, `workspace_os` persistence
facet, model backend adapter, wizard transport adapter를 먼저 분리한다. 동시에
graph/Cytoscape, chart, command palette 등 무거운 UI 경로를 측정 후 lazy-load
하고 gzip budget을 CI에 둔다. 호환 shim은 신규 추가 금지와 제거 기한을
정해 줄인다.

### P2 — 배포 신뢰와 선택 기능의 실제 환경 검증

CI는 lint/type/test/eval/visual까지 충실하지만 workflow에 dependency
취약점/SBOM 검사가 보이지 않고 action은 immutable SHA가 아닌 tag를 사용한다.
release workflow도 자체적으로 agent/brain/integration gate를 실행하지 않아
`main` branch protection 설정에 의존한다. PostgreSQL migration integration은
라이브 서비스 없이 skip된다.

**개선:** 보호 브랜치 required checks를 문서화·감사하고 release에 필요한
게이트를 명시적으로 확인한다. dependency audit/SBOM 및 action pin 정책을
추가한다. 컨테이너 PostgreSQL/pgvector 경로를 scheduled 또는 release-candidate
CI에서 돌려 optional scale mode의 migration을 실제로 검증한다.

## 하네스와 루프 엔지니어링 평가

| 항목 | 현재 잘한 점 | 다음 단계 |
| --- | --- | --- |
| 상태 기계 | `REASONING → ACTING → VERIFYING`와 step budget, 즉시 동일 create loop guard가 있다. | 반복 형태가 교대하거나 의미상 같은 경우를 탐지하고, 모든 비성공 종료 코드를 명시한다. |
| 관측성 | trace가 repair/correction/tool result를 구조화해 API로 돌아온다. | trace에 plan evidence, proposal revision, verifier availability를 추가한다. |
| 약한 모델 회복 | deterministic parser repair와 scripted gauntlet이 있다. | repair가 성공하지 않은 verifier를 성공으로 바꾸지 않는다. |
| 도구 안전성 | destructive block과 approval flow를 scenario로 시험한다. | 실제 proposal service·파일 경쟁·모든 mutator coverage를 통합 시험한다. |
| 평가 신뢰도 | 결과가 빠르고 재현 가능해 CI gate로 적합하다. | synthetic score와 실사용 task/model matrix를 구분해 release 판단에 함께 쓴다. |

핵심 원칙은 **“루프가 끝났다”와 “사용자 요청을 검증해 완료했다”를 분리**하는
것이다. 전자는 시스템 안정성, 후자는 제품 신뢰성 지표다. 둘 모두가 좋아야
autonomous workflow의 완료 배지가 설득력을 갖는다.

## 사용자 관점 평가

### 잘 작동하는 경험

- 첫 실행부터 capture → Brain → proof/graph → review라는 흐름이 보이며,
  “Brain”이라는 용어가 기술 용어보다 목적에 가깝다.
- 일반 사용자와 admin 작업을 분리하고, 키보드 포커스·skip link·reduced
  motion·한/영 UI를 챙겼다.
- visual suite는 첫 실행, compact/mobile overflow, 명시적 capture,
  core unavailable, review center 등 사용자가 실제로 만나는 상태를 다룬다.
- 지식이 없을 때 모델이 답을 꾸며내지 않는 제품 원칙은 신뢰를 만든다.

### 사용자가 막히거나 오해할 지점

| 여정 | 위험 | 바꿀 경험 |
| --- | --- | --- |
| 장비 분석/모델 설치 | probe 실패도 추천 카드가 “준비됨”처럼 보일 수 있다. | 알 수 없음과 지원됨을 엄격히 구분하고 재시도/모델 없이 시작을 1차 선택으로 둔다. |
| 에이전트 완료 | critic 장애 후에도 완료 메시지가 나올 수 있다. | “검증 불가 — 검토 필요”를 완료와 확실히 다른 색·문구·행동으로 보인다. |
| 변경 승인 | 오래된 diff가 현재 파일과 다를 수 있다. | 승인 전에 “파일이 바뀜, 새 diff 보기”를 띄우고 원본을 보존한다. |
| Brain 화면 | 넓은 화면에서도 작은 graph/상태 라벨이 composer와 주의를 경쟁한다. | 5초 과업 테스트와 contrast audit로 primary action과 보조 evidence의 위계를 재조정한다. |
| 느린 첫 실행 | 초기 bundle과 설치 화면의 반복 정보가 대기 체감을 키운다. | 분석 진행 이유·예상 다음 행동을 한 문장으로 보여 주고 무거운 화면은 지연 로드한다. |

## 권장 실행 순서

1. **즉시(P0):** proposal revision/atomic apply와 verifier fail-closed를 구현하고,
   경쟁·garbage critic·timeout 테스트를 CI gate에 넣는다.
2. **다음 릴리스(P1):** mutating-tool inventory와 proposal coverage를 완성하고,
   설치 분석 실패 UX를 정직한 `unavailable` 상태로 교체한다. 운영/기능/개발
   문서를 현행 또는 historical로 분류한다.
3. **성숙화(P2):** 실제 service 기반 agent integration, model/corpus benchmark,
   PostgreSQL scheduled test, supply-chain gate, 큰 런타임·bundle 분리를 진행한다.

## 완료를 판단할 재검증 게이트

- proposal 원본 해시 충돌, concurrent approval, delete/recreate를 포함한 unit 및
  temp-workspace integration 테스트
- critic invalid JSON/timeout/빈 evidence에서 `DONE`이 절대 나오지 않는 agent eval
- registry의 모든 mutation 도구가 governance policy에 매핑되었음을 확인하는 CI 검사
- onboarding backend failure matrix의 frontend/visual 테스트
- 전체 문서 링크 검사 및 current/historical 문서 상태 검사
- `npm run lint`, `npm run typecheck`, Python/frontend/unit/integration/visual,
  agent/brain/product eval, package/frontend build

## 남은 위험과 후속 판단

이 리뷰는 현 코드의 구조와 자동화 결과에 근거한다. 로컬 모델의 실제 품질은
모델·하드웨어·언어·사용자 데이터에 따라 달라지므로, synthetic eval의 높은
점수를 범용 품질 보증으로 해석하면 안 된다. 또한 no-open-issue/PR 상태는
부정적 신호라기보다, 이 보고서를 P0/P1/P2 milestone 이슈로 분해하고 각
수락 기준을 추적할 기회다.

가장 먼저 할 리팩터링은 UI나 새 기능이 아니라 **proposal의 낙관적 동시성
보호와 verifier의 fail-closed 종료 의미**다. 이것이 해결되면 기존의 강한
관측성·하네스·제품 흐름이 실제 사용자 신뢰로 연결된다.
