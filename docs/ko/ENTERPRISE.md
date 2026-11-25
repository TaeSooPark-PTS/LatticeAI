[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../ENTERPRISE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./ENTERPRISE.md)

# Lattice AI — Enterprise 에디션

> **상태:** 기반 구조 및 확장 경계(extension seam) 전용 문서입니다. 이 저장소의 오픈소스 Community 에디션은 아래 기능 중 **어느 것도 활성화해 배포하지 않으며**, Enterprise 구현체를 **포함하지 않습니다**. 이 문서는 배포된 코드가 아니라 경계와 로드맵을 설명합니다.

Lattice AI는 **오픈 코어(open-core)** 모델을 따릅니다.

- **Community** (이 저장소, MIT 라이선스)는 단독으로도 완전하게 동작합니다. 로컬 모델, 지식 그래프, 개인(Personal) 및 조직(Organization) 워크스페이스, 역할, 스냅샷, 영속 컨텍스트, AI 파이프라인, 멀티 에이전트 워크플로우, 스킬, 감사 가능한 타임라인, 그리고 전체 v2 워크스페이스 플랫폼(Plugin SDK, Workflow Designer, Multi-Agent Runtime, Realtime Collaboration)을 모두 포함합니다.
- **Enterprise**는 안정적인 런타임 확장 경계를 통해 조직 규모의 고급 거버넌스와 배포 기능을 연결하는 별도 배포 플러그인입니다. Community 빌드에는 절대 번들되지 않습니다.

Community를 일부러 제한해 Enterprise를 판매하지 않는 경계 원칙은 [EDITION_STRATEGY.md](./EDITION_STRATEGY.md)를 참고하세요.

## 확장 경계 (Extension Seam)

확장 경계는 11.6.0에서 제품 서버와 함께 Rust로 이전되었으며 현재 [`rust/lattice-platform/src/adminops/admin/enterprise.rs`](../../rust/lattice-platform/src/adminops/admin/enterprise.rs)에 있습니다.

- `Edition` — `community`(기본값) 또는 `enterprise`.
- `EnterpriseCapability` — 아래 예약된 Enterprise 기능 열거형.
- `CapabilityProvider` — Enterprise 플러그인이 구현하는 프로토콜.
- `capability_registry` — 프로세스 전역 레지스트리. Enterprise 플러그인은 시작 시 `capability_registry.register_provider(...)`를 호출합니다. 그전까지 기본 `CommunityCapabilityProvider`는 모든 기능 질의에 "Community / disabled"로 응답합니다.

Community 코드는 확장 지점에서 `is_capability_enabled(EnterpriseCapability.X)`를 조회합니다. Community 빌드에서는 이 값이 항상 `False`이므로 Community 코드 경로가 그대로 실행되며 어떤 기능도 차단되지 않습니다.

실시간 에디션 및 기능 매트릭스는 `GET /workspace/editions`에서 노출되며, 워크스페이스 요약(`GET /workspace/os` → `edition`)과 `/admin#enterprise`의 Enterprise 관리자 UI에도 표시됩니다.

Community는 또한 `GET /admin/enterprise` 및 `GET /admin/enterprise/siem-export`에 설명용 관리자 표면을 제공합니다. 이 엔드포인트는 정책·내보내기·엔벨로프 구조를 보여주되 Enterprise 전용 기능은 비활성화로 보고하며 외부로 이벤트를 스트리밍하지 않습니다.

## 저장소 경계 (SQLite 기본, PostgreSQL/pgvector 내보내기 대상)

`lattice-host`는 로컬 **SQLite**(`knowledge_graph.sqlite`, WAL 모드 + FTS5)를 단일 정본 런타임 저장소로 사용합니다.

- `lattice-host`에서 `LATTICEAI_STORAGE_ENGINE=postgres`를 설정하면 Postgres가 활성화된 척하면서 조용히 SQLite에 기록하는 대신 시작 시점에 fail-closed로 중단합니다.
- `POST /api/brain/storage/migrate-postgres`와 `POST /api/brain/storage/postgres/docker`는 Community에서도 스키마/행 진단과 Compose 매니페스트를 깔끔하게 준비합니다. 분석 및 읽기 복제본 파이프라인을 위한 실시간 SQLite-to-PostgreSQL/pgvector 스냅샷 내보내기는 Python 유틸리티 `lattice_brain.storage.postgres_migration`이 제공합니다.

## Enterprise 기능 로드맵

아래 항목은 확장 경계를 안정적이고 발견 가능하게 유지하기 위해 `EnterpriseCapability`에 선언되어 있습니다. Community에는 구현되어 있지 않습니다.

| 기능 | 열거형 | 요약 |
|-----------|------|---------|
| 고급 SSO | `SSO_ADVANCED` | 강제 SSO, 세션 정책, SAML/OIDC 페더레이션 |
| IdP 프로비저닝 | `IDP_PROVISIONING` | Entra ID / Okta 사용자 및 그룹 프로비저닝 |
| SCIM | `SCIM` | SCIM 2.0 사용자/그룹 수명 주기 동기화 |
| 고급 RBAC/ABAC | `RBAC_ABAC_ADVANCED` | 기본 4개 역할을 넘어서는 사용자 지정 역할 및 속성 기반 정책 |
| 테넌트 격리 | `TENANT_ISOLATION` | 엄격한 멀티 테넌트 데이터 격리 |
| 컴플라이언스 보존 | `COMPLIANCE_RETENTION` | 법적 보존(legal-hold), 보존 기간, 불변 감사 로그 |
| SIEM 내보내기 | `SIEM_EXPORT` | Splunk/Sentinel 등으로의 스트리밍 감사·이벤트 내보내기 |
| 프라이빗 VPC | `PRIVATE_VPC` | 사설 네트워크 / 고객 VPC 배포 제어 |
| 에어갭 배포 | `AIR_GAPPED_DEPLOYMENT` | 완전 오프라인 설치 및 업데이트 채널 |
| DLP 정책 | `DLP_POLICY` | 데이터 유출 방지(DLP) 스캔 및 강제 적용 |
| eDiscovery | `EDISCOVERY` | 법적 증거 개시를 위한 검색, 보존 및 내보내기 |
| 관리자 정책 팩 | `ADMIN_POLICY_PACKS` | 중앙 관리형 조직 정책 번들 |

## Enterprise 플러그인 연결 방식 (예시)

```python
from latticeai.core.enterprise import (
    Edition,
    EnterpriseCapability,
    capability_registry,
)


class EnterpriseProvider:
    def edition(self) -> Edition:
        return Edition.ENTERPRISE

    def is_enabled(self, capability: EnterpriseCapability) -> bool:
        return capability in MY_LICENSED_CAPABILITIES


# Enterprise 패키지 시작 시:
capability_registry.register_provider(EnterpriseProvider())
```

이 저장소에는 어떠한 Enterprise 코드나 자격 증명도 없으며, Community 빌드는 위 코드를 임포트하지 않습니다.
