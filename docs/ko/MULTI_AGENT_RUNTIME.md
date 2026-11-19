[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../MULTI_AGENT_RUNTIME.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./MULTI_AGENT_RUNTIME.md)

# Lattice AI 멀티 에이전트 런타임 2.1 (Multi-Agent Runtime)

Multi-Agent Runtime은 v2.0.0에서 도입되고 v2.2.0에서 운영 수준으로 고도화된 **오케스트레이션 계층**입니다. 이 계층은 단일 에이전트 상태 머신(11.6.0부터 Rust 루프 오케스트레이터인 [`lattice_agent::agentloop`](../../rust/lattice-agent/src/kernel/agentloop/mod.rs)) *위에* 위치하며, 서로 작업을 인계(handoff)하고 검토 실패 시 재시도하며 완전한 관측 및 리플레이가 가능한 타임라인을 방출하는 명명된 **역할(roles)** 파이프라인을 조율합니다.

- **진실 공급원(Source of truth):** `rust/lattice-agent/src/kernel/agentloop/`(상태 머신) 위의 `rust/lattice-platform/src/toolsurface/agents/mod.rs`(오케스트레이션 라우트)
- **HTTP 표면:** `lattice-platform`이 마운트하는 `agents` 라우트 패밀리
- **영속성(Persistence):** `rust/lattice-platform/src/governance/review_queue.rs` — Review Center가 읽는 것과 동일한 `workspace_os.json` 문서

> **11.6.0 이전 안내.** 이 문서가 과거에 언급했던 Python 모듈들(`lattice_brain/runtime/multi_agent.py`, `latticeai/api/agents.py`, `latticeai/core/workspace_os.py`)은 제품 서버가 Rust로 전환될 때 삭제되었으며, 아래에 설명된 모든 동작은 이제 Rust에서 네이티브로 제공됩니다.

```python
MULTI_AGENT_VERSION = "2.2.0"
```

## v2.2에서 추가된 기능

v2.2는 v2.0 런타임을 대체하지 않고 런타임의 운영 객체를 영속적이고 검사 가능하게 확장합니다.

- **명시적 인계 레코드(Handoff records)**: `handoff_id`, 출발/대상 에이전트 ID, 사유, 작업 요약, 컨텍스트 패킷, 상태, 타임스탬프.
- **에이전트 컨텍스트 패킷(Agent context packets)**: 목표, 작업 요약, 워크스페이스/그래프/메모리/워크플로 컨텍스트, 플러그인 출력, 제약 조건, 검토자 노트, 재시도 메타데이터(명백한 시크릿 키는 자동 마스킹됨).
- **검토 / 재시도 이력**: 검토자 판정 결과를 `approve`, `reject`, `retry`로 정규화하고 재시도 사유, 노트, 횟수, 한도를 영속화합니다.
- **계획 레코드(Planning records)**: 계획에 `plan_id`, 순서가 지정된 실행 가능 단계, 계획 검토 메타데이터를 포함합니다.
- **리플레이(Replay)**: 저장된 실행을 `/agents/api/runs/{run_id}/replay`를 통해 수행 주체(actor), 시간, 사유, 입력, 출력, 결정을 보여주는 프레임 단위로 리플레이할 수 있습니다.

## v1 단일 에이전트 런타임과의 관계

v1.x는 주입된 `AgentDeps` 포트 위에서 `PLAN → EXECUTE → VERIFY → DONE`(`ROLLBACK` / `FAILED` 복구 경로 포함)을 구동하는 단일 에이전트 상태 머신(`SingleAgentRuntime`)을 제공했습니다. 기존 `AgentRuntime` 임포트는 호환성 별칭으로 유지됩니다.

v2.0은 그 위에 *오케스트레이션* 계층을 추가합니다. 단일 에이전트가 내부 단계를 반복하는 대신, `MultiAgentOrchestrator`가 서로 작업을 인계하고 검토 실패 시 되감기(rewind)할 수 있는 **구별된 역할들의 파이프라인**(`researcher`, `planner`, `executor`, `reviewer`, `release`)을 구동합니다. 두 계층은 상호 보완적입니다. v2 오케스트레이터가 역할들을 조율하고, 개별 역할의 실행기(runner)는 주입된 러너 포트 뒤에서 v1 `SingleAgentRuntime` 루프나 LLM으로 구동될 수 있습니다.

> **호환성.** Multi-Agent Runtime은 순수하게 추가적(additive)입니다. v1 `SingleAgentRuntime` 상태 머신과 `/agent` 엔드포인트는 그대로 유지되며, v2 API는 복수형인 `/agents` 네임스페이스를 사용하므로 기존 단일 에이전트 `/agent` 라우트와 절대 충돌하지 않습니다. 기존 v1.x 데이터는 보존되며 새로운 실행은 워크스페이스 상태와 Knowledge Graph에 나란히 추가됩니다.

## 내장 역할 (`AGENT_ROLES`)

런타임은 다섯 가지 내장 역할을 정의합니다. 각 역할 ID는 워크스페이스의 기본 에이전트 항목(`DEFAULT_AGENTS`)과 일치하므로 오케스트레이션 실행은 워크스페이스에 이미 표시되는 동일한 에이전트들을 참조합니다.

```python
AGENT_ROLES = ("researcher", "planner", "executor", "reviewer", "release")

ROLE_AGENT_IDS = {
    "researcher": "agent:researcher",
    "planner":    "agent:planner",
    "executor":   "agent:executor",
    "reviewer":   "agent:reviewer",
    "release":    "agent:release",
}
```

| 역할 | 에이전트 ID | 책임 |
| --- | --- | --- |
| `researcher` | `agent:researcher` | 주입된 `context_provider`를 통해 관련 컨텍스트(워크스페이스 메모리 / 그래프)를 수집합니다. |
| `planner` | `agent:planner` | 목표를 순서가 있고 검사 가능한 단계들로 분해합니다. |
| `executor` | `agent:executor` | 각 단계를 수행하며 주입된 워크플로 / 플러그인 러너를 구동할 수 있습니다. |
| `reviewer` | `agent:reviewer` | 실행 결과를 평가하고 `pass` / `retry` 판정을 반환합니다. |
| `release` | `agent:release` | 최종 결과를 확정하고 패키징합니다(선택 사항). |

`agent:*` ID들은 `workspace_os`의 `DEFAULT_AGENTS`(`agent:planner`, `agent:executor`, `agent:reviewer`, `agent:researcher`, `agent:release`)와 일대일로 대응되므로 오케스트레이션 실행은 워크스페이스가 이미 알고 있는 에이전트들을 대상으로 관계를 기록할 수 있습니다.

### 핵심 파이프라인 (Core pipeline)

`researcher`와 `release`는 명시적으로 요청될 때만 실행되는 선택적 단계입니다. 따라서 빠르고 기본적인 실행은 다음 세 단계로 구성됩니다.

```python
CORE_PIPELINE = ("planner", "executor", "reviewer")
```

명시적인 `roles` 목록 없이 `MultiAgentOrchestrator.run(...)`을 호출하면 `CORE_PIPELINE`을 사용합니다. 전달된 역할 목록은 알려진 `AGENT_ROLES` 집합으로 필터링되며, 유효한 역할이 하나도 남지 않으면 `CORE_PIPELINE`으로 폴백합니다.

## 오케스트레이션: `MultiAgentOrchestrator.run`

```python
def run(
    self,
    goal: str,
    *,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    roles: Optional[List[str]] = None,
    max_retries: int = 2,
) -> AgentRunResult:
    ...
```

`run`은 확정된 파이프라인을 따라 이동하며 모든 단계에 단일 `OrchestrationContext`를 전달합니다. 진행 과정에서 관측 가능한 타임라인에 두 가지 종류의 이벤트를 기록합니다.

- **`role` 이벤트** — 각 단계에서 `_run_role`이 방출하며 역할, `agent_id`, 상태(`ok` / `error`), 원시 러너 결과, 시작/종료 타임스탬프를 기록합니다.
- **`handoff` 이벤트** — 제어권이 한 역할에서 다음 역할로 넘어갈 때(그리고 재시도 되감기 시) `OrchestrationContext.handoff(frm, to, note)`를 통해 방출됩니다.

타임라인의 처음과 끝에는 목표와 확정된 파이프라인을 담는 `start` 이벤트와 최종 상태 및 재시도 횟수를 담는 `end` 이벤트가 기록됩니다.

### 재시도: reviewer에서 executor로 되감기

파이프라인은 기본적으로 선형이지만 **검토자(reviewer)가 실행자(executor) 단계로 파이프라인을 되감을 수 있습니다.** 검토자 실행 후 판정이 `retry`이고 재시도 예산이 아직 남아 있다면 오케스트레이터는 다음을 수행합니다.

1. `ctx.retries`를 1 증가시킵니다.
2. `handoff("reviewer", "executor", note="retry #N: <reason>")` 이벤트를 방출합니다.
3. 파이프라인 인덱스를 다시 `executor` 단계로 되돌립니다.
4. `executor` → `reviewer`를 다시 실행합니다.

이 과정은 판정이 `pass`가 되거나 `ctx.retries`가 `max_retries`에 도달할 때까지 반복됩니다.

```text
planner → executor → reviewer ──pass──▶ (계속 진행 / 종료)
                        │
                        └──retry (그리고 retries < max_retries)──▶ executor (되감기)
```

### 최종 상태 (Final status)

최종 상태는 마지막 검토자 판정과 재시도 횟수로부터 결정됩니다.

| 조건 | `status` |
| --- | --- |
| 최종 판정 `pass`, 재시도 없음 | `ok` |
| 최종 판정 `pass`, 1회 이상 재시도 후 성공 | `retried_ok` |
| 최종 판정이 `pass`가 아님 (재시도 예산 소진) | `failed` |

### `OrchestrationContext`

모든 단계에 걸쳐 전달되는 가변 컨텍스트 객체입니다.

```python
@dataclass
class OrchestrationContext:
    goal: str
    user_email: Optional[str] = None
    workspace_id: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    research: List[str] = field(default_factory=list)
    executed: List[Dict[str, Any]] = field(default_factory=list)
    review: Dict[str, Any] = field(default_factory=dict)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    retries: int = 0
    output: str = ""

    def handoff(self, frm: str, to: str, note: str = "") -> None: ...
```

### `AgentRunResult`

`run`은 직렬화를 위한 `as_dict()`를 제공하는 `AgentRunResult`를 반환합니다.

```python
@dataclass
class AgentRunResult:
    agent_id: str
    status: str  # ok | failed | retried_ok
    output: str
    timeline: List[Dict[str, Any]]
    plan: List[Dict[str, Any]]
    review: Dict[str, Any]
    roles_run: List[str]
    retries: int = 0
```

## 주입 가능한 포트로서의 역할 러너 (Role runner)

v1 런타임과 마찬가지로 오케스트레이터는 **주입된 `role_runner` 포트 위에서 동작하는 순수 로직**입니다. LLM이나 서버 없이도 독립적으로 실행됩니다.

```python
class MultiAgentOrchestrator:
    def __init__(
        self,
        role_runner: Optional[Callable[[str, OrchestrationContext], Dict[str, Any]]] = None,
    ):
        self.role_runner = role_runner or default_role_runner()
```

러너는 `(role: str, ctx: OrchestrationContext) -> Dict[str, Any]` 시그니처를 가지는 단일 콜러블입니다. 파이프라인 순회, 인계, 재시도 되감기, 타임라인 방출, 상태 도출과 같은 오케스트레이션 로직은 각 역할이 *어떻게* 작업을 수행하는지에 의존하지 않습니다. 즉 **프로덕션 배포 환경에서 오케스트레이션 계층을 전혀 건드리지 않고도 LLM 기반 러너로 손쉽게 교체**할 수 있습니다.

러너에서 예외가 발생하면 `_run_role`은 실행 전체를 중단시키는 대신 해당 `role` 이벤트에 `status: "error"`와 `{"error": ...}` 결과로 안전하게 포착합니다.

## 결정론적이고 실용적인 기본 러너 (`default_role_runner`)

`default_role_runner`는 모든 내장 역할을 실제(비-LLM) 동작으로 구현하는 **무의존성 결정론적** 러너를 생성합니다. 이를 통해 커뮤니티 에디션에서도 별도의 모델 없이 에이전트 실행이 워크플로와 플러그인을 구동할 수 있습니다.

```python
def default_role_runner(
    *,
    workflow_runner: Optional[Callable[..., Any]] = None,
    plugin_runner: Optional[Callable[..., Any]] = None,
    context_provider: Optional[Callable[[str], List[str]]] = None,
) -> Callable[[str, OrchestrationContext], Dict[str, Any]]:
    ...
```

역할별 기본 동작:

- **`researcher`** — 주입된 `context_provider(goal)`(워크스페이스 메모리)를 호출하여 관련 컨텍스트를 `ctx.research`로 가져오고 개수와 첫 항목들을 반환합니다.
- **`planner`** — 목표를 순서가 있는 단계들로 분해합니다. `inputs["steps"]`가 비어 있지 않은 리스트라면 각 항목을 계획된 단계로 사용하고, 그렇지 않으면 기본 3단계 계획(`Analyze` / `Execute` / `Verify the result`)을 생성하여 `ctx.plan`에 기록합니다.
- **`executor`** — 계획을 순회하며 각 단계를 `done`으로 표시합니다. 단계가 워크플로 또는 플러그인 실행을 요청하고 해당 러너가 주입되어 있다면 실행자가 이를 실제로 구동합니다(아래 참조). 결과는 `ctx.executed`에, 요약 줄은 `ctx.output`에 기록됩니다.
- **`reviewer`** — `ctx.executed`가 비어 있지 않고 실행된 *모든* 단계의 `status == "done"`일 때만 통과(`pass`)시키며, 그렇지 않으면 `retry`를 반환합니다. 판정 결과 형태는 다음과 같습니다.

  ```json
  {
    "verdict": "pass",
    "reason": "all steps completed",
    "confidence": 0.9
  }
  ```

  검토 실패 시에는 `{"verdict": "retry", "reason": "no steps executed", "confidence": 0.3}`을 반환합니다.
- **`release`** — `ctx.output`을 확정하고 `{"released": true, "summary": ...}`를 반환합니다.

인식되지 않는 역할은 `{"role": role, "status": "noop"}`을 반환합니다.

### 에이전트 → 워크플로 및 에이전트 → 플러그인 통합

`executor` 역할은 Lattice의 기능 간 통합이 이루어지는 지점입니다. `default_role_runner`에 `workflow_runner` 및/또는 `plugin_runner`가 주입되면 계획 단계에서 이들을 직접 호출할 수 있습니다.

- 단계의 `workflow`(또는 첫 번째 단계 `index == 0`에서의 `inputs["workflow"]`)는 `workflow_runner(wf, ctx)`를 통해 실행되며 결과를 `workflow_result`(실패 시 `workflow_error`)에 기록합니다.
- 단계의 `plugin`은 `plugin_runner(pl, ctx)`를 통해 실행되며 결과를 `plugin_result`(실패 시 `plugin_error`)에 기록합니다.

이것이 바로 **에이전트 → 워크플로** 및 **에이전트 → 플러그인** 연결 경계(seam)입니다.

## 영속성 및 Knowledge Graph 연동

실행이 완료되면 API는 `WorkspaceOSStore.record_agent_run`을 통해 결과를 영속화하며, Knowledge Graph 노드 수집과 워크스페이스 타임라인 이벤트 기록을 동시에 수행합니다.

```python
def record_agent_run(
    self,
    *,
    agent_id: str,
    status: str,
    input_text: str,
    output_text: str,
    user_email: Optional[str],
    timeline: Optional[List[Dict[str, Any]]] = None,
    relationships: Optional[List[str]] = None,
    graph: Any = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    ...
```

수행 동작:

- 실행 레코드(`id`, `agent_id`, `status`, `input`, 1000자로 잘린 `output_preview`, `user_email`, 스코프된 `workspace_id`, `relationships`, `timeline`, `created_at`)를 생성합니다.
- `graph`가 제공된 경우 `graph.ingest_event("AgentRun", ...)`를 호출하고 반환된 `graph_node_id`를 실행 레코드에 저장합니다(수집 실패 시 `graph_error` 기록).
- 워크스페이스 상태에 실행을 추가하고(최근 300개 유지) `agent` / `agent_run` 타임라인 이벤트를 기록합니다.

실행 내역은 워크스페이스별로 스코프가 격리되며, `list_agents(workspace_id=...)`는 해당 스코프에 등록된 `agents`와 최근 실행 목록을 반환합니다.

## HTTP API

모든 경로는 복수형인 `/agents` 아래에 위치합니다.

> **호환성.** `/agents`는 기존 단일 에이전트 `/agent` 엔드포인트와 충돌하지 **않습니다**. 끝의 `s`가 v2 네임스페이스를 완전히 분리하므로 기존 v1 클라이언트는 변경 없이 그대로 작동합니다.

### `GET /agents` — UI 페이지

인증된 사용자가 필요합니다. 정적 디렉터리에서 `agents.html` Multi-Agent UI를 제공하며, UI를 사용할 수 없거나 찾을 수 없는 경우 `404`를 반환합니다.

### `GET /agents/api/roles`

인증된 사용자가 필요합니다. 내장 역할 목록과 기본 파이프라인을 반환합니다.

```json
{
  "roles": [
    {"role": "researcher", "agent_id": "agent:researcher"},
    {"role": "planner",    "agent_id": "agent:planner"},
    {"role": "executor",   "agent_id": "agent:executor"},
    {"role": "reviewer",   "agent_id": "agent:reviewer"},
    {"role": "release",    "agent_id": "agent:release"}
  ],
  "default_pipeline": ["planner", "executor", "reviewer"]
}
```

### `GET /agents/api/runs`

인증된 사용자가 필요하며 읽기는 `gate_read`로 스코프가 제한됩니다. 확인된 워크스페이스 스코프에 대해 등록된 에이전트 목록과 최근 실행 내역을 반환합니다.

```json
{
  "agents": [ { "id": "agent:planner", "name": "Planner", "...": "..." } ],
  "runs":   [ { "id": "agent-run-...", "agent_id": "agent:executor", "status": "ok", "...": "..." } ]
}
```

### `POST /agents/api/run`

인증된 사용자가 필요하며 쓰기는 `gate_write`로 스코프가 제한됩니다. 오케스트레이터를 실행하고 결과를 영속화합니다.

**요청 본문** (`AgentRunRequest`):

```json
{
  "goal": "Summarize the open incidents and draft a status update",
  "roles": ["researcher", "planner", "executor", "reviewer"],
  "inputs": { "steps": ["Collect incidents", "Draft update"] },
  "max_retries": 2
}
```

| 필드 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `goal` | string | — | 필수; 비어 있으면 `400`을 반환합니다. |
| `roles` | string[] | `[]` | 비어 있으면 기본 `CORE_PIPELINE`을 사용합니다. 알 수 없는 역할은 필터링됩니다. |
| `inputs` | object | `{}` | 러너에 그대로 전달됩니다(예: `steps`, `workflow`). |
| `max_retries` | int | `2` | 서버 측에서 `0`~`5` 범위로 제한(clamp)됩니다. |

이 엔드포인트는 주입된 `orchestrator_factory(user, scope)`를 통해 오케스트레이터를 가져와 실행하고, `store.record_agent_run(...)`으로 실행을 기록한 뒤(KG 수집을 위한 `workspace_graph()`와 `relationships`로 전달되는 `roles_run` 포함), `multi_agent_run` 감사 이벤트를 추가합니다.

**응답:**

```json
{
  "run": {
    "id": "agent-run-…",
    "agent_id": "agent:executor",
    "status": "ok",
    "input": "Summarize the open incidents and draft a status update",
    "output_preview": "Completed 2 planned step(s) for: …",
    "relationships": ["agent:researcher", "agent:planner", "agent:executor", "agent:reviewer"],
    "timeline": [ { "event": "start", "...": "..." } ],
    "graph_node_id": "…",
    "created_at": "…"
  },
  "result": {
    "agent_id": "agent:executor",
    "status": "ok",
    "output": "Completed 2 planned step(s) for: …",
    "timeline": [ "…" ],
    "plan": [ { "index": 0, "description": "Collect incidents", "status": "done" } ],
    "review": { "verdict": "pass", "reason": "all steps completed", "confidence": 0.9 },
    "roles_run": ["researcher", "planner", "executor", "reviewer"],
    "retries": 0
  }
}
```

## 타임라인 이벤트 레퍼런스

오케스트레이션 타임라인은 시간순으로 정렬된 평탄한(flat) 이벤트 객체 리스트입니다.

| `event` | 방출 위치 | 주요 필드 |
| --- | --- | --- |
| `start` | `run` (파이프라인 시작 전) | `goal`, `pipeline`, `timestamp` |
| `role` | `_run_role` (단계별) | `role`, `agent_id`, `status`, `result`, `started_at`, `timestamp` |
| `handoff` | `OrchestrationContext.handoff` | `from`, `to`, `note`, `timestamp` |
| `end` | `run` (파이프라인 종료 후) | `status`, `retries`, `timestamp` |

이 타임라인은 실행 결과로 반환될 뿐만 아니라 실행 레코드와 함께 영속화되어 워크스페이스 타임라인과 Knowledge Graph로 곧바로 연결됩니다.
