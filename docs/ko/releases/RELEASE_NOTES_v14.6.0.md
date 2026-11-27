[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.6.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.6.0.md)

# Lattice AI v14.6.0 — Within Your Scope (2026-11-28)

`v14.6.0`은 자동화가 기본적으로 누구의 항목에 반응하는지 바꾸고, 제품이 실제로 하지 않은 일을 한 것처럼 보고하던 여러 지점을 고칩니다.

## 주요 변경

### 1. 소유자 범위 자동화 (보안 및 동작 변경)
- `brain_event` 자동화는 기본적으로 소유자 범위(`owner_scope: "self"`)입니다. 소유자가 만든 항목에서만 실행됩니다.
- `owner_scope: "workspace"`는 같은 워크스페이스의 다른 구성원이 소유한 공유 항목에서 실행됩니다. 개인 항목이나 대화 항목에서는 실행되지 않으며 워크스페이스 경계를 넘지 않습니다.
- **다중 사용자 설치의 필수 조치:** 동료의 업로드를 감시하던 기존 트리거는 자동화 양식에서 **Workspace** 범위로 설정해야 합니다. 단일 사용자 설치에는 영향이 없습니다.
- 새 `source_types`·`title_contains` 필터를 API와 자동화 양식에서 사용할 수 있습니다.

### 2. 수정
- 기각되거나 승인된 종합 제안이 `pending`으로 남아 재제안을 막던 문제(오래된 `effective_status`)를 고쳤습니다.
- Hugging Face 리비전 고정이 다운로드와 로드 경로에 실제로 적용됩니다. `LATTICEAI_HF_REVISIONS`로 고정값을 재정의할 수 있습니다. endpoint·token·revision을 떨어뜨리던 조용한 `TypeError` 재시도를 제거했습니다. 이미 받은 모델은 다시 고정되지 않으며, 고정되지 않은 저장소는 여전히 `main`을 사용합니다.
- 훅 카탈로그가 각 훅의 `executed`, `implementation`, `not_executed_reason`을 보고하므로, 목록에 있지만 실행되지 않는 훅은 그렇다고 표시됩니다.

### 3. 인터페이스
- 명령 팔레트에서 대화 결과를 열 수 있고 상단 바 버튼이 추가되었습니다.
- 검색 결과에서 바로 작업할 수 있습니다.
- 기억 관리자: 기억 보기·추가·검색·잊기.
- "Brain에 저장" 상태가 실제 상태를 표시합니다.
- 필터와 미리보기가 있는 문서 라이브러리.
- 실행 이력 필터에 취소된 실행, "더 보기", 오류 상태가 포함됩니다.

### 4. 테스트
- HNSW 교체(churn) 재현율 테스트.

## 알려진 공백
- 워크스페이스 안의 비공개 문서는 구현되지 않았습니다. 이번 릴리스에 포함되지 않으며 비공개 업로드 기능을 주장하지 않습니다. 자세한 내용은 [ROADMAP](../../ROADMAP.md)을 참고하세요.

## 빌드 산출물
- `dist/ltcai-14.6.0-py3-none-any.whl`
- `dist/ltcai-14.6.0.tar.gz`
- `ltcai-14.6.0.tgz`
- `dist/ltcai-14.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.6.0_aarch64.dmg`
