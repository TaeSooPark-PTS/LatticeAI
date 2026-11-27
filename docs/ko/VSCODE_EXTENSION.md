[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../vscode-extension/README.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./VSCODE_EXTENSION.md)

# Lattice AI — VS Code 확장

**어떤 AI 모델로 교체해도 지식을 영속적으로 유지하는 로컬 우선 Digital Brain용 에디터 확장 프로그램입니다.**

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../../LICENSE)

Lattice AI 확장은 VS Code, Cursor, VSCodium을 로컬 우선 Digital Brain에 연결합니다. 에디터에서 바로 파일을 영속 Brain 컨텍스트로 보내고, Brain 기반 채팅과 모델 워크플로를 실행하며, 코딩 작업을 수행할 수 있습니다.

## 현재 릴리스

**14.4.0 — Measured Retrieval**은 실제 EmbeddingGemma 2 및 `BAAI/bge-reranker-v2-m3` 가중치로 검색을 측정하고, 그 측정에서 드러난 검색 결함을 고친 Lattice AI 호스트에 맞춘 버전입니다. 상세 내역은 [docs/ko/releases/RELEASE_NOTES_v14.4.0.md](./releases/RELEASE_NOTES_v14.4.0.md)에서 확인할 수 있습니다.

**핵심 프로토콜은 기존과 호환됩니다.** 동기화, 상태, 회상, 임시 제안, `POST /agent`(`stream:true`), `strict`/`trusted`/`bypass` 모드는 이전과 동일하게 동작하며, 클라우드 에스컬레이션과 MCP는 에디터가 아닌 호스트에서 실행됩니다.

이 확장은 에디터를 데스크톱·웹 앱과 동일한 로컬 우선 Digital Brain, 명시적 동의 게이트, 교체 가능한 모델 워크플로(`MLX-VLM` / `llama.cpp` / `Ollama` / `LM Studio` / 선택적 클라우드), 제안 우선 Review Center에 연결합니다.

## 빠른 시작

Lattice AI 워크스페이스를 설치하고 실행합니다.

```bash
pip install ltcai
LTCAI
```

Apple Silicon 로컬 모델 지원을 함께 설치하려면:

```bash
pip install "ltcai[local]"
```

그다음 확장을 설치합니다.

- [VS Code Marketplace: parktaesoo.ltcai](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
- [Open VSX: parktaesoo.ltcai](https://open-vsx.org/extension/parktaesoo/ltcai)

확장은 기본적으로 `http://localhost:4825`에 자동 연결됩니다.

## 주요 기능

| 영역 | 설명 |
| --- | --- |
| 채팅 패널 | 워크스페이스 및 그래프 컨텍스트를 활용해 로컬 또는 클라우드 모델과 대화 |
| 현재 파일 작업 | 활성 파일에서 질문, 편집, 설명, 리팩터링, 테스트 생성 |
| Send To Lattice | 현재 파일이나 선택 영역을 워크스페이스 워크플로로 전송 |
| 동기화 상태 | 에디터 연결, 인덱싱, 동기화 완료, 오프라인 상태 확인 |
| 모델 워크플로 | Lattice AI가 관리하는 모델 로드, 전환, 사용 |
| 지식 그래프 | 파일, 노트, 스크린샷, 대화 기반 그래프 컨텍스트 활용 |
| 멀티 에이전트 워크플로 | 플래너/실행기/리뷰어 방식 코딩 워크플로 시작 |
| 로컬 우선 워크스페이스 | 개인 작업은 로컬에 보관하면서 조직 워크스페이스 패턴도 지원 |
| 네이티브 v4 앱 | `/app`을 주 제품 셸로 사용 |

## 명령어

| 명령어 | 단축키 | 설명 |
| --- | --- | --- |
| Lattice AI: Open Chat | `Cmd+Shift+A` | 워크스페이스 채팅 패널 열기 |
| Lattice AI: Edit Selection | `Cmd+Shift+E` | 선택한 코드 다시 작성 |
| Lattice AI: Load Model | `Cmd+Shift+M` | 로컬 또는 클라우드 모델 선택 |
| Lattice AI: Explain Selection | 우클릭 메뉴 | 선택한 코드 설명 |
| Lattice AI: Refactor Selection | 명령 팔레트 | 선택한 코드 리팩터링 |
| Lattice AI: Generate Tests | 명령 팔레트 | 집중 테스트 코드 생성 |
| Lattice AI: Send To Lattice | 명령 팔레트 | 파일 컨텍스트를 Lattice AI에 기록 |
| Lattice AI: Ask About Current File | 명령 팔레트 | 현재 파일 컨텍스트로 질문 |
| Lattice AI: Show Sync Status | 명령 팔레트 / 상태 표시줄 | 에디터-앱 브리지 상태 확인 |
| Lattice AI: Generate Terminal Command | 우클릭 메뉴 | 셸 명령어 생성 |
| Lattice AI: Save to Knowledge Garden | 우클릭 메뉴 | 스니펫이나 노트 저장 |

## 모델 워크플로

Lattice AI는 로컬 및 클라우드 모델을 모두 지원합니다.

- Apple Silicon에서는 현세대 멀티모달 로컬 모델에 `MLX-VLM`을 사용하며, `MLX-LM`은 표준 Gemma 4 메타데이터용 텍스트 폴백으로만 유지합니다.
- LM Studio, vLLM, llama.cpp, Ollama 호환 로컬 경로를 지원합니다.
- OpenAI, OpenRouter, Groq, Together AI 및 OpenAI 호환 엔드포인트를 지원합니다.
- 모델 카드는 제작 국가, 제작 회사, 실행 방식, 인터넷 사용 여부, 모델명, HF 검증 상태, 다운로드/로드 전략, 하드웨어 적합도를 공개합니다.

## 설정

| 설정 키 | 기본값 | 설명 |
| --- | --- | --- |
| `ltcai.serverUrl` | `http://localhost:4825` | Lattice AI 워크스페이스 URL |
| `ltcai.authToken` | 비어 있음 | 선택적 Bearer 인증 토큰(`LATTICEAI_API_TOKEN` 환경 변수도 지원) |
| `ltcai.autoLoadModel` | `false` | 확장 활성화 시 기본 모델 자동 로드 |
| `ltcai.defaultModel` | 비어 있음 | 선택적 기본 모델 ID |

원격 또는 터널 서버 연결 예시:

```json
{
  "ltcai.serverUrl": "https://your-server.example"
}
```

## 링크

- [프로젝트 README](https://github.com/TaeSooPark-PTS/LatticeAI)
- [PyPI](https://pypi.org/project/ltcai/)
- [npm](https://www.npmjs.com/package/ltcai)
- [GitHub Releases](https://github.com/TaeSooPark-PTS/LatticeAI/releases)
- [변경 이력 (Changelog)](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/CHANGELOG.md)
- [보안 정책 (Security Policy)](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/SECURITY.md)

## 라이선스

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)
