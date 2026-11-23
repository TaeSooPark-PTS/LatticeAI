[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.5.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.5.0.md)

# Lattice AI v13.5.0 — Living Synthesis (2026-11-24)

**macOS**(`omlx` Homebrew 및 무-Homebrew universal2 휠 자동 폴백, `local_mlx`), **Windows**(`winget` 기반 Ollama / llama.cpp / LM Studio 및 `pip` `llama-cpp-python[server]` 폴백), **Linux**(공식 Ollama 설치 스크립트 및 `pip` `llama-cpp-python[server]` / `vllm`) 전 환경에서 **Qwen3.8 27B 4-bit** 모델을 원클릭(원웨이)으로 설치·다운로드·실행할 수 있도록 통합하고, 워크스페이스 타입체커를 **TypeScript 7.0.2 (`tsgo`)**로, `scripts/check_i18n_literals.mjs`를 `@babel/parser`로 전환했으며, 대형 서비스 모듈 분할과 데드코드·불필요 테스트 정리를 완료했습니다.

## 주요 변경 사항

- **크로스플랫폼 원웨이 엔진 설치 및 Qwen3.8 27B 4-bit 지원 (`latticeai/services/engine_adapters.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `rust/lattice-platform/src/modelops/`, `frontend/src/features/library/ModelsPanel.tsx`):**
  - Windows `winget`(`Ollama.Ollama`, `ggml.llamacpp`, `ElementLabs.LMStudio`) + `pip` `llama-cpp-python[server]` 폴백, Linux `curl` Ollama 설치 스크립트 + `pip` 폴백, macOS Apple Silicon `omlx`의 Homebrew 및 무-Homebrew GitHub Release universal2 휠 폴백(`~/.omlx/venv`)을 자동 선택하는 `resolve_engine_installer()`를 구현했습니다.
  - Windows 및 Linux 환경에서 `Qwen3.8-27B` 요청 시 `ollama:hf.co/ggml-org/Qwen3.8-27B-GGUF:Q4_K_M` 또는 `llamacpp:ggml-org/Qwen3.8-27B-GGUF`(`unsloth/Qwen3.8-27B-GGUF` 공개 미러 자동 폴백 및 `HF_XET_HIGH_PERFORMANCE=1` 고속 전송 기본 활성화)로 자동 라우팅하도록 개선했습니다 (`resolve_cross_platform_model_request`, `_recommended_with_engine_options`, Rust `probe.rs`, `scan.rs`, `probe_sysinfo.rs`).
  - macOS(`vm_stat`), Linux(`/proc/meminfo`), Windows(`GlobalMemoryStatusEx`) 실시간 가용 메모리 감지(`host_available_ram_gb`)를 추가해 32GB 환경에서 동시 실행 앱으로 여유 메모리가 부족할 때 `omlx_serve_args`가 자동으로 `--memory-guard safe`, `--hot-cache-max-size 1GB`, `--max-concurrent-requests 1`로 전환하며, `llamacpp_serve_args`에 GPU 오프로드 및 양자화 KV 캐시(`q8_0`) 옵션을 적용했습니다.
  - `status.install_engine`의 확인 토큰 자동 바인딩과 `ModelsPanel.tsx`의 `install_required` 원클릭 버튼을 연결해 사용자 동의 시 엔진 설치·모델 다운로드·서버 기동·모델 로드가 끊김 없이 한 번에 수행됩니다.
- **TypeScript 7 (`tsgo`) 및 `@babel/parser` AST 전환 (`package.json`, `scripts/check_i18n_literals.mjs`, `scripts/run_openapi_typescript.mjs`):**
  - 루트 `typescript`를 `^5.9.3`에서 `^7.0.2`로 업그레이드하고, `scripts/check_i18n_literals.mjs`의 TS 5 컴파일러 AST 의존성을 `@babel/parser`로 완전히 대체했으며, `openapi-typescript` 실행 시에만 `scripts/run_openapi_typescript.mjs`로 격리했습니다.
- **모듈 리팩토링 및 데드코드·불필요 테스트 삭제 (`latticeai/services/{engine_adapters.py,model_engines.py,model_loading.py,model_catalog.py}`, `latticeai/core/policy.py`, `frontend/src/{routes.ts,lib/folderPicker.ts}`, `rust/Cargo.toml`):**
  - `model_engines.py`에서 `engine_adapters.py`(`EngineAdapter` 레지스트리)를 분리하고, `model_loading.py`에서 동기/SSE 스트리밍 경로에 중복 작성되어 있던 7개 엔진 준비 분기를 단일 파이프라인으로 통합했습니다(`716 -> 492`줄).
  - 미사용 코드(`model_catalog.py`의 `recommend_models_for_pc`, `policy.py`의 `capabilities_for_role`, `routes.ts`의 `routeAliases`, `folderPicker.ts`의 `hasBrowserFolderPicker`, `rust/Cargo.toml`의 미사용 `walkdir`) 및 중복 테스트 파일(`tests/unit/test_model_loading_deps.py`)을 삭제했습니다.

## 배포 아티팩트

- `dist/ltcai-13.5.0-py3-none-any.whl`
- `dist/ltcai-13.5.0.tar.gz`
- `ltcai-13.5.0.tgz`
- `dist/ltcai-13.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.5.0_aarch64.dmg`
