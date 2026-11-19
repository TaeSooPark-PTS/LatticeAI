[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../REALTIME_COLLABORATION.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./REALTIME_COLLABORATION.md)

# Lattice AI 실시간 협업 (Realtime Collaboration)

실시간 협업(Realtime Collaboration)은 Lattice AI 워크스페이스에 라이브 **프레즌스(presence)** 레지스트리와 **활동 피드(activity feed)**를 제공하는 하위 시스템입니다. v2.2.0부터는 에이전트, 인계(handoff), 검토, 워크플로, 플러그인, 재시도, 실패에 대한 워크스페이스 스코프 실행 관측성도 함께 전달합니다. 이 기능은 11.6.0부터 Rust에서 네이티브로 구현된 인프로세스 pub/sub 버스인 [`RealtimeBus`](../../rust/lattice-platform/src/workspaceos/realtime.rs)를 통해 Server-Sent Events(SSE)로 전송됩니다.

설계 목표는 새로운 전송 프로토콜이나 외부 의존성, 혹은 두 번째 이벤트 시스템을 추가하지 않고도 "지금 워크스페이스에서 무슨 일이 일어나고 있는가"(워크스페이스 생성, 그래프 인덱싱, 에이전트 및 워크플로 실행, 플러그인 활성화, 현재 접속자)를 실시간으로 표면화하는 것입니다.

버스 버전은 다음과 같습니다.

```python
REALTIME_VERSION = "2.2.0"
```

v2.2 실행 이벤트 유형에는 `agent_started`, `handoff_created`, `handoff_accepted`, `handoff_completed`, `review_requested`, `review_approved`, `retry_requested`, `workflow_started`, `workflow_completed`, `plugin_started`, `plugin_completed`, `execution_failed`, `execution_cancelled`가 포함됩니다.

---

## 왜 SSE인가?

WebSockets 대신 SSE(Server-Sent Events)를 의도적으로 선택했습니다.

- 코드베이스가 **이미 SSE를 통해 모델 출력을 스트리밍**하고 있으므로 와이어 포맷과 클라이언트 패턴이 익숙합니다.
- SSE는 **추가 의존성이 전혀 필요하지 않습니다** — 로컬 우선 배포가 사용하는 기존 단일 HTTP 포트 위에서 표준 `text/event-stream`으로 작동합니다.
- 추가 포트나 프로토콜 업그레이드 핸드셰이크 없이 기존 단일 포트 로컬 우선 서버를 그대로 통과합니다.

버스는 **인프로세스(in-process)**로 동작하며 인메모리 구독자 큐(subscriber queues)로 이벤트를 팬아웃(fan-out)합니다.

> **호환성.** 이 하위 시스템은 순수하게 추가적(additive)입니다. 새로운 `/realtime/*` 엔드포인트와 `/app#/activity` 페이지를 도입하고 선택적 `event_sink` 훅을 통해 기존 `WorkspaceOSStore`에 연결됩니다. 기존 v1.x 데이터 형태, API, 동작은 전혀 바뀌지 않습니다. 구독자가 0명일 때 버스는 아무 작업도 하지 않으므로(no-op) 단일 사용자 로컬 모드는 이전과 완벽히 동일하게 동작합니다.

---

## 아키텍처 한눈에 보기

```
record_timeline_event(...)        (모든 워크스페이스/그래프/에이전트/워크플로 쓰기)
        │
        ▼
WorkspaceOSStore.event_sink ──► RealtimeBus.publish(event)
                                      │
                  ┌───────────────────┼───────────────────────┐
                  ▼                   ▼                         ▼
        링 버퍼 피드          매칭되는 구독자 큐          프레즌스 레지스트리
        (상한 200개)          (크기 제한, 가장 오래된 항목 폐기)
                                      │
                                      ▼
                         GET /realtime/stream  (SSE 프레임)
```

### 핵심 통합 지점: 단일 `event_sink`

`WorkspaceOSStore`는 단 하나의 실시간 훅만 노출합니다. 의미 있는 **모든** 워크스페이스 쓰기 작업에서 이미 실행되는 `record_timeline_event` 메서드가 마지막에 이 싱크를 호출합니다.

```python
def record_timeline_event(self, area, event_type, payload, workspace_id=None):
    state = self.load_state()
    event = {
        "id": f"timeline-{...}",
        "area": area,
        "event_type": event_type,
        "timestamp": _now(),
        "workspace_id": self._resolve_scope(workspace_id, state),
        "payload": payload,
    }
    # ... 타임라인에 영속화 ...
    if self.event_sink is not None:
        try:
            self.event_sink(event)
        except Exception:
            # 실시간 전달은 최선형(best-effort)이며 절대 쓰기 작업을 실패시켜서는 안 됩니다.
            pass
    return event
```

스토어가 `event_sink(event)`를 호출하고 `RealtimeBus`가 이를 `publish`로 연결하므로, 버스를 싱크로 연결하는 것만으로 **모든** 워크스페이스 / 그래프 / 에이전트 / 워크플로 / 메모리 / 스킬 / 플러그인 활동이 자동으로 실시간 피드로 흘러갑니다. 호출부마다 개별 계측 코드를 유지할 필요가 없으며 중복된 이벤트 시스템도 없습니다.

스토어가 방출하는 대표적인 `area` / `event_type` 쌍은 다음과 같습니다.

| `area`      | 예시 `event_type`                                 |
|-------------|---------------------------------------------------|
| `workspace` | `workspace_created`, `member_added`, `workspace_archived` |
| `graph`     | `answer_trace`, `indexing_paused`, `indexing_resumed` |
| `agent`     | `agent_run`                                       |
| `workflow`  | `workflow_created`, `workflow_run`, `workflow_edited` |
| `memory`    | `memory_upserted`, `memory_deleted`               |
| `skills`    | `skill_installed`, `skill_enabled`                |
| `plugins`   | `plugin_installed`, `plugin_enabled`              |
| `presence`  | `join`, `leave` (버스 자체가 직접 방출)           |

---

## `publish()` — 핵심 계약

```python
def publish(self, event: Dict[str, Any]) -> Dict[str, Any]: ...
```

`publish`는 버스의 핵심이며 스토어의 동기식 쓰기 경로에서 언제 호출하더라도 안전하도록 설계되었습니다.

- **동기 호출 가능(Sync-callable).** `await` 없이 어떤 동기 코드에서도 즉시 호출할 수 있습니다.
- **절대 예외를 던지지 않음(Never raises).** 큐 오버플로나 기타 오류는 내부에서 삼켜지며 최악의 경우에도 프레임 하나가 드롭될 뿐 쓰기 작업이 깨지지 않습니다.
- **절대 블로킹하지 않음(Never blocks).** 구독자 큐의 크기가 제한되어 있으므로 발행자는 느리거나 연결이 끊긴 소비자를 기다리지 않습니다.

호출될 때마다 다음 작업을 수행합니다.

1. 단조 증가하는 `seq`와 `received_at` 타임스탬프를 부여하고 이벤트를 일관된 보강(enriched) 형태로 정규화합니다.
2. 보강된 이벤트를 **크기가 제한된 링 버퍼 피드**(`_FEED_LIMIT = 200`)에 추가합니다(오래된 이벤트는 앞쪽에서 밀려납니다).
3. 이벤트의 `workspace_id`를 허용하는 스코프를 가진 모든 구독자에게 이벤트를 팬아웃합니다.

### 보강된 이벤트 형태

```json
{
  "seq": 42,
  "received_at": "2026-06-01T10:15:30",
  "area": "workflow",
  "event_type": "workflow_run",
  "workspace_id": "ws_marketing",
  "payload": { "run_id": "wf-run-7", "workflow_id": "wf_3", "status": "ok" },
  "id": "timeline-9f1c...",
  "timestamp": "2026-06-01T10:15:30"
}
```

### 백프레셔(Backpressure): 가장 오래된 항목을 버리는 유한 큐

각 구독자는 최대 크기 100(`maxsize=100`)의 큐를 가집니다. 큐가 가득 차면 발행자는 블로킹하는 대신 큐에서 가장 오래된 이벤트를 버리고 최신 이벤트를 넣습니다. 따라서 느린 클라이언트는 서버 전체를 멈추게 하는 대신 일부 중간 프레임만 건너뛰게 됩니다.

---

## 워크스페이스 격리 (Workspace isolation)

모든 이벤트는 `workspace_id`를 가집니다. 구독자는 호출자가 볼 수 있는 워크스페이스 ID 집합인 **워크스페이스 스코프**와 함께 생성되며, 자신의 스코프가 허용하는 이벤트만 수신합니다.

```python
def accepts(self, workspace_id: Optional[str]) -> bool:
    # ``None`` 스코프 = 로컬 사용자가 볼 수 있는 모든 항목 허용 (개인/비스코프).
    if self.workspace_scope is None:
        return True
    if workspace_id is None:
        return True
    return workspace_id in self.workspace_scope
```

여기서 두 가지 규칙이 도출됩니다.

- **스코프가 지정되지 않은(`None`) 이벤트는 항상 전달됩니다.** `workspace_id`가 `None`인 이벤트는 모든 구독자에게 도달하며, 스코프가 `None`인 구독자도 모든 이벤트를 받습니다. 덕분에 **단일 사용자 로컬 모드**에서는 별도의 스코프 제약 없이 모든 활동을 볼 수 있습니다.
- **스코프가 지정된 이벤트는 필터링됩니다.** 구체적인 스코프 집합을 가진 구독자는 해당 집합에 포함된 `workspace_id`의 이벤트(및 비스코프 이벤트)만 수신합니다.

피드 조회(`recent`)와 프레즌스 조회(`presence`)에도 동일한 스코프 필터가 적용되므로 호출자는 권한이 없는 다른 워크스페이스의 활동을 절대 읽을 수 없습니다.

---

## `stream()` — 테일 리플레이, 라이브 프레임, 하트비트

클라이언트가 연결되면 `stream`은 먼저 새로운 구독자가 즉시 맥락을 파악할 수 있도록 **짧은 최근 이력(최대 10개의 스코프 내 이벤트)을 리플레이**한 뒤, 이어서 도착하는 **라이브 프레임**을 전송합니다. `heartbeat` 간격(기본 15초) 동안 새 이벤트가 없으면 SSE 주석 프레임을 보냅니다.

```
: heartbeat
```

하트비트는 프록시가 유휴 연결을 끊는 것을 방지하고 단일 사용자 로컬 모드에서 아무 일이 없을 때 연결이 멈춘 것처럼 보이지 않게 합니다. 클라이언트 연결이 종료되면 `finally` 블록에서 구독자가 즉시 정리되어 큐 누수가 발생하지 않습니다.

---

## API 레퍼런스

모든 엔드포인트는 `require_user`를 통한 인증을 요구합니다. 인증이 비활성화된 로컬 모드에서는 `require_user`가 빈 문자열을 반환하고 스코프가 `None`(전체 보기)으로 설정됩니다.

### `GET /activity`

`/app#/activity`로의 호환성 리다이렉트입니다. Activity UI는 SPA의 일부이며 `/realtime/feed`, `/realtime/presence`, `/workspace/time-machine`을 읽습니다.

### `GET /realtime/stream`

SSE 스트림 엔드포인트입니다. 호출자의 허용 스코프를 확인하고 무작위 ID로 새 구독자를 등록한 뒤 `media_type="text/event-stream"` 응답을 스트리밍합니다.

```
Cache-Control: no-cache
X-Accel-Buffering: no
Connection: keep-alive
```

### `GET /realtime/feed`

스코프 필터링된 최근 활동 피드를 최신순으로 반환합니다. 쿼리 파라미터 `limit`의 기본값은 `50`이며 `200`개 버퍼 한도 내로 제한됩니다.

```json
{
  "events": [ /* 최신순 보강 이벤트 */ ],
  "stats": {
    "version": "2.2.0",
    "subscribers": 1,
    "presence": 2,
    "feed_size": 17,
    "transport": "sse"
  }
}
```

### `GET /realtime/presence`

스코프 필터링된 프레즌스 레지스트리와 동일한 `stats` 블록을 반환합니다.

### `POST /realtime/presence/join`

클라이언트를 현재 접속 중(present)으로 등록합니다.

```json
{
  "client_id": "optional-client-id",
  "workspace_id": "ws_marketing"
}
```

두 필드 모두 선택 사항이며 `client_id`가 생략되면 서버가 생성합니다. 스코프 내 구독자들에게 `presence`/`join` 이벤트가 발행됩니다.

### `POST /realtime/presence/leave`

`client_id`가 제공되면 프레즌스 레지스트리에서 해당 클라이언트를 제거하고 `presence`/`leave` 이벤트를 발행합니다.

```json
{ "status": "ok" }
```

---

## 클라이언트 사용 예시 (`EventSource`)

표준 SSE 스트림이므로 브라우저 내장 `EventSource`로 바로 소비할 수 있습니다.

```javascript
// 1. 프레즌스 등록 (선택 사항이지만 프레즌스 레지스트리를 활성화함).
const clientId = crypto.randomUUID();
await fetch("/realtime/presence/join", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ client_id: clientId, workspace_id: "ws_marketing" }),
});

// 2. 라이브 활동 스트림 구독.
const source = new EventSource("/realtime/stream");

source.onmessage = (e) => {
  const event = JSON.parse(e.data);
  console.log(`[${event.seq}] ${event.area}/${event.event_type}`, event.payload);
};

source.onerror = () => {
  // EventSource는 자동으로 재연결하며, 재연결 시 서버가 최근 테일을 리플레이합니다.
};

// 3. 페이지 언로드 시 프레즌스 해제.
window.addEventListener("beforeunload", () => {
  navigator.sendBeacon(
    "/realtime/presence/leave",
    new Blob([JSON.stringify({ client_id: clientId })], { type: "application/json" }),
  );
});
```

---

## 운영 노트

- **한도(Limits).** 피드 링 버퍼: `200`개 이벤트(`_FEED_LIMIT`). 구독자당 큐: `100`개 이벤트(`_QUEUE_MAX`). 하트비트 간격: `15`초.
- **비영속성(No persistence).** 실시간 피드, 프레즌스 레지스트리, 구독자 목록은 메모리에만 존재하며 서버 재시작 시 초기화됩니다. 활동의 영구 기록은 스토어 자체의 `timeline`(최대 500개 이벤트)이 담당하며 실시간 버스는 그 위의 라이브 뷰입니다.
- **단일 프로세스.** 버스는 로컬 우선 배포를 위해 인프로세스로 설계되었으며 다중 서버 프로세스 간에는 상태를 동기화하지 않습니다.
- **`stats()`**는 상태 확인 및 관측성을 위해 `version`(`2.2.0`), 활성 `subscribers`, `presence` 수, `feed_size`, `transport`(`"sse"`)를 보고합니다.
