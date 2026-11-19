[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../HYBRID_CLOUD_KG_STREAMING.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./HYBRID_CLOUD_KG_STREAMING.md)

# 하이브리드 클라우드 + 로컬 Knowledge Graph 스트리밍

> 상태: **10.1.0에 배포됨**, **10.1.1에 인앱 제어 UI 추가**  
> 원칙: **로컬 Brain이 자산이고, 클라우드는 옵트인 워커입니다.**  
> 화면 경로: `환경설정 → 내 지식이 나가는 범위` (`frontend/src/components/NetworkBoundaryPanel.tsx`), `/api/network-boundary`, `LATTICEAI_NETWORK_MODE`.

## 목표

로컬 Knowledge Graph는 기기에 그대로 남습니다. 클라우드 LLM(및 선택적 멀티모달)은 사용자가 선택적으로 호출하는 워커입니다. 꼭 필요한 최소 관련 노드만 기기 밖으로 나가며, 스트리밍된 답변은 프로비넌스와 검토 게이트를 거쳐 로컬 Brain을 확장합니다.

## 단계별 요약

| 단계 | 브랜치 | 제공 항목 |
|-------|--------|-----------|
| 0 | `feature/hybrid-cloud-kg-streaming` | 아키텍처 및 핵심 계약 |
| 1 | `…-phase1` | 네트워크 다이얼, OpenAI 어댑터, 하이브리드 헬퍼, 기본 확장 |
| 2 | `…-phase2` | `/chat` 분기, 토큰 가드레일, 지식 추출, 미리보기 API |
| **3** | `…-phase3` | **정책, Review Queue 쓰기 경로, 멀티모달 계약, UI 패널 모듈** |

## 3단계 상세

### 하이브리드 정책 (`HybridPolicyService`)
다음 항목을 영속적으로 제어하는 다이얼을 제공합니다.
- `blocked_node_types` / `blocked_metadata_flags` (하드 서킷 브레이커와 합집합으로 적용)
- `auto_commit` (기본값 **false**)
- `allow_multimodal` (기본값 **false**)
- `min_extraction_confidence`

API:
```
GET  /api/network-boundary/policy
POST /api/network-boundary/policy
     { "auto_commit": false, "allow_multimodal": false, "blocked_node_types": ["Secret"] }
```

### Review Queue 쓰기 경로
`CloudResponseIngestor`는 다음과 같이 동작합니다.
1. `ReviewQueueService`가 연결되어 있으면 항상 `change_proposal` 검토 항목을 큐에 등록합니다.
2. `plan.auto_commit`이 `true`이고 저장소 쓰기 API가 존재할 때만 그래프에 자동 기록합니다.

사용자는 기존 Review Center에서 클라우드 응답에서 도출된 기억 확장을 승인하거나 거절합니다.

### 멀티모달 / 비디오
클라우드 멀티모달 *스트리밍* 경로는 존재하지 않습니다. 프로바이더 어댑터 형태로 작성되었던 `multimodal_streaming` 모듈은 어떤 프로바이더나 라우트에도 연결되지 않았고, 설계 초안이 실제 기능처럼 보이지 않도록 11.5.2에서 삭제되었습니다.

멀티모달 수집 자체는 로컬 기능이며 이 문서의 클라우드 경계와는 별개입니다(`lattice_brain/ingestion/routing.py` 및 `allow_multimodal` 게이트 참고). 향후 클라우드 비디오 프로바이더가 도입된다면 git 이력에서 해당 모듈(`latticeai/services/multimodal_streaming.py`, 11.5.2 제거)을 복구할 수 있습니다.

### UI
`NetworkBoundaryPanel`(React) + `GET /api/network-boundary/ui-state`:
- 로컬 ↔ 클라우드 전환 (명시적 확인 포함)
- 외부로 전송될 노드의 투명성 미리보기
- 현재 정책 플래그 표시

`frontend/src/pages/System.tsx`에서 `PermissionModePanel` 옆에 마운트되어 모든 설치 환경의 설정 탭에 표시됩니다. 10.1.0에 포함되었던 단독 파일 `static/app/network-boundary-panel.js`는 아무 곳에서도 마운트하지 않았고 `npm run build:assets`가 비우는 Vite 출력 디렉터리에 있었으므로 10.1.1에서 제거되었습니다.

## 종단 간 흐름 (3단계)

```
사용자가 cloud_allowed 활성화 (UI 패널 또는 API)
    → 선택 정책: auto_commit / multimodal / 민감도 필터
사용자가 채팅 메시지 전송
    → /chat이 모드 해석
    → cloud_allowed인 경우: 최소 KG 컨텍스트 + 토큰 예산 검사
    → 클라우드 스트림 (SSE)
    → 구조화 추출 (Decision/Task/Concept)
    → Review Queue 항목 (change_proposal)
    → 정책 허용 시 선택적 auto-commit
```

## 환경 설정

```bash
export LATTICEAI_CLOUD_API_KEY=sk-...
export LATTICEAI_CLOUD_MODEL=gpt-4o-mini
export LATTICEAI_NETWORK_MODE=local_only
export LATTICEAI_CLOUD_MAX_TOKENS_PER_TURN=2500
export LATTICEAI_CLOUD_MAX_TOKENS_PER_SESSION=50000
```

## 개인정보 보호 (변하지 않는 핵심 규칙)

1. 기본값은 `local_only`
2. 클라우드 사용에는 명시적 동의 필요
3. 최소한의 압축 컨텍스트만 호스트 밖으로 전송
4. 하드 서킷 브레이커 + 사용자 민감도 필터 적용
5. 사용자가 `auto_commit`에 옵트인하지 않는 한 클라우드 유래 노드는 Review Queue 경유
6. 정책에서 명시적으로 켜지 않으면 멀티모달은 비활성화

## 완료 기준 (3단계)

- 사용자가 코드 변경 없이 민감도 필터와 자동 커밋을 설정할 수 있음
- 클라우드 기반 KG 확장이 Review Center에 실행 가능한 제안으로 표시됨
- 클라우드 모드와 정책 플래그가 모두 켜지지 않으면 멀티모달 경로가 실행되지 않음
- UI 패널이 메인 React 번들을 별도로 재빌드하지 않고도 정상 동작함
