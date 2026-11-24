[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.7.2.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.7.2.md)

# Lattice AI v13.7.2 — Living Synthesis (2026-11-24)

Rust 워크스페이스 전체(`lattice-host`, `lattice-chat`, `lattice-platform`, `lattice-agent`, `lattice-retrieval`, `src-tauri` 및 전체 통합 테스트)의 임포트를 평탄한 크레이트 루트 재내보내기에서 정본 도메인 모듈 경로(`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`)로 전면 이전하고, `lattice-agent`와 `lattice-platform`의 구형 호환성 `pub use` 맵 및 마지막 미사용 `lattice_chat::crate_ready()` 스캐폴드 스텁을 제거했습니다.

## 주요 변경 사항

- **정본 도메인 모듈 경로 전면 이전 (`rust/lattice-agent`, `rust/lattice-platform`, `rust/lattice-host`, `rust/lattice-chat`, `rust/lattice-retrieval`):**
  - `rust/lattice-host/src/gateway/product.rs`를 포함한 77개 이상의 Rust 소스·통합 테스트 파일이 `lattice_platform::{adminops,governance,knowledge,modelops,shell,toolsurface,workspaceos}::*` 및 `lattice_agent::{content,kernel,parse,prompts,seam,surface,tools}::*` 정본 경로를 직접 임포트하도록 이전했습니다.
  - `rust/lattice-agent/src/lib.rs`와 `rust/lattice-platform/src/lib.rs`의 구형 평탄 호환성 `pub use` 맵을 완전히 삭제해 엄격한 도메인 범위의 공개 API 표면만 유지합니다.
  - `rust/lattice-chat/src/lib.rs`에서 마지막 미사용 스캐폴드 스텁 `lattice_chat::crate_ready()`를 제거했습니다.
- **크레이트 아키텍처 지도 및 문서 동기화 (`AGENTS.md`, `rust/lattice-agent/ARCHITECTURE.md`, `rust/lattice-platform/ARCHITECTURE.md`, `docs/ko/crates/`):**
  - `13.7.2`에서 평탄 호환성 맵이 제거되었음을 크레이트 아키텍처 문서와 `AGENTS.md`(EN/KO)에 반영했습니다.
  - 모든 정본 EN/KO 문서, 릴리스 노트, 증거물(`output/release/v13.7.2`), 정확 버전 산출물 매니페스트를 `13.7.2`로 동기화했습니다.

## 정확한 빌드 산출물

- `dist/ltcai-13.7.2-py3-none-any.whl`
- `dist/ltcai-13.7.2.tar.gz`
- `ltcai-13.7.2.tgz`
- `dist/ltcai-13.7.2.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.2_aarch64.dmg`
