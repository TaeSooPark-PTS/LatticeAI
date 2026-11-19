[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../LEGACY_COMPATIBILITY.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./LEGACY_COMPATIBILITY.md)

# 레거시 호환성 맵 (Legacy Compatibility Map)

기준 목표 버전: **9.9.1 — 레거시 부채 상환 (Legacy debt paydown)**.

Lattice AI는 `lattice_brain`, `latticeai.services`, `latticeai.api`, `latticeai.runtime`을 중심으로 구성됩니다. 9.9.1에서 루트 심(root-shim) 호환성 유예 기간이 완료되어 `server.py`를 제외한 모든 저장소 루트 임포트 심이 물리적으로 삭제되었으며, 레거시 부채 게이트가 루트 디렉터리를 깨끗하게 유지합니다.

관리 대상 인벤토리는 계층별로 심을 분류합니다.

- `root`: `knowledge_graph.py`와 같은 과거 저장소 루트 임포트 — 외부 진입점(`uvicorn server:app`, Docker CMD, 실행 스크립트)을 위한 `server.py`를 제외하고 **9.9.1에서 제거됨**.
- `brain-flat`: `lattice_brain.store`와 같은 그래프 패키지 분리 이전 임포트 — **8.8.0에서 제거됨**(내부 전용).
- `deprecated-namespace`: 구형 `latticeai.brain.*` 패키지 임포트 — **8.8.0에서 제거됨**(내부 전용).
- `service-alias`: `latticeai.services.agent_runtime` — **8.8.0에서 제거됨**(내부 전용).

제거된 계층들은 `legacy_compatibility.REMOVED_SHIMS` 및 `legacy_shim_report()` 페이로드(`removed`, `lingering`)에 기록되어 도구가 "의도적으로 제거된 항목"과 "실수로 누락된 항목"을 구분할 수 있도록 설계되었습니다.

## 레거시 부채 게이트 (Legacy Debt Gate)

**단 하나의** 게이트가 상환된 부채가 다시 생겨나지 않도록 감시합니다.

- `tests/unit/test_legacy_root_shims.py` — 제거된 심들이 임포트 불가능한 상태로 유지되고 표준 대체 경로들이 정상적으로 임포트되는지 검증합니다.

과거에는 게이트가 두 개였습니다. `scripts/check_legacy_debt.mjs`가 `npm run lint`의 일부로 동일한 규칙을 JavaScript로 검사했으나, 11.8.0 무렵 두 구현이 서로 달라지면서 둘 다 신뢰하기 어려운 상태가 되었습니다. 이에 따라 Python 테스트를 단일 권위 소스로 삼고 `.mjs` 미러는 삭제했습니다. `latticeai.core.legacy_compatibility` 역시 11.6.0에서 플랫폼 코드가 Rust로 이전될 때 함께 정리되었습니다.

## 루트 모듈 맵 (9.9.1에서 제거됨)

| 제거된 루트 모듈 | 표준 임포트 경로 (Canonical import) |
| --- | --- |
| `knowledge_graph.py` | `from lattice_brain.graph.store import KnowledgeGraphStore` |
| `knowledge_graph_api.py` | `from latticeai.api.knowledge_graph import create_knowledge_graph_router` |
| `kg_schema.py` | `from lattice_brain.graph.schema import ...` |
| `auto_setup.py` | `from latticeai.setup.auto_setup import probe, recommend` |
| `llm_router.py` | `import latticeai.models.router` |
| `ltcai_cli.py` | `from latticeai.cli.entrypoint import main` (콘솔 스크립트 `LTCAI`) |
| `mcp_registry.py` | `import latticeai.core.mcp_registry` |
| `local_knowledge_api.py` | `from latticeai.services.local_knowledge import create_local_knowledge_router` |
| `p_reinforce.py` | `from latticeai.services.p_reinforce import PReinforceGardener` |
| `telegram_bot.py` | `from latticeai.integrations.telegram_bot import run_bot` |
| `setup_wizard.py` | `from latticeai.setup.wizard import scan_environment` |
| `tools/` | `from latticeai.tools import execute_tool` |

유일하게 남아 있는 루트 모듈:

| 유지된 루트 모듈 | 유지 사유 |
| --- | --- |
| `server.py` | `latticeai.server_app`에 대한 지연 프록시(lazy proxy); 임포트 시점의 무거운 객체 생성 없이 `uvicorn server:app`, Docker CMD, 실행 스크립트 호환성을 보존합니다. |

## Brain Core 독립 패키지 추출 준비 상태

`lattice_brain`은 물리적 모듈 경로와 일치하는 단일 임포트 표면을 가집니다. 두 가지 구조적 가드가 이를 언제든 독립 패키지로 추출 가능한 상태로 유지합니다.

- `tests/unit/test_brain_core_isolation.py` — 어떤 `lattice_brain` 모듈이라도 `latticeai`를 임포트하면 실패하는 AST 가드입니다(제품이 Brain을 임포트해야 하며 그 역은 절대 허용되지 않습니다).
- `tests/unit/test_legacy_root_shims.py::test_internal_shim_layers_are_gone` — 제거된 내부 심 경로가 다시 임포트 가능해지면 실패합니다.

## 내부 심 계층 (8.8.0에서 제거됨)

| 레거시 계층 | 예시 모듈 | 대체 임포트 | 상태 |
| --- | --- | --- | --- |
| `brain-flat` | `lattice_brain.store`, `lattice_brain.ingest`, `lattice_brain.retrieval` | `lattice_brain.graph.*` | 제거됨 — 내부 전용이었으며 지원되는 외부 진입점이 의존하지 않음 |
| `deprecated-namespace` | `latticeai.brain.*` | `lattice_brain.*` | 제거됨 — `pyproject.toml` 패키지 목록에서도 네임스페이스 제거 |
| `service-alias` | `latticeai.services.agent_runtime` | `lattice_brain.runtime.agent_runtime` | 제거됨 — 런타임 소유권이 Brain Core에 위치 |

## 패키징 노트

`pyproject.toml`은 단일 루트 모듈(`server`)과 `latticeai.*` / `lattice_brain.*` 패키지를 배포하며 npm 패키지도 이를 미러링합니다. 콘솔 스크립트 `LTCAI`는 `latticeai.cli.entrypoint:main`을 직접 가리키고 `bin/ltcai.js`는 `python -m latticeai.cli.entrypoint`를 실행합니다.

## 레거시 모듈 제거 체크리스트

레거시 모듈을 제거하거나 제외하기 전에 다음 절차를 수행하십시오.

1. 저장소 및 생성된 패키지 파일 전체에서 해당 임포트를 검색합니다.
2. 호환성 테스트를 추가하거나 업데이트합니다.
3. 저장소 외부의 새로운 작업 디렉터리에서도 패키지가 정상적으로 임포트되는지 확인합니다.
4. `README.md`, `ARCHITECTURE.md`, `FEATURE_STATUS.md` 및 이 문서를 업데이트합니다.
5. 유닛 테스트, 패키지 빌드, 휠(wheel) 스모크 검증을 실행합니다.
