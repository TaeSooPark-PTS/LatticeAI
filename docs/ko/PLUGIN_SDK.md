[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../PLUGIN_SDK.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./PLUGIN_SDK.md)

# Lattice AI 플러그인 SDK (Plugin SDK)

Plugin SDK는 Lattice AI 에이전틱 워크스페이스 플랫폼의 확장 계층입니다. v2.2.0은 v2.0의 플러그인 모델을 유지하면서 실행 관측성(observability)과 로컬 마켓플레이스 템플릿 기반을 추가합니다. 스킬, 도구, 워크플로 템플릿, 액션을 버전과 권한이 부여된 단일 단위인 *플러그인(plugin)*으로 패키징할 수 있으며, 각 플러그인은 설정된 `plugins` 루트 아래에 `plugin.json` 매니페스트를 포함하는 디렉터리로 구성됩니다.

이 SDK는 의도적으로 **추가적(additive)**으로 설계되었습니다. 플러그인은 기존의 Skill, Tool, Workflow 표면을 *확장*할 뿐 절대 대체하지 않습니다. 이미 설치된 독립형 스킬들은 아무런 영향 없이 그대로 작동하며, 스킬을 번들링한 플러그인도 별도의 병렬 레지스트리를 만드는 대신 기존 스킬 레지스트리에 해당 스킬을 등록합니다.

> **호환성.** v1.x 데이터와 API는 모두 보존됩니다. Plugin SDK는 새로운 코드와 영속 상태(워크스페이스 스토어 내부의 `plugin_registry`)를 추가하며 기존 v1.x 계약은 변경하지 않습니다. 새로운 HTTP 라우트는 `/plugins/registry` 및 관련 액션 경로 아래에 위치하므로 기존의 `/plugins/directory` 마켓플레이스 라우트와 충돌하지 **않습니다**.

호스트 SDK 버전은 다음과 같이 노출됩니다.

```python
PLUGIN_SDK_VERSION = "2.2.0"
```

## v2.2에서 추가된 기능

- `execute_action(...)`이 기존 워크스페이스 타임라인/실시간 피드를 통해 `plugin_started`, `plugin_completed`, `execution_failed` 이벤트를 방출합니다.
- 플러그인 출력이 에이전트 컨텍스트 패킷에 담겨 전달될 수 있으며 에이전트/워크플로 실행 이력에서 리플레이될 수 있습니다.
- 로컬 템플릿 카탈로그(`latticeai.core.marketplace`)가 외부 클라우드 마켓플레이스 서비스 없이도 플러그인, 워크플로, 에이전트 템플릿 메타데이터와 내보내기/가져오기, 설치 훅, 템플릿 레지스트리를 제공합니다.

---

## `plugin.json` 매니페스트

모든 플러그인은 자신의 디렉터리 루트에 `plugin.json`을 포함합니다. 매니페스트는 파싱 및 검증을 거쳐 불변 객체인 `PluginManifest`로 변환됩니다.

### 스키마

| 필드 | 타입 | 필수 여부 | 설명 |
| --- | --- | --- | --- |
| `id` | string | 예 | 소문자 영숫자 및 `-` 또는 `_`, 2~64자(`^[a-z0-9][a-z0-9_-]{1,63}$`). 디렉터리 이름과 일치해야 합니다. |
| `name` | string | 예 | 사람이 읽을 수 있는 이름. 생략 시 `id`로 폴백합니다. |
| `version` | string | 예 | 시맨틱 버전(`^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$`). |
| `description` | string | 아니요 | 짧은 요약 설명. |
| `author` | string | 아니요 | 작성자 또는 조직. |
| `lattice_version` | string | 아니요 | 플러그인이 요구하는 최소 호스트 버전. 단순 버전(`"2.2.0"`) 또는 접두사 형식(`">=2.2.0"`)을 지원하며 비어 있으면 "모든 호스트"를 의미합니다. |
| `permissions` | string[] | 아니요 | 권한 허용 목록의 부분집합이어야 합니다. 알 수 없는 값은 거부됩니다. |
| `provides` | object | 아니요 | 플러그인이 제공하는 항목. 키는 `("skills", "tools", "workflows", "actions")` 중 하나여야 하며 각 값은 이름 리스트입니다. |
| `entrypoint` | string | 아니요 | 선택적 코드 진입점을 위해 예약된 필드입니다. |
| `homepage` | string | 아니요 | 프로젝트 또는 문서 URL. |

`provides` 객체는 플러그인이 확장하는 표면을 선언합니다.

```json
{
  "provides": {
    "skills": ["..."],
    "tools": ["..."],
    "workflows": ["..."],
    "actions": ["..."]
  }
}
```

허용되는 키는 고정되어 있습니다.

```python
PLUGIN_PROVIDES = ("skills", "tools", "workflows", "actions")
```

### 검증 규칙

`validate_manifest(data, *, path="")`는 `(manifest_or_None, errors)`를 반환합니다. 오류가 하나라도 있는 매니페스트는 첫 번째 반환값으로 `None`을 돌려주므로 호출자가 유효하지 않은 플러그인을 실수로 실행 가능한 것으로 취급할 수 없습니다. 검증기는 다음을 강제합니다.

- `id`, `name`, `version`이 존재하는지 확인.
- `id`가 ID 패턴과 일치하고 `version`이 유효한 시맨틱 버전인지 확인.
- `permissions`가 리스트이며 모든 항목이 `PLUGIN_PERMISSIONS`에 속하는지 확인.
- `provides`가 객체이며 모든 키가 `PLUGIN_PROVIDES`에 속하고 각 값이 리스트인지 확인.
- `lattice_version`이 설정된 경우 현재 호스트와 호환되는지 확인.

```python
def validate_manifest(
    data: Dict[str, Any], *, path: str = ""
) -> Tuple[Optional[PluginManifest], List[str]]: ...
```

### 파싱된 매니페스트

```python
@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    lattice_version: str = ""
    permissions: Tuple[str, ...] = ()
    provides: Dict[str, List[str]] = field(default_factory=dict)
    entrypoint: str = ""
    homepage: str = ""
    path: str = ""
```

`PluginManifest.public()`은 계산된 `compatible` 플래그와 디스크 상의 `path`를 포함하여 API에서 사용하는 JSON 안전 형태를 반환합니다.

```json
{
  "id": "hello-world",
  "name": "Hello World",
  "version": "1.0.0",
  "description": "...",
  "author": "Lattice AI",
  "lattice_version": "2.0.0",
  "permissions": ["read_workspace", "run_skills"],
  "provides": {
    "skills": ["hello_skill"],
    "workflows": ["hello-workflow"],
    "actions": ["greet"]
  },
  "entrypoint": "",
  "homepage": "https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/PLUGIN_SDK.md",
  "path": "/abs/path/to/plugins/hello-world",
  "compatible": true
}
```

---

## 권한 (Permissions)

권한은 작고 고정된 허용 목록(allow-list)으로 관리됩니다. 매니페스트는 이 집합에 속한 권한만 요청할 수 있으며, 실행 경계는 플러그인이 매니페스트에 선언하지 않았거나 설치 시점에 부여받지 못한 모든 기능을 거부합니다.

```python
PLUGIN_PERMISSIONS = (
    "read_workspace",
    "write_workspace",
    "read_graph",
    "write_graph",
    "run_tools",
    "run_skills",
    "run_workflows",
    "run_agents",
    "network",
    "manage_memory",
)
```

| 권한 | 부여되는 기능 |
| --- | --- |
| `read_workspace` | 워크스페이스 상태 읽기. |
| `write_workspace` | 워크스페이스 상태 수정. |
| `read_graph` | Knowledge Graph 읽기. |
| `write_graph` | Knowledge Graph에 쓰기. |
| `run_tools` | 도구 호출 (`run_tool` 액션에 필요). |
| `run_skills` | 스킬 호출 (`run_skill` 액션에 필요). |
| `run_workflows` | 워크플로 호출 (`run_workflow` 액션에 필요). |
| `run_agents` | 에이전트 호출 (`run_agent` 액션에 필요). |
| `network` | 외부 네트워크 호출 수행. |
| `manage_memory` | 영구 메모리 읽기/쓰기. |

이 목록은 커뮤니티 계약을 변경하지 않고도 엔터프라이즈 계층에서 더 세분화된 정책을 얹을 수 있도록 의도적으로 간결하게 유지됩니다.

---

## 버전 호환성 (Version compatibility)

플러그인의 `lattice_version`은 **최소 요구 버전**으로 취급됩니다. 호스트의 메이저(major) 버전이 일치해야 하고 호스트 버전이 요구 버전 이상이어야 합니다. 값이 비어 있거나 생략된 경우에는 "모든 호스트"와 호환되는 것으로 간주합니다.

```python
def is_compatible(required: str, current: str = PLUGIN_SDK_VERSION) -> bool:
    """True if a plugin requiring ``required`` runs on ``current`` host version.

    ``required`` is a minimum (major must match, host must be >= required).
    Empty / missing requirement is treated as "any host".
    """
```

앞부분의 `>=`는 비교 전에 제거되므로 아래 두 형태는 동일하게 작동합니다.

```json
{ "lattice_version": "2.0.0" }
{ "lattice_version": ">=2.0.0" }
```

호스트 버전이 `2.2.0`일 때의 호환성 예시:

| 요구 버전 | 호환 여부 | 이유 |
| --- | --- | --- |
| `""` (생략됨) | 예 | 모든 호스트 허용. |
| `2.0.0` / `>=2.0.0` | 예 | 동일한 메이저 버전이며 호스트 버전이 요구 버전 이상임. |
| `2.2.0` / `>=2.2.0` | 예 | 동일한 메이저 버전이며 현재 호스트 버전과 정확히 일치함. |
| `2.3.0` | 아니요 | 호스트 버전이 최소 요구 버전보다 낮음. |
| `1.0.0` | 아니요 | 메이저 버전 불일치. |
| `3.0.0` | 아니요 | 메이저 버전 불일치. |

호환성은 검증 시점과 설치 시점에 모두 확인되므로 호환되지 않는 플러그인은 절대 활성화될 수 없습니다.

---

## 수명 주기 (Lifecycle)

```
탐색(discover) -> 검증(validate) -> 설치(install) -> 활성화/비활성화(enable/disable) -> 제거(uninstall)
```

수명 주기 *상태*(설치 여부 / 활성화 여부 / 버전 / 상태)는 작은 `store` 포트를 통해 워크스페이스 스토어에 위임되므로 플러그인은 스킬과 동일한 로컬 우선 JSON 영속성, 워크스페이스 스코핑, 타임라인 이벤트를 재사용합니다. 레지스트리 자체는 매니페스트 파싱과 실행 경계만 담당합니다.

```python
class PluginRegistry:
    def __init__(self, plugins_dir: Path | str, *, store: Any = None): ...
```

### 탐색 (Discover)

`discover()`는 `plugins_dir`에서 `<id>/plugin.json` 매니페스트들을 스캔합니다. 임포트 시점에는 파일시스템 I/O를 전혀 수행하지 않으며 호출될 때만 디스크를 읽습니다. 각 매니페스트는 검증을 거쳐 `valid`와 `invalid`로 분류됩니다.

```python
def discover(self) -> Dict[str, Any]:
    # {"valid": List[PluginManifest], "invalid": List[{path, id?, errors}]}
    ...
```

`catalog()`는 UI를 위해 탐색된 매니페스트와 영속화된 수명 주기 상태를 병합하여 `sdk_version`, 권한 및 제공 항목 어휘, 병합된 `plugins` 리스트, `invalid` 엔트리, `plugins_dir`, `total`을 반환합니다.

### 검증 (Validate)

디스크를 건드리지 않고도 `validate_manifest` 함수나 `POST /plugins/validate` HTTP 엔드포인트를 통해 매니페스트를 독립적으로 검증할 수 있습니다.

### 설치 (Install)

`install(plugin_id, *, register_skill=None)`은 탐색된 플러그인을 활성화합니다. 호환성을 다시 확인하고, 플러그인이 번들링한 스킬들을 **기존** 스킬 레지스트리에 등록한 뒤 스토어에 수명 주기 상태를 기록합니다.

```python
def install(
    self,
    plugin_id: str,
    *,
    register_skill: Optional[Callable[[str, str], Any]] = None,
) -> Dict[str, Any]: ...
```

`register_skill(skill_name, plugin_id)`는 호스트가 주입하므로 플러그인은 별도의 병렬 레지스트리를 소유하는 대신 기존 스킬 레지스트리를 확장합니다. `provides.skills` 아래의 각 이름에 대해 `register_skill`을 호출하며, 반환되는 딕셔너리에는 공개 매니페스트, 영속화된 `registry` 엔트리, `registered_skills` 목록이 포함됩니다.

설치 시 스토어의 `plugin_registry`에 다음 엔트리가 저장됩니다.

```python
entry = self.store.mark_plugin_installed(
    plugin_id,
    version=manifest.version,
    metadata={
        "permissions": list(manifest.permissions),
        "provides": {k: list(v) for k, v in manifest.provides.items()},
        "registered_skills": registered_skills,
    },
)
```

이 시점에 승인된 권한이 `metadata.permissions`에 캡처되며, 실행 경계는 바로 이 "설치 시점에 부여된 권한" 집합을 강제합니다.

플러그인이 없거나 유효하지 않거나 `lattice_version`이 호스트와 호환되지 않으면 `install`은 `PluginError`를 발생시킵니다.

### 활성화 / 비활성화 (Enable / Disable)

```python
def set_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]: ...
```

비활성화된 플러그인은 설치 상태를 유지하지만 액션을 실행할 수 없습니다(실행 경계가 `blocked` 반환). 활성화/비활성화는 영속화된 레지스트리 엔트리의 토글입니다.

### 제거 (Uninstall)

```python
def uninstall(self, plugin_id: str) -> Dict[str, Any]: ...
```

제거는 레지스트리 엔트리를 `installed: false`, `enabled: false`로 표시합니다. 디스크 상의 플러그인 디렉터리를 삭제하지 않는 비파괴적 동작이므로 다시 설치하면 재활성화됩니다.

### 영속화된 상태

상태는 `plugin_id`를 키로 하는 딕셔너리인 `WorkspaceOSStore.plugin_registry`에 저장됩니다.

```json
{
  "id": "hello-world",
  "installed": true,
  "enabled": true,
  "version": "1.0.0",
  "install_status": "ready",
  "validation_status": "valid",
  "metadata": {
    "permissions": ["read_workspace", "run_skills"],
    "provides": {
      "skills": ["hello_skill"],
      "workflows": ["hello-workflow"],
      "actions": ["greet"]
    },
    "registered_skills": ["hello_skill"]
  },
  "updated_at": "..."
}
```

각 수명 주기 변경은 `plugins` 채널 아래에 워크스페이스 타임라인 이벤트(`plugin_installed`, `plugin_uninstalled`, `plugin_enabled`, `plugin_disabled`)도 함께 기록합니다.

---

## 권한 기반 실행 경계 (Permissioned execution boundary)

플러그인 액션은 절대 임의의 코드를 직접 실행하지 않습니다. 모든 액션은 활성화 여부와 권한을 강제한 뒤 호스트가 제공한 러너로 디스패치하는 `execute_action`을 통과해야 합니다.

```python
def execute_action(
    self,
    plugin_id: str,
    action: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    runners: Optional[Dict[str, Callable[..., Any]]] = None,
) -> PluginExecutionResult: ...
```

`runners`는 기능(`"tools"`, `"skills"`, `"workflows"`, `"agents"`)을 호스트가 주입한 콜러블에 매핑합니다. 내장 액션과 요구 권한의 매핑은 다음과 같습니다.

| 액션 | 기능(Capability) | 필요한 권한 |
| --- | --- | --- |
| `run_tool` | `tools` | `run_tools` |
| `run_skill` | `skills` | `run_skills` |
| `run_workflow` | `workflows` | `run_workflows` |
| `run_agent` | `agents` | `run_agents` |
| *(그 외 사용자 정의 액션)* | `actions` | *(없음)* |

실행 경계는 다음 순서대로 검사를 적용합니다.

1. **알 수 없거나 유효하지 않은 플러그인** → `status: "error"` (`plugin not found or invalid`).
2. **비활성화된 플러그인** → `status: "blocked"` (`plugin is not enabled`).
3. **매니페스트에 선언되지 않은 권한** → `status: "blocked"` (`plugin did not declare required permission '...'`).
4. **설치 시점에 부여되지 않은 권한** → `status: "blocked"` (`permission '...' not granted at install time`). 부여된 권한 집합은 영속화된 `metadata.permissions`에서 읽습니다.
5. **해당 기능에 연결된 호스트 러너가 없음** → `status: "skipped"` (`no host runner for capability '...'`). 기본적으로 안전하게 동작하여 러너가 없더라도 호출자를 크래시시키지 않습니다.
6. 위 검사를 모두 통과하면 러너를 호출합니다. 성공 시 `output`과 함께 `status: "ok"`를 반환하고, 예외 발생 시 `reason`과 함께 `status: "error"`를 반환합니다.

### 결과 구조체 (`PluginExecutionResult`)

```python
@dataclass
class PluginExecutionResult:
    plugin_id: str
    action: str
    status: str  # "ok" | "blocked" | "error" | "skipped"
    output: Any = None
    reason: str = ""
```

---

## HTTP API

> 모든 경로는 기존 `/plugins/directory` 마켓플레이스 라우트와 충돌하지 않도록 `/plugins/registry` 및 형제 액션 라우트 아래에 위치합니다.

### `GET /plugins/registry`

인증된 사용자가 필요합니다. 전체 `catalog()`(SDK 버전, 권한/제공 어휘, 병합된 플러그인 목록, 유효하지 않은 매니페스트, `plugins_dir`, 총계)를 반환합니다.

### `GET /plugins/registry/{plugin_id}`

인증된 사용자가 필요합니다. 공개 매니페스트와 영속화된 레지스트리 상태를 반환하며 플러그인이 없거나 유효하지 않으면 `404`를 반환합니다.

### `POST /plugins/validate`

인증된 사용자가 필요합니다. 디스크를 건드리지 않고 매니페스트 딕셔너리를 검증합니다.

### `POST /plugins/install`

**관리자(admin)** 권한이 필요합니다. 플러그인을 설치하고(번들링된 스킬 등록 포함) `plugin_install` 감사 이벤트를 기록합니다.

### `POST /plugins/uninstall`

**관리자(admin)** 권한이 필요합니다. 플러그인을 제거하고 `plugin_uninstall` 감사 이벤트를 기록합니다.

### `POST /plugins/enable`

인증된 사용자가 필요합니다. 플러그인을 활성화합니다.

### `POST /plugins/disable`

인증된 사용자가 필요합니다. 플러그인을 비활성화합니다.

### `POST /plugins/execute`

인증된 사용자가 필요합니다. 실행 경계를 통해 액션을 실행하고 결과 상태를 포함한 `plugin_execute` 감사 이벤트를 기록합니다.

### `GET /plugins/sdk`

인증된 사용자가 필요합니다. Plugin SDK UI 페이지(`plugins.html`)를 제공합니다.

---

## 번들 예제 플러그인

플랫폼의 `plugins` 루트 아래에는 두 개의 예제 플러그인이 기본 포함되어 있습니다.

### `hello-world`

스킬, 워크플로 템플릿, 커스텀 액션을 하나로 번들링하는 방법을 보여줍니다. `read_workspace`와 `run_skills` 권한만 요청합니다.

### `git-insights`

권한이 부여된 도구 실행 경계를 통해 읽기 전용 git 상태 및 로그 인사이트를 노출합니다. `lattice_version`에 접두사 형태인 `">=2.0.0"`을 사용하는 예시를 보여줍니다.

---

## 플러그인 작성 체크리스트

1. 플러그인 `id`와 이름이 일치하는 `plugins/<id>/` 디렉터리를 생성합니다.
2. 최소한 `id`, `name`, `version`이 포함된 `plugin.json`을 작성합니다.
3. 꼭 필요한 권한만 요청합니다(`PLUGIN_PERMISSIONS`의 부분집합).
4. 제공하는 항목(`skills` / `tools` / `workflows` / `actions`)을 `provides`에 선언합니다.
5. 최소 지원 호스트 버전을 `lattice_version`에 지정합니다(예: `">=2.0.0"`).
6. `POST /plugins/validate`(또는 `validate_manifest`)로 검증합니다.
7. 설치(관리자), 활성화 후 `POST /plugins/execute`를 통해 액션을 실행합니다.

기억하십시오. 플러그인은 기존의 스킬, 도구, 워크플로를 **확장**할 뿐 절대 대체하지 않으며 모든 v1.x 데이터와 API는 그대로 유지됩니다.
