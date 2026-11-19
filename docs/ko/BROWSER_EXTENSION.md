[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../browser-extension/README.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./BROWSER_EXTENSION.md)

# Lattice AI — 브라우저 확장 (Manifest V3)

현재 릴리스: **13.4.0 — Living Synthesis**.

**로컬** Lattice AI 지식 그래프를 위한 경량 Chrome/Edge(Manifest V3) 확장 프로그램입니다. 클라우드 모델 연동은 호스트 측에 머무르며(선택 사항, 기본값 로컬), 이 확장은 오직 `127.0.0.1`로만 요청을 보냅니다. 핵심 기능은 세 가지입니다.

1. **캡처 (Capture)** — 현재 보고 있는 웹 페이지를 지식 그래프로 전송합니다.
2. **회상 (Recall)** — Brain에 질문하고 답변에 대한 *서버 자체의* 근거 검증(grounding) 판정을 확인합니다.
3. **승인 (Approvals)** — 승인 대기 중인 에이전트 실행 건수를 표시하여 일시 정지된 실행을 놓치지 않도록 합니다.

회상과 승인은 다른 모든 클라이언트 표면과 동일한 `/chat` 및 `/agent/approvals` 엔드포인트를 사용합니다. 확장은 근거 검증 판정을 자체 계산하지 않으며, 판정이 누락된 경우 절대 "근거 있음"으로 표시하지 않고 "근거 확인 불가"로 표시합니다.

## 로컬 우선 보장

- 오직 사용자 PC의 `http://127.0.0.1:<port>`로만 요청을 전송합니다.
- 이 확장 내부에는 클라우드 엔드포인트, 텔레메트리, 외부 서버 호출이 **전혀** 없습니다. 소스를 검색해 보면 유일한 `fetch` 대상은 `127.0.0.1`입니다.
- `host_permissions`는 `http://127.0.0.1/*`과 `http://localhost/*`로만 제한됩니다.

## 전송되는 데이터

활성 탭은 스크립트·스타일·SVG를 제거한 뒤 아래 JSON 형식으로 캡처됩니다.

```json
{
  "url": "https://…",
  "title": "Page title",
  "text": "readable text (≤4 MiB as UTF-8 bytes)",
  "selected_text": "current selection (optional)",
  "captured_at": "ISO-8601"
}
```

이 페이로드는 `POST /api/browser/ingest-current-tab`으로 전송되며, 통합 인제스트 파이프라인을 통해 `source_type=browser_tab`으로 처리됩니다(`latticeai/api/browser.py` 참고).

## 설치 방법 (개발자 모드)

1. 로컬에서 Lattice AI를 실행하고 로그인합니다(확장은 `credentials: include`로 로컬 세션 쿠키를 재사용합니다).
2. `chrome://extensions`를 열고 **개발자 모드(Developer mode)**를 켭니다.
3. **압축해제된 확장 프로그램을 로드합니다(Load unpacked)** → `browser-extension/` 폴더를 선택합니다.
4. 툴바 아이콘을 클릭하고 포트(기본 `4825`)를 확인한 뒤 **Send this page**를 누릅니다.

로컬 인제스트 요청이 30초 내에 완료되지 않으면 팝업이 요청을 중단하고 Send 버튼을 복구하여 다시 시도할 수 있게 합니다.

## 백엔드 계약

| 엔드포인트 | 소스 유형 | 비고 |
|---|---|---|
| `POST /api/browser/read-url` | `web_url` | 런타임이 공개 URL을 로컬에서 직접 가져와 텍스트를 추출합니다. |
| `POST /api/browser/ingest-current-tab` | `browser_tab` | 위 확장 페이로드를 수신하며 크기 제한 및 정제를 적용합니다. |

두 경로 모두 단일 네이티브 인제스트 진입점(`lattice-ingest` → `lattice_core::graph_write`)으로 연결되며 `pre_tool`/`post_tool` 훅을 실행하고 출처(`get_provenance(node_id)`)를 기록합니다. `rust/lattice-ingest/tests/browser_api_replay.rs`가 이 계약을 검증합니다.
