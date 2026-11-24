[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../../rust/lattice-agent/ARCHITECTURE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./lattice-agent-ARCHITECTURE.md)

# `lattice-agent` — 도메인 맵

여섯 개의 그룹과 단 하나의 원칙: **커널(kernel)이 결정하고, 표면(surface)이 전달합니다.** 모든 모듈은 자신의 목적을 나타내는 그룹 안에 위치하며, 각 그룹의 `mod.rs`는 그 그룹에 무엇이 속하고 무엇이 절대 들어오면 안 되는지, 그리고 어떤 불변성을 지켜야 하는지를 명시합니다. 먼저 각 `mod.rs`를 읽으십시오. 이 문서는 색인이며, 실제 계약은 각 `mod.rs`입니다.

```
surface/   HTTP 입력, HTTP 출력 — 그리고 Python 연산 워커로 나가는 클라이언트
   │       router · looproutes · runbody · worker
   │  호출만 수행하며 결정하지 않음
kernel/    에이전트 루프 및 거부(refuse)할 수 있는 모든 의사결정
   │       agentloop/ (mod · execution/ · guided/ · planning · verification ·
   │                   fallback · gates · recovery · harness)
   │       policy · mode · permission · breaker · governor · proposals
   │       state · transcript · trace · plan · profile · probe · runs
   ├── parse/    신뢰할 수 없는 모델 텍스트를 입력받아 타입화된 값으로 변환
   │             action · channel · inference · pyjson · pyliteral · pyshlex · pystr
   ├── content/  실행이 기록하려는 바이트 데이터 정제 및 검증
   │             sanitize/ (extract · validate · repair · salvage · python · text) · pydiff
   └── tools/    도구의 동작과 도구가 작동할 수 있는 작업 영역
                 catalog · host · sandbox · command · exec · authorize · documents
                 args · files · shell · vault · desktop · render · scaffold · local

prompts/   호출자가 프롬프트를 제공하지 않았을 때 모델에 전달되는 기본 프롬프트
           mod (세 가지 역할) · guided (분해된 마이크로 턴)
```

화살표는 오직 아래쪽으로만 향합니다. `kernel`은 절대 `surface`를 임포트하지 않으며, `parse`, `content`, `tools`는 절대 `kernel`을 임포트하지 않습니다. 변경 작업 중 위쪽을 향하는 화살표가 필요해졌다면 계층 구조가 아니라 인자 목록이 잘못된 것입니다.

## 커널(Kernel) vs 표면(Surface)

커널은 HTTP 서버가 붙어 있지 않아도 그 자체로 가치를 가지는 핵심입니다. 즉 상태 머신, 게이트, 실행 자체의 기록입니다. 반면 표면은 양 끝단의 전송 계층입니다. 호출자가 들어오는 axum 라우트와 이 프로세스가 Python 연산 워커로 나가는 `worker` 클라이언트가 여기에 속합니다. 핸들러가 직접 "이 작업이 허용되는가"를 판단하기 시작하면 `kernel::permission`을 포크한 셈이 되며, 커널 모듈이 호출자의 요청 표현 방식을 알게 된다면 잘못된 인자를 전달받은 것입니다.

## 소형 모델 하니스

제품은 사용자의 머신에 올라갈 수 있는 어떤 모델에서든 작동해야 합니다. 0.5B 모델은 단순히 크기가 작은 70B 모델이 아니라, 도구 호출(tool-call) 계약을 전혀 유지할 수 없는 모델입니다. 이를 해결하는 세 가지 구성 요소가 있으며, 그중 어느 것도 특정 모델 이름을 하드코딩하지 않습니다.

**세 개의 다이얼, 그리고 JSON을 요구하지 않는 하나의 다이얼.** `kernel/profile.rs`는 `standard`와 `compact` 옆에 `guided`를 추가합니다. `guided` 모드에서 `kernel/agentloop/guided.rs`는 한 단계를 여러 마이크로 턴(micro-turns)으로 분해합니다. 먼저 실제 번호가 매겨진 카탈로그를 두고 *"실행할 행동 번호 하나를 고르시오"*라고 묻고, 이어서 필수 인자마다 한 턴씩 묻습니다(`path`는 한 줄로, `content`는 JSON 이스케이프가 전혀 필요 없는 자유 형식 텍스트로 받습니다). **하니스가 직접 액션 구조체를 조립**한 뒤 모든 모드가 공통으로 실행하는 마지막 단계인 `Runtime::perform_action`에 넘깁니다. 따라서 게이트 체인, 루프 가드, 쓰기 전 스냅샷, `sanitize_write_content`는 완벽히 동일하게 적용됩니다. 검증(verification) 역시 동일한 방식으로 처리됩니다. 닫힌 `PASS`/`FAIL` 질문과 한 줄짜리 이유를 받아 JSON 판정이 통과해야 하는 것과 똑같은 증거 및 커버리지 게이트로 평가합니다. 여기서 생략되는 것은 JSON 포맷뿐이며 거버넌스를 우회하는 지름길은 전혀 없습니다.

**경계(seam)에서의 구조적 생성 보장.** `Completion::prefix`는 모델의 응답이 반드시 지정된 문자열로 *시작*하도록 강제합니다. `compact` 모드는 `{"thoughts": "`를 전송하므로 서두 인사말, 마크다운 코드 펜스, `<|channel|>` 프레임은 복구 체인이 사후에 지워야 할 대상이 아니라 모델이 애초에 출력할 수 없는 형태가 됩니다. 워커는 생성 마커 직후 프롬프트에 이 접두사를 프리필(prefill)하고 반환 텍스트가 해당 문자열로 시작함을 보장합니다. 토큰 단위의 JSON 문법 제약(grammar constraint)은 검토되었으나 **도입하지 않았습니다**. 토크나이저를 인식하는 증분 파서와 단일 MLX 실행기 위에서의 토큰당 Python 콜백이 필요한데, `guided` 모드가 JSON을 아예 요구하지 않음으로써 토큰당 비용 0으로 동일한 보장을 제공하기 때문입니다.

**추측이 아닌 실측.** `kernel/probe.rs`는 모델에 고정된 두 가지 질문을 단 한 번 던집니다. 첫째는 약한 모델이 JSON 문자열 안에서 가장 자주 깨뜨리는 두 문자인 줄바꿈과 따옴표가 포함된 액션 객체를 출력하게 하는 것이고, 둘째는 3줄짜리 메뉴를 읽게 하는 것입니다. 첫 번째 응답을 루프 *자체의* 파서로 채점하여 다이얼을 선택합니다. 깨끗하게 파싱되면 `standard`, 복구를 거쳐 파싱되면 `compact`, 파싱이 불가능하면 `guided`가 됩니다. 판정 결과는 데이터 디렉터리 아래에 모델 ID **및 크레이트 버전**별로 캐시됩니다. `LATTICEAI_AGENT_PROFILE` 환경 변수는 여전히 모든 것보다 우선하여 원하는 다이얼을 고정할 수 있으며, `LATTICEAI_AGENT_PROBE=0`은 측정을 비활성화합니다. 모델 크기 정규식은 측정이 불가능할 때의 사전 확률(prior)로만 남아 있습니다. 프로빙은 포트(`LoopDeps::probe`)로 분리되어 있어 `LoopDeps::new`에서는 꺼져 있고 실제 서버 연결 시에만 켜지므로 테스트 하니스나 고정된 골든 궤적이 암묵적으로 두 번의 완성과 `$HOME` 아래의 쓰기 비용을 치르지 않습니다.

**하향 전용 자기 교정(Self-correction, downward only).** 프로브는 장난감 질문을 한 번 던질 뿐이지만 실제 작업은 훨씬 어렵습니다. `standard`나 `compact`로 측정된 실행이 포맷 예산을 전부 소진하면서도 예시를 그대로 복사하거나 파싱 가능한 출력을 전혀 내지 못한다면 **스스로 `guided`로 강등(demote)하여 작업을 마무리합니다**(`execution::Runtime::demote_to_guided`). 세 가지 조건이 이를 보호합니다. 다이얼이 *실측된* 것이어야 하고(호출자가 명시적으로 주입한 다이얼은 유지), 이미 `guided`가 아니어야 하며, 아직 실행 증거가 없어야 합니다. 상향 승격은 절대 일어나지 않습니다.

**모델이 조향을 멈추면 계획(plan)이 조향합니다.** 탈출 해치(`kernel/agentloop/fallback.rs`)는 두 부분으로 나뉘며 순서대로 실행됩니다. 먼저 `direct_plan_path`가 계획에 포함된 **비-쓰기(non-write)** 단계들과 *요청 자체가 명시적으로 지목한* `skill.` 항목을(최대 한 번) 모든 다이얼이 공유하는 `Runtime::perform_action`을 통해 디스패치합니다. 그다음 `direct_file_path`가 앞선 실행 결과를 컨텍스트로 삼아 계획된 파일들을 기록합니다. 여기서 지어내는 것은 아무것도 없습니다. 모든 이름과 인자는 해당 실행이 이미 생성한 계획에서 나오며, 카탈로그에 없는 이름은 건너뛰고, 이미 성공한 단계는 다시 실행하지 않습니다. 파일 쓰기 절반만으로는 *"README를 읽고 요약하라"*는 계획에서 README를 읽지 못한 채 쓰기 단계로 넘어가 모델이 요약문을 지어내거나, 단일 `list_dir` 또는 `mcp.grep` 계획이 아무 결과도 내지 못하는 문제가 있었기 때문에 이 구조가 도입되었습니다. 또한 동일한 액션, 동일한 인자, 동일한 도구 오류로 두 번 연속 실패하는 실행은 이미 거부당한 작업에 전체 스텝 예산을 낭비하는 대신 즉시 이 탈출 해치를 탑니다(모든 다이얼에 `guided::REPEAT_FAILURE_LIMIT` 적용).

**우리 자신의 프롬프트 텍스트 거부.** 약한 모델은 가장 가까운 완성된 형태를 이어 쓰려는 경향이 있으며 에이전트 턴에서 그것은 바로 프롬프트입니다. 루프는 자신이 준 문장을 그대로 실행하는 것을 거부합니다. `content`가 `prompts::WRITE_EXAMPLE_CONTENT`와 글자 그대로 일치하는 `write_file`은 `COPIED_EXAMPLE`로 기록되고 어디에도 쓰이지 않으며, 우리의 지시문 줄로 시작하는 `guided` 응답은 해당 줄이 제거되고(`guided::answers::strip_echoed_lines`), 평가자(critic)의 플레이스홀더 사유는 노출되는 대신 비워집니다. 이 모든 검사는 이 크레이트가 직접 소유한 상수와만 비교하므로 실제 정상 답변을 잘못 거부할 일은 없습니다.

**세 가지 종류를 아우르는 단일 카탈로그.** `tools/catalog.rs`는 단일 어휘 사전입니다. 실행 정책 테이블에서 온 네이티브 도구, 호스트 MCP 표면에서 온 `mcp.<tool>`, 스킬 레지스트리에서 온 `skill.<name>`이 하나의 번호 매겨진 메뉴, 하나의 인자 시그니처 집합, 하나의 디스패치 결정으로 통합됩니다. `resolve`는 접두사가 붙어 있더라도 기본 이름을 실행 정책이 관리하고 있다면 두 거버넌스 체인 중 더 엄격한 **네이티브** 경로로 라우팅합니다. 실행 정책에 없는 이름만 호스트의 `ToolCatalog` 포트로 전달됩니다. 스킬은 실행 파일이 아닌 가이드로 명시됩니다. 스킬을 선택하면 해당 `SKILL.md`를 반환하여 루프가 모델 앞에 유지하고, 실행은 여전히 실제 도구를 선택해야 합니다. `lattice-host`의 `gateway/agent_catalog.rs`가 `POST /mcp`가 스캔하는 바로 그 디렉터리를 읽는 프로덕션 구현체입니다.

**요청에서 이름으로 지목된 스킬은 가장 먼저 참조됩니다.** 사용자의 문장이 설치된 스킬을 카탈로그 이름으로 직접 언급하면(예: `using the code_review skill, …`), 하니스는 어떤 다이얼에서든 실행의 첫 번째 단계로 해당 스킬 참조를 결정론적으로 수행한 뒤 즉시 모델에 제어권을 돌려줍니다(`execution::Runtime::consult_named_skill`). `SKILL.md` 내용은 실행자 프롬프트와 `guided` 브리프가 읽는 트랜스크립트에 기록되며 그 외에는 아무것도 바뀌지 않습니다. 모델을 대신해 도구를 고르지도, 파일을 쓰지도 않으며 모델이 무엇을 할지 결정하고 모든 바이트를 직접 작성합니다. **이는 하니스가 대신 선택하는 것이 아니라 사용자의 명시적 지시를 이행하는 것입니다.** 설치된 스킬을 호명하는 것은 슬래시 명령어와 마찬가지로 그 스킬을 읽으라는 지시이며, 이를 무시하는 실행은 판단력을 발휘한 것이 아니라 사용자를 무시한 것입니다. 이 동작은 요청이 실제 제공되는 스킬을 지목했을 때, 아무것도 실행되기 전에, 실행당 단 한 번만 발동합니다.

**MCP 디스패치:** `lattice_platform::mcp::dispatch_for_agent`가 공개(public)되었으며 `POST /mcp`가 실행하는 것과 동일한 `check_governance`를 수행합니다. `resolve`는 실행이 이미 관리하는 접두사 이름을 커널의 더 엄격한 체인으로 보내고, 실행이 관리하지 *않는* 이름은 `PlatformCatalog::execute`로 전달되어 해당 심볼을 호출합니다. 거버넌스 거부, 알 수 없는 도구, 또는 누락된 도구 표면은 모두 도구 오류로 처리되며 절대 우회되지 않습니다.

## 어디에 추가해야 하나요?

**…새로운 도구(tool)?** `tools/` 아래에서 해당 도구가 *다루는 대상*의 이름을 딴 파일에 추가합니다. 워크스페이스 파일은 `files`, 브레인/Obsidian 볼트는 `vault`, 서브프로세스는 `shell`, OS 제어는 `desktop`, 문서 생성기는 `render`, 프로젝트 템플릿은 `scaffold`, 워크스페이스 외부 경로는 `local`입니다. 그런 다음 `tools::host::MUTATING_TOOLS`(또는 `RENDER_TOOLS`)에 이름을 추가하고 `tools/host.rs`의 디스패처에 분기(arm)를 추가합니다. `is_native`는 해당 테이블들로부터 자동 도출됩니다. 모든 경로는 `tools::sandbox::Workspace::resolve`를 통해 확인되며 모든 쓰기는 `content::sanitize::sanitize_write_content`를 거칩니다. 여기에 별도의 권한 검사를 추가하지 **마십시오** — 커널이 이미 승인한 상태입니다.

**…새로운 파싱 단계(parse rung)?** `parse/action.rs`(또는 새로운 모델 패밀리 프레임인 경우 `parse/channel.rs`)에 추가합니다. 비용이 가장 적고 가장 문자 그대로인 단계부터 순서대로 연결된 체인 끝에 추가하고, **반환되는 `repairs` 리스트에 반드시 해당 복구 이름을 기록하십시오.** 조용히 복구만 수행하는 단계는 버그입니다. 그 리스트가 보여주고자 하는 약한 모델의 회귀(regression)를 숨기기 때문입니다. 어떤 파싱 단계도 모델이 직접 쓰지 않은 도구 이름, 경로, 인자를 임의로 채워 넣어서는 안 됩니다.

**…새로운 프롬프트?** `prompts/mod.rs`에 상수로 추가하고 실제 파서를 통과시키는 테스트를 함께 작성합니다. 호출자가 제공한 프롬프트가 항상 우선하며 내장 프롬프트는 빈칸만 채웁니다. 도구 이름은 항상 실행 정책 테이블에서 가져와야 하며 별도의 두 번째 목록을 만들지 마십시오.

**…새로운 게이트 또는 판정(verdict)?** `kernel/`에 추가하며 반드시 **실패 시 닫히는(fail-closed)** 방식이어야 합니다. 알 수 없는 경우는 거부(refusal)이고, 도달할 수 없는 검증기는 `NEEDS_REVIEW`이며, 정책이 없는 도구는 가장 위험한 등급으로 분류됩니다. 순서 자체가 계약입니다. `kernel/agentloop/gates.rs`의 게이트 체인과 `kernel::permission::block_reason_for_tool`의 우선순위 체인은 `rust/fixtures/agent/` 아래의 고정된 골든 픽스처로 고정되어 있으므로 순서를 바꾸는 것은 곧 동작 변경입니다.

**…새로운 라우트?** `surface/`에 추가하며 요청 본문은 `runbody.rs`에 두고 400 응답은 `surface::bad_request`를 통해 반환합니다. 라우트는 요청을 변환하고, 커널을 호출하고, 결과를 다시 변환할 뿐입니다.

## 공개 API 표면 (14.1.0)

`src/lib.rs`는 정본 도메인 그룹(`content`, `kernel`, `parse`, `prompts`, `seam`, `surface`, `tools`)과 `VERSION`만 공개합니다. 모든 내부·외부 호출자는 정본 경로(`lattice_agent::kernel::state`, `lattice_agent::tools::sandbox`, `lattice_agent::surface::looproutes` 등)에서 직접 임포트합니다.
