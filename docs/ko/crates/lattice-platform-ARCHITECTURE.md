[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../../rust/lattice-platform/ARCHITECTURE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./lattice-platform-ARCHITECTURE.md)

# `lattice-platform` — 도메인 맵

일곱 개의 도메인과 단 하나의 원칙: **이 크레이트는 제품의 표면(surface)입니다.** 이 크레이트는 기능을 제공하지만, 그 기능이 허용되는지 스스로 결정하지 않으며 무엇이 진실(truth)인지 직접 소유하지도 않습니다. 모든 모듈은 자신의 *목적*을 나타내는 도메인 안에 위치하며, 각 도메인의 `mod.rs`는 무엇이 그 안에 속하고 무엇이 절대 들어오면 안 되는지, 그리고 어떤 불변성을 유지해야 하는지를 명시합니다. 먼저 각 `mod.rs`를 읽으십시오. 이 문서는 색인이며, 실제 계약은 각 `mod.rs`입니다.

```
shell/         브라우저의 첫 접점 — SPA 셸과 모든 308 리다이렉트
               static_ui · ui_redirects

workspaceos/   사용자가 일하는 공간과 그 공간에 참여할 수 있는 주체
               workspace/ · invitations · permissions · permission_mode
               features · project_sessions · realtime

governance/    제품이 자체적으로 수행하는 작업과 그 앞단의 승인 게이트
               review_queue/ · change_proposals · automation/
               workflow_designer/ · hooks/

toolsurface/   제품이 도달할 수 있는 모든 기능과 카탈로그
               mcp/ · tools/ · plugins · marketplace · agents/
               agent_registry · computer_use

knowledge/     그래프 콘텐츠가 신뢰 경계를 넘어 이동하는 방식
               portability/ · network · network_boundary · voice

modelops/      이 머신에서 실행할 모델 선택과 실행 환경 준비
               models_catalog/ · setup/

adminops/      설치 환경 운영 및 감사
               admin/ · security_dashboard/ · funnel_metrics
```

화살표는 이 크레이트 바깥의 **아래쪽**으로만 향합니다. 즉 `lattice-auth`(누구인가), `lattice-agent`(실행해도 되는가, 무엇을 하는가), `lattice-core`(무엇이 진실인가), 그리고 `WorkerSeamClient` 너머의 Python 워커(연산)를 향합니다. 어떤 도메인도 형제 도메인의 내부 구현을 옆으로 침범하지 않습니다. 도메인 경계를 넘는 네 가지 결합은 각각 해당 `mod.rs`에 명시되어 있습니다. `governance`는 `workspaceos`의 단일 스토어를 통해 기록하고, `toolsurface`는 `mcp`의 HTTP 어휘를 사용하며, `modelops`의 `setup`은 자체 카탈로그의 호스트 프로브를 읽고, `adminops`의 두 리더는 `admin`의 단일 감사 작성자(audit writer)를 공유합니다.

## 제품 표면(Product surface) vs 커널(Kernel) vs 진실(Truth)

| 크레이트 | 핵심 질문 | 예시 |
|---|---|---|
| **`lattice-platform`** | 제품이 무엇을 **제공(offer)**하는가? | `POST /api/knowledge-graph/export`, Review Center, `/setup/scan` |
| `lattice-agent` (kernel) | 이를 실행해도 **되는가(may)**, 그리고 무엇을 **수행(do)**하는가? | `block_reason_for_tool`, 루프 게이트, 네이티브 `write_file` |
| `lattice-core` | 무엇이 **진실(true)**인가? | 그래프, 단일 그래프 작성자(`GraphWriter`), 상태 파일들 |

이 크레이트의 핸들러가 "이 작업이 허용되는가"를 자체적으로 다시 도출한다면 `lattice_agent::kernel::permission`을 포크한 것입니다. 이 크레이트의 모듈이 그래프를 직접 열어 수정한다면 두 번째 작성자가 된 것입니다. 두 경우 모두 이 경계가 방지하고자 하는 실패 모드입니다. 추론, 문서 파서/생성기 매트릭스, ASR과 같은 연산(compute)은 Python 워커에 남습니다.

## 어디에 추가해야 하나요?

**…새로운 라우트 패밀리?** 해당 주제를 이름으로 갖는 도메인 안에 새 모듈을 만들고, `(method, path)` 쌍을 나열하는 `MOUNTED: &[(&str, &str)]`와 `router(state) -> Router` 팩토리를 정의한 뒤 `lattice-host`의 `gateway/product.rs`에 마운트합니다. 한 모듈이 정확히 하나의 라우트 패밀리를 소유하므로 두 작업 패키지가 같은 파일을 동시에 편집할 일이 없습니다. `lattice-host`는 라우터를 빌드하기 *전에* 모든 `MOUNTED`의 합집합에 중복이 없음을 검증하므로, 이중 마운트는 생성자 패닉이 아닌 명확한 어설션으로 잡힙니다. 만약 라우트가 **페이지 셸**이라면 `shell/ui_redirects`의 308 리다이렉트로 추가합니다(단, 자체 `require_user`를 가지는 `GET /plugins/sdk`는 예외적으로 `toolsurface::plugins`에 속합니다).

**…새로운 도구?** `toolsurface/tools/` 아래에서 해당 도구가 *다루는 대상*의 이름을 딴 파일(`fs`, `shell`, `knowledge`, `downloads`, `meta`)에 추가하고, 외부의 `lattice-host`가 읽는 `tools::governance_for`에 등급을 등록합니다. 파일시스템 쓰기는 `lattice_agent::sandbox::Workspace`를 거치므로 에이전트의 `write_file`, `/tools/write_file`, 승인된 변경 제안이 모두 단일 경로 정책을 공유합니다. *에이전트 턴 내부에서* 실행되는 도구는 이곳에 추가하지 않습니다 — 그 핸들러는 `lattice_agent::tools`에 있으며 이 크레이트는 이를 HTTP로 노출할 뿐입니다.

**…워크플로 노드, 훅, 또는 검토 항목 유형?** `governance/`에 추가합니다. 변경을 스테이징하는 모든 항목은 기반 SHA(base SHA)를 함께 기록합니다. 승인 시점에 기반 SHA가 더 이상 일치하지 않으면 병합이나 조용한 덮어쓰기가 아니라 **409**를 반환해야 합니다.

**…상태(state)?** 이미 해당 파일을 소유한 패밀리를 통해 접근합니다. `workspace_os.json`의 작성자는 오직 하나(`workspaceos::workspace::store::WorkspaceOsStore`)뿐이며, `governance::review_queue::GovernanceState`는 두 번째 복사본이 아니라 *동일한* `Arc` 위의 파사드(facade)로서 단일 문서에 대한 마지막 작성자 승리(last-writer-wins) 경쟁을 방지합니다.

**…감사 로그(audit line)?** `adminops::admin::append_audit_event`를 사용합니다. 단일 추가 함수, 단일 파일, 단일 포맷을 유지하며 `lattice-chat`과 `lattice-host` 역시 자체 작성자를 늘리지 않고 외부에서 이 함수를 호출합니다.

## 공개 API 표면 (14.1.1)

`src/lib.rs`는 7개의 정본 도메인 모듈(`adminops`, `governance`, `knowledge`, `modelops`, `shell`, `toolsurface`, `workspaceos`)과 `VERSION`만 공개합니다. `lattice-host`, `lattice-chat` 및 통합 테스트의 모든 호출자는 정본 도메인 경로(`lattice_platform::adminops::admin`, `lattice_platform::workspaceos::workspace` 등)에서 직접 임포트합니다.

공유 HTTP JSON 응답, FastAPI 422 검증 엔벌로프, 지역화된 메시지 상세 정보, UTC 타임스탬프 헬퍼는 크레이트 내부 모듈인 `crate::http_reply`(`src/http_reply.rs`)에 위치하여 `knowledge/`와 `toolsurface/`가 전송 포맷팅을 위해 `workspaceos::project_sessions`를 옆으로 참조하지 않도록 합니다.
