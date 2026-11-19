[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../EDITION_STRATEGY.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./EDITION_STRATEGY.md)

# Lattice AI — 에디션 전략 (오픈 코어)

이 문서는 기여자와 사용자가 경계를 예측할 수 있도록 Community와 Enterprise 분리 원칙을 기록합니다.

## 에디션 구성

- **Community** — 이 저장소(MIT 라이선스). 로컬 우선 **AI 워크스페이스 플랫폼**으로, 로컬 모델, 지식 그래프, 개인(Personal) **및** 조직(Organization) 워크스페이스, 역할 기반 멤버십, 스냅샷, 영속 컨텍스트, AI 파이프라인, 멀티 에이전트 워크플로우, 스킬, 감사 가능한 타임라인, 그리고 전체 v2 플랫폼(**Plugin SDK**, **Workflow Designer**, **Multi-Agent Runtime**, **Realtime Collaboration**)을 제공합니다. Community는 그 자체로 완성된 제품입니다.
- **Enterprise** — 조직 규모의 거버넌스, 신원 관리, 컴플라이언스, 배포 기능을 추가하는 별도 배포 플러그인입니다. 별도로 배포되고 라이선스가 부여됩니다. 자세한 내용은 [ENTERPRISE.md](./ENTERPRISE.md)를 참고하세요.

## 원칙

1. **Community의 기능은 절대 제한하지 않습니다.** Enterprise를 유도하기 위해 기존 Community 기능을 제거하거나 속도를 낮추거나 잠그지 않습니다. 기능 확장 경계는 오직 "이 *Enterprise* 기능이 사용 가능한가?"만 답하며(오픈 빌드에서는 `False`), Community 코드 경로를 비활성화하지 않습니다.
2. **오픈 코어 경계는 포크가 아니라 런타임 seam입니다.** 경계는 Rust(`rust/lattice-platform/src/adminops/admin/enterprise.rs`)의 `capability_registry.register_provider(...)`로 정의됩니다. Community 저장소에는 Enterprise 구현이 없으며 Enterprise 코드를 임포트하지 않습니다.
3. **기능은 숨기지 않고 선언합니다.** `EnterpriseCapability`는 예약된 기능을 열거해 계약을 투명하고 안정적으로 유지합니다. Community는 이 중 어느 것도 구현하지 않습니다.
4. **로컬 우선이 계속 기본값입니다.** Enterprise는 테넌트 격리, 에어갭 배포, SIEM 내보내기 같은 선택지를 추가할 뿐 기본 경험을 사용자 기기 밖으로 옮기지 않습니다.
5. **기본적으로 우아하게 폴백합니다.** 오작동하거나 존재하지 않는 프로바이더가 Community 요청을 깨뜨려서는 안 되며, 레지스트리는 항상 Community 프로바이더로 폴백합니다.

## 영역별 위치

| 항목 | Community (이 저장소) | Enterprise (별도 배포) |
|--------|------------------------|------------------------|
| 개인 및 조직 워크스페이스 | ✅ | — |
| 기본 역할 (`owner`/`admin`/`member`/`viewer`) | ✅ | — |
| 스냅샷 / 영속 컨텍스트 / AI 파이프라인 / 멀티 에이전트 워크플로우 / 스킬 | ✅ | — |
| Plugin SDK (매니페스트, 라이프사이클, 권한 경계) | ✅ | 플러그인 권한에 대한 RBAC/ABAC |
| Workflow Designer (작성/실행/실행 이력) | ✅ | 조직 승인 게이트, 예약 트리거 |
| Multi-Agent Runtime 2.0 (역할/핸드오프/재시도) | ✅ | 정책 범위 내 자율 실행 |
| Realtime Collaboration (프레즌스 + 활동 피드) | ✅ | 크로스 테넌트 팬아웃, 보존 정책 |
| 감사 타임라인 (로컬) | ✅ | — |
| 기능 seam 및 열거형 | ✅ (선언) | ✅ (구현) |
| SSO/SCIM/IdP 프로비저닝 | seam만 제공 | ✅ |
| 테넌트 격리, 컴플라이언스 보존, eDiscovery | seam만 제공 | ✅ |
| SIEM 내보내기, DLP, 관리자 정책 팩 | seam만 제공 | ✅ |
| 프라이빗 VPC / 에어갭 배포 | seam만 제공 | ✅ |

## 런타임에서 에디션 확인하기

- `GET /workspace/editions` → 에디션 및 기능별 매트릭스.
- `GET /workspace/os` → 워크스페이스 요약의 `edition` 블록.
- `/admin#enterprise` → 관리자 정책, 감사 내보내기, SIEM 미리보기, 조직 설정, 기능 상태 UI.
- `GET /admin/enterprise` 및 `GET /admin/enterprise/siem-export` → 연동을 위한 Community 안전 설명용 Enterprise 표면.
- 에디션 확인 기준은 `GET /workspace/editions`와 `rust/lattice-platform/src/adminops/admin/enterprise.rs`입니다.

Community 빌드에서는 위 경로가 모두 `community`를 보고하며 모든 Enterprise 기능은 `false`입니다.
