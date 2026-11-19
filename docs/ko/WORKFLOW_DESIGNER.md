[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../WORKFLOW_DESIGNER.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./WORKFLOW_DESIGNER.md)

# Lattice AI 워크플로 디자이너 (Workflow Designer)

Workflow Designer(**v2.0.0**에서 도입되고 **v2.2.0**에서 강화됨)는 자동화 작업을 **타입화된 노드들의 방향성 그래프(directed graph)** 형태로 구축, 검증, 실행, 검사, 리플레이, 내보내기 및 가져오기할 수 있게 해줍니다. 워크플로는 단일 `trigger` 노드에서 시작해 노드에서 노드로 이동하며 `output` 노드에 도달하고, 각 실행 가능 노드를 실제 도구 레지스트리, 스킬 레지스트리, 플러그인 레지스트리 또는 멀티 에이전트 오케스트레이터를 호출하는 주입된 *러너(runner)*로 디스패치합니다.

실행 모델은 [`rust/lattice-platform/src/governance/workflow_designer/definition.rs`](../../rust/lattice-platform/src/governance/workflow_designer/definition.rs)(주입된 러너를 사용하는 순수 로직)에, HTTP 표면은 [`rust/lattice-platform/src/governance/workflow_designer/handlers.rs`](../../rust/lattice-platform/src/governance/workflow_designer/handlers.rs)에 위치합니다. 두 모듈 모두 11.6.0에서 Python으로부터 Rust로 이전되었습니다. 영속성은 기존 `WorkspaceOSStore`를 재사용하므로 2.0 이전의 워크플로 이력도 그대로 보존됩니다.

엔진 버전은 다음과 같이 노출됩니다.

```python
WORKFLOW_ENGINE_VERSION = "2.2.0"
```

## 기억에 기반한 Brain 자동화 (Memory-grounded Brain automation)

Brain Home은 회상된 지식을 바탕으로 추천 액션과 자동화 레시피를 제안하되, 사용자 모르게 작업을 자동으로 활성화하지 않습니다. 레시피는 먼저 비활성화되고 검토 가능한 워크플로 초안(draft)으로 설치됩니다. 사용자는 기억 초점, 출처, 근거, 에이전트 역할을 검토한 뒤 동일한 워크플로를 그 자리에서 명시적으로 활성화할 수 있으며, 활성화 과정에서 중복 레시피가 생성되거나 사용자가 검토하고 수정한 이름, 프롬프트, 역할, 노드 편집 내용이 덮어써지지 않습니다.

활성화된 레시피가 워크스페이스 스코프의 성공적인 Brain 수집(ingestion) 이벤트를 수신하면 워크플로는 연구자 우선(researcher-first) 에이전트 체인을 실행합니다.

```text
Brain 이벤트 -> researcher 회상 -> planner -> executor -> reviewer -> 검토 대기열(review queue)
```

`researcher`는 스코프된 `MemoryService.recall` 컨텍스트를 전달받습니다. 트리거 식별자, 워크스페이스, 출처 이력(provenance), 실행 ID는 실행과 검토 전 과정에 걸쳐 유지됩니다. 채팅, 업로드, 로컬 폴더, 노트, 웹 및 호환 레거시 수집 이벤트가 정규화되며 수집이 실패한 경우에는 절대 레시피가 트리거되지 않습니다. 생성된 작업은 검토되지 않은 외부 동작을 즉시 수행하는 대신 검토 대기열에 스테이징됩니다.

## v2.2 강화 사항

- 에이전트 노드의 출력이 워크플로 컨텍스트에 캡처되어 `last_output`을 통해 후속 플러그인이나 출력 노드로 전달될 수 있습니다.
- 플러그인 노드 실패 시 실행이 실패(`failed`)로 기록되고 실시간 실행 이벤트가 방출됩니다.
- 워크플로 실행은 `/workflows/api/runs/{run_id}/replay`를 통해 수행 주체, 시간, 사유, 입력, 출력, 결정을 담은 프레임 단위로 리플레이할 수 있습니다.
- `record_workflow_run`이 기존 SSE 활동 피드를 통해 `workflow_started`, `workflow_completed`, `execution_failed` 이벤트를 방출합니다.

---

## 노드 유형 (Node types)

워크플로는 고정된 노드 유형 어휘(`workflow_engine.NODE_TYPES`)로 구성됩니다.

```python
NODE_TYPES = (
    "trigger",
    "tool",
    "skill",
    "plugin",
    "agent",
    "condition",
    "output",
)
```

- `trigger` — 구조적 진입점. 워크플로당 정확히 하나만 존재해야 합니다.
- `output` — 구조적 종료점. 실행 출력을 기록합니다.
- `condition` — 실행 컨텍스트에 대한 안전한 비교 연산자를 기반으로 분기합니다.
- `tool` / `skill` / `plugin` / `agent` — **실행 가능(executable)** 노드이며 각각 일치하는 패밀리의 러너로 디스패치됩니다.

### 노드 구조

모든 노드는 `id`, `type`, 선택적 `name`, `config` 객체, 그리고 다음 노드 ID를 가리키는 `next` 포인터(`null`이면 분기 종료)를 가진 JSON 객체입니다.

```json
{
  "id": "n1",
  "type": "tool",
  "name": "Fetch report",
  "config": { "tool": "http_get", "args": { "url": "https://example.com" } },
  "next": "n2"
}
```

### 조건(`condition`) 노드 구조

`condition` 노드는 `next`를 사용하지 않고 평가 결과를 다음 노드 ID에 매핑하는 `branches`를 정의합니다. 표준 분기는 `true`와 `false`입니다(각 분기는 경로를 종료하기 위해 `null`일 수 있습니다).

```json
{
  "id": "check",
  "type": "condition",
  "name": "Has results?",
  "config": { "left": "count", "op": ">", "right": 0 },
  "branches": { "true": "notify", "false": "output" }
}
```

---

## 검증 — `validate_definition`

```python
def validate_definition(workflow: Dict[str, Any]) -> List[str]: ...
```

오류 문자열 리스트를 반환하며 빈 리스트(`[]`)는 워크플로가 유효함을 의미합니다. 정의는 먼저 정규화된 뒤 다음 규칙들을 기준으로 검사됩니다.

1. **비어 있지 않음** — 노드가 없는 워크플로는 거부됩니다(`"workflow has no nodes"`).
2. **고유한 ID** — 중복된 노드 ID(`"duplicate node ids"`)나 ID가 누락된 노드(`"node missing id"`)는 거부됩니다.
3. **정확히 하나의 트리거** — 트리거가 0개면 `"workflow must have a trigger node"`, 2개 이상이면 `"workflow must have exactly one trigger node"`를 보고합니다.
4. **알려진 노드 유형** — `NODE_TYPES` 외부의 `type`은 `"node '<id>': unknown type '<type>'"`을 보고합니다.
5. **유효한 엣지 대상** — 모든 `next`와 `condition` 분기 대상은 `null`이거나 실제 존재하는 노드 ID를 가리켜야 하며, 그렇지 않으면 `"node '<id>' points at unknown node '<target>'"`을 보고합니다.
6. **조건 노드의 분기 정의 필수** — 비어 있지 않은 `branches` 딕셔너리가 없는 `condition` 노드는 `"condition node '<id>' must define branches (e.g. true/false)"`를 보고합니다.

---

## 정규화 및 하위 호환성 — `normalize_definition`

`normalize_definition`은 노드 기반 정의를 반환하며 **입력 객체를 절대 직접 변이시키지 않습니다.**

- 워크플로에 이미 비어 있지 않은 `nodes` 리스트가 있다면 그대로 반환합니다.
- 레거시 `steps` 리스트(2.0 이전 형태)만 있다면 **선형 `trigger -> tool... -> output` 노드 체인으로 자동 승격(lift)**합니다. 각 단계는 원래 액션과 인자를 `config`에 담은 `tool` 노드가 되며 `metadata`에 `"lifted_from_steps": true` 마커가 추가됩니다.

> **호환성.** 이 메커니즘 덕분에 **2.0 이전의 워크플로 이력이 v2.0에서도 변경 없이 그대로 작동합니다.** 저장된 `{"steps": [...]}` 워크플로는 영속화된 이력을 다시 쓸 필요 없이 검증 및 실행 시점에 `trigger`로 시작하는 노드 체인으로 해석됩니다.

---

## 실행 — `WorkflowEngine.run`

`run`은 먼저 정의를 정규화하고 검증합니다. 검증에 실패하면 즉시 `status = "failed"`와 오류 목록을 담은 단일 `validation` 타임라인 항목을 반환합니다.

검증을 통과하면 트리거 노드에서 시작해 그래프를 따라 이동하며 단계별 `timeline`을 구축합니다. 실행 컨텍스트는 `inputs`로 초기화되며, 각 실행 가능 노드의 결과는 후속 노드와 조건 노드가 참조할 수 있도록 `context["last_output"]`과 `context[<node id>]` 양쪽에 저장됩니다.

### 전체 실행 상태 (`WorkflowRun.status`)

- `"failed"` — 실행 가능 노드 중 하나라도 오류가 발생했거나 순환 방지 가드(cycle guard)가 작동한 경우.
- `"partial"` — 오류는 없지만 최소 하나 이상의 노드가 `skipped`된 경우.
- `"ok"` — 모든 노드가 성공적으로 완료된 경우.

### 순환 방지 가드 (Cycle guard)

잘못 연결된 `next` 순환 구조가 실행을 무한 대기 상태에 빠뜨리지 않도록 엔진은 하드 캡(`_MAX_STEPS = 100`)을 강제합니다. 100단계를 초과하면 `error` 상태의 `guard` 타임라인 항목(`"exceeded 100 steps (cycle?)"`)이 추가되고 실행은 `failed`로 표시됩니다.

### 안전한 조건 평가 — **`eval` 미사용**

조건은 실행 컨텍스트에 대한 비교 연산자로만 평가됩니다. **`eval`이나 임의 코드 실행은 절대 존재하지 않습니다.**

```json
{ "left": "<context key>", "op": "==", "right": "<literal>" }
```

지원되는 `op` 값: `==`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `truthy`(기본값). **알 수 없는 키나 연산자, 또는 평가 오류는 모두 안전하게 닫혀(fail-closed) `false` 분기로 이동**하므로 잘못 구성된 조건이 실행을 크래시시키지 않습니다.

---

## 러너 및 도구 노드 거버넌스

> **안전성: 도구 노드는 파괴적인 도구를 절대 암묵적으로 실행하지 않습니다.** `tool` 러너는 도구를 직접 실행하는 대신 호출 요청과 **거버넌스 결정**을 기록하고 도구의 권한 레코드를 확인하여 `{"tool", "args", "recorded": true, "permission": {...}}`를 반환합니다. 실행(exec) 또는 파괴적 도구는 승인을 필요로 하며 워크플로 노드에서 암묵적으로 실행되지 않습니다.

---

## 실행 이력 및 영속성

실행 내역은 `WorkspaceOSStore.record_workflow_run`을 통해 영속화됩니다. 기록된 각 실행은 **로컬 우선(local-first)**이며 다음을 수행합니다.

- Knowledge Graph에 `WorkflowRun` 이벤트로 수집됩니다(`graph_node_id` 저장).
- 워크스페이스 타임라인 이벤트(`record_timeline_event("workflow", "workflow_run", ...)`)를 방출합니다.
- 워크플로 자체의 이벤트 로그에 교차 링크됩니다.
- 최근 **300**개의 실행을 유지합니다(`state["workflow_runs"][-300:]`).

---

## HTTP API

모든 라우트는 `/workspace/workflows`와의 충돌을 피하기 위해 `/workflows` 아래에 네임스페이스가 지정됩니다.

- `GET /workflows` — Workflow Designer UI 제공.
- `GET /workflows/api/definitions` — 워크플로 목록 조회 (`?q=` 검색 지원, 읽기 스코프).
- `POST /workflows/api/definitions` — 워크플로 생성 (`validate_definition` 검증 적용, 쓰기 스코프).
- `GET /workflows/api/definitions/{id}` — 단일 워크플로 조회.
- `PATCH /workflows/api/definitions/{id}` — 이름/노드/메타데이터 수정.
- `POST /workflows/api/validate` — 저장 없이 정의 검증 (`{ "ok": true, "errors": [] }`).
- `POST /workflows/api/definitions/{id}/run` — 워크플로 실행 및 결과 영속화.
- `GET /workflows/api/definitions/{id}/runs` — 특정 워크플로의 실행 이력 조회.
- `GET /workflows/api/runs` — 현재 스코프의 전체 실행 이력 조회.
- `GET /workflows/api/export/{id}` — 휴대 가능한 정의 전용 JSON 내보내기.
- `POST /workflows/api/import` — 내보낸 JSON으로부터 새 워크플로 생성.

---

## 호환성 요약

- **추가 전용(Additive only).** v1.x 동작이나 데이터를 제거하지 않고 타입화된 노드 그래프와 `/workflows` API 표면을 제공합니다.
- **레거시 데이터 보존.** 2.0 이전의 `{"steps": [...]}` 워크플로도 `normalize_definition`을 통해 즉시 `trigger -> ... -> output` 체인으로 승격되어 그대로 검증 및 실행됩니다.
- **동일한 영속성 계층.** 워크플로와 실행 이력은 기존 `WorkspaceOSStore`에 의해 저장되고 워크스페이스 스코프를 유지하며 Knowledge Graph 및 워크스페이스 타임라인에 계속 공급됩니다.
