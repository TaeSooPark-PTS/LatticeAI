[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.7.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.7.0.md)

# Lattice AI v13.7.0 — Living Synthesis (2026-11-24)

폐기된 Python 워커 라우트로 포워딩되던 Track 5 연동 경로를 단일 라이터 `GraphWriter` 기반 네이티브 Rust Obsidian 볼트(`[[wikilink]]` `LINKS_TO` 엣지) 및 Notion/Git/Mail 브릿지로 완전 전환하고, Vite 8 / Vitest 5 설정을 네이티브 ESM(`.mts`)으로 마이그레이션해 `configLoader: 'native'` 경고를 제거했으며, `v13.0.0` 미만 릴리스 노트를 일괄 정리하고 `ARCHITECTURE.md` 및 `docs/ko/ARCHITECTURE.md`를 `13.7.0` 현재 아키텍처 기준으로 최신화했습니다.

## 주요 변경 사항

- **네이티브 Rust Obsidian 및 Interop 브릿지 (`rust/lattice-ingest/src/local_files_api/interop.rs`, `rust/lattice-ingest/src/local_files_api/ingest.rs`, `rust/lattice-ingest/tests/local_files_replay.rs`):**
  - `GraphWriter` 및 `NoteIngestor` 기반 `ingest_obsidian_native()`를 구현해 `.md` 노트의 `#tag`와 `[[wikilink]]`/`[[target|alias]]`/`[[target#heading]]` 참조를 추출하고 `GraphWriter::upsert_edges`로 `LINKS_TO` 엣지를 생성하며 `dry_run` 미리보기를 지원합니다.
  - `notion`(디렉터리 및 `.zip` 내보내기, 32자리 hex UUID 접미사 제거), `git`(로컬 저장소 커밋 이력 및 변경 파일 통계), `mail`(로컬 `.eml` 및 `.ics` `VEVENT` 파싱)용 `ingest_interop_native()`를 구현해 폐기된 Python 워커 포워딩을 완전히 제거했습니다.
- **네이티브 ESM Vite 8 / Vitest 5 설정 전환 (`vite.config.mts`, `vitest.config.mts`, `tsconfig.json`):**
  - `vite.config.ts`와 `vitest.config.ts`를 `fileURLToPath` + `readFileSync` 기반 `.mts`로 전환해 CommonJS `__dirname`과 Vite 8 `configLoader: 'native'` 경고를 완전히 제거했습니다.
- **`v13.0.0+` 릴리스 노트 정리 및 `ARCHITECTURE.md` 최신화 (`ARCHITECTURE.md`, `docs/ko/ARCHITECTURE.md`, `RELEASE.md`, `RELEASE_NOTES.md`, `docs/CHANGELOG.md`, `SECURITY.md`, `scripts/check_current_release_docs.mjs`):**
  - `v13.0.0` 미만(`v12.x`) 릴리스 노트 파일 및 원장 항목을 EN/KO 문서에서 모두 정리하고, `scripts/check_current_release_docs.mjs`와 `tests/unit/test_server_app_decomposition.py`의 하한을 `v13.0.0+`로 갱신했습니다.
  - `ARCHITECTURE.md`와 `docs/ko/ARCHITECTURE.md`에서 과거 프리-v13 마이그레이션 이력을 걷어내고 `13.7.0` 기준 9개 Rust 크레이트, 20개 라우트 Python 워커, 네이티브 연동 브릿지, TS 7 / `.mts` 툴체인 중심으로 간결하게 최신화했습니다.

## 정확한 빌드 산출물

- `dist/ltcai-13.7.0-py3-none-any.whl`
- `dist/ltcai-13.7.0.tar.gz`
- `ltcai-13.7.0.tgz`
- `dist/ltcai-13.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.0_aarch64.dmg`
