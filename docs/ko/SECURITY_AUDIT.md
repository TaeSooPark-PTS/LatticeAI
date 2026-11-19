[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../SECURITY_AUDIT.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./SECURITY_AUDIT.md)

# 보안 감사 — 정적 스캔 및 공급망 스냅샷 (Security Audit)

> 상태: 감사 스냅샷 (2026-11-16)

이 문서는 현재 Rust 호스트 + Python AI 워커 트리(`latticeai/`)에 대해 수행한 특정 시점의 정적 분석 및 의존성 감사 스냅샷입니다. 실행된 스캐너, 관찰된 원시 결과, 이전 `v9.8.0` 스냅샷 이후 완료된 보안 조치, 그리고 남아 있는 후속 과제를 정직하게 기록합니다. 이는 취약점이 전혀 없음을 보장하는 증서가 아니라 명시된 날짜에 해당 도구들이 확인한 증거 기록입니다.

관련 문서: [CI 및 릴리스 게이트](./CI_AND_RELEASE_GATES.md)에서 이러한 스캔들이 지속적 통합(`.github/workflows/dependency-audit.yml` 및 `.github/workflows/ci.yml`)에 어떻게 연결되어 있는지 확인할 수 있습니다.

## 실행 도구 및 명령어

스캐너는 프로젝트 가상환경(`.venv`)과 루트 npm 워크스페이스에서 실행되었습니다.

| 도구 | 버전 | 실행 명령어 |
|------|---------|---------|
| `pip-audit` | 2.10.1 | `.venv/bin/pip-audit -r requirements.txt --strict` |
| `bandit` | 1.9.4 | `.venv/bin/bandit -q -r latticeai -ll -f json` |
| `npm audit` | npm | `npm audit --audit-level=high` |

## 의존성 취약점 (`pip-audit` / `npm audit`)

- `pip-audit -r requirements.txt --strict` → `No known vulnerabilities found` (종료 코드 0).
- `npm audit --audit-level=high` → `found 0 vulnerabilities` (종료 코드 0).

보안 오버라이드가 필요한 의존성 핀은 발견되지 않았습니다.

## 정적 코드 분석 (`bandit`)

`latticeai/` 전체의 **16,745 LOC**를 스캔했습니다(`lattice_brain/`은 `v11.6.0`에서 Rust 크레이트로 이전됨). `-ll` 필터(MEDIUM 심각도 이상 표시) 기준 bandit은 **총 11건**(**HIGH 0건**, **MEDIUM 11건**)을 보고했으며, 이는 `v9.8.0` 모놀리스 스냅샷 당시 54,795 LOC에서 80건이 보고되었던 것에서 대폭 감소한 수치입니다.

| 건수 | 심각도 | 규칙 | 탐지 내용 |
|------:|----------|------|---------------|
| 4 | MEDIUM | `B615` `huggingface_unsafe_download` | 리터럴 `revision=` 키워드 인자 없이 호출된 `from_pretrained()` |
| 4 | MEDIUM | `B604` `any_other_function_with_shell_equals_true` | `ToolPolicy(..., shell=True)` 데이터클래스 선언 |
| 2 | MEDIUM | `B310` `blacklist` (`urllib.request.urlopen`) | 명시적 URL 스킴 검사 없는 `urlopen` 호출 |
| 1 | MEDIUM | `B608` `hardcoded_sql_expressions` | SQL `IN (%s)` 플레이스홀더 포맷팅 |

### 항목별 실제 위험도 평가

| 규칙 | 위치 | 판정 | 근거 |
|------|-------------|---------|-----------|
| **`B615`** HF 다운로드 | `latticeai/core/embedding_providers/text.py:471,472,474,475` (및 `latticeai/services/model_runtime/download.py:294,303`의 래퍼 `_call_hf_hub_download`) | **실제 공급망 관련 항목** | `AutoTokenizer.from_pretrained` / `AutoModel.from_pretrained`(및 `_call_hf_hub_download`)가 현재 고정된 커밋 `revision=`을 전달하지 않습니다. 로컬 스냅샷 로드는 캐시 존재 시 `local_files_only=True`를 사용하고 `EmbeddingIndexContract`가 `model_revision`을 추적하지만, 다운로드 시점의 명시적 Hub 커밋/해시 고정은 추적 중인 강화 과제로 남아 있습니다. |
| **`B604`** `shell=True` | `latticeai/core/tool_registry.py:129,136,161,168` | **오탐 (False positive)** | 실제 `subprocess(..., shell=True)` 호출이 아니라 해당 도구 정책이 셸 기능을 나타내는지 명시하는 선언적 메타데이터(`ToolPolicy(..., shell=True)`)입니다. |
| **`B310`** `urlopen` | `latticeai/services/model_engines.py:123,240` | **낮은 실제 위험 — 추가 강화 대상** | 운영자가 구성한 로컬 추론 엔진(LM Studio / Ollama)을 대상으로 합니다. `urlopen` 호출 전에 `http`/`https` URL 스킴을 검증하면 `file:`이나 커스텀 스킴 엣지 케이스를 완전히 닫을 수 있습니다. |
| **`B608`** SQL `IN (%s)` | `latticeai/core/vector_index/sidecar.py:109` | **낮은 실제 위험 (안전한 파라미터 바인딩 쿼리)** | `len(chunk)` 길이에 맞춰 `"?"` 플레이스홀더(`IN (?,?,...)`)만 조립하며 실제 값인 `(model_id, int(dim), *chunk)`는 바인딩된 SQLite 파라미터로 전달하므로 사용자 데이터가 SQL 문자열에 직접 보간되지 않습니다. |

## 완료된 보안 및 공급망 개선 조치 (v9.8.0 스냅샷 이후)

1. **레거시 Python 표면 퇴역**: Rust 호스트/워커 분리와 함께 `lattice_brain/` 및 `latticeai/integrations/telegram_bot.py`(`B306` `tempfile.mktemp()`)가 제거되어 정적 분석 탐지 건수가 80건(HIGH 1건)에서 11건(HIGH 0건)으로 줄어들었습니다.
2. **`.latticebrain` 아카이브 암호화 및 경로 안전성**: Rust(`rust/lattice-platform/src/knowledge/portability/{archive, crypto}.rs`)에서 구현 완료 — RFC 7914 `scrypt` 키 파생, X25519 + HKDF-SHA256 수신자 봉인, 상수 시간 MAC 검증(`ct_eq`)을 포함한 encrypt-then-MAC(`SHA256-CTR` + `HMAC-SHA256`), ZIP 경로 순회 공격 차단(`validate_zip_entries`), 드라이런 및 검증된 복원/가져오기 게이트.
3. **사용자별 `AGENT_ROOT` 격리**: 서버 측 워크스페이스 경로 파생(`IsolatedRootSpec`, `resolve_isolated_agent_root`, `LoopRoutesState::isolated`)을 통해 `(user_id, tenant_id, workspace_id)` 단위로 에이전트 실행 영역을 격리하고 인가되지 않은 공유 워크스페이스 접근을 차단(fail-close)합니다.
4. **저장소 위생 및 토큰 정리**: `scratchpad/` 아래에서 추적되던 77개의 검증용 파일(`n7_token.txt` 및 `verify*` 덤프 포함)을 git 추적과 디스크에서 모두 제거하고 `.gitignore`에 `/scratchpad/`, `*.db-wal`, `*.db-shm`을 추가했습니다.
5. **CI/CD 공급망 및 릴리스 에셋 게이트**: 모든 GitHub Actions `uses:` 항목을 불변 커밋 SHA로 100% 고정하고, 검색 사이드카/계약 테스트와 `cargo test -p lattice-retrieval --tests`를 `.github/workflows/ci.yml`의 PR 차단 게이트로 승격했으며, `.github/workflows/release.yml` 및 `publish.yml`에 Apple Silicon DMG 빌드/서명/검증과 SHA-256 릴리스 에셋 업로드를 추가했습니다.

## 남아 있는 보안 강화 백로그

1. `latticeai/services/model_runtime/download.py`(`_call_hf_hub_download`)와 `latticeai/core/embedding_providers/text.py`(`from_pretrained`)에 명시적 커밋 `revision=`(및 게시된 경우 SHA-256 체크섬 검증)을 연결하여 4건의 `B615` 탐지를 해소합니다.
2. `latticeai/services/model_engines.py:123,240`(`B310`)에서 `urllib.request.urlopen` 호출 전 `http`/`https` 스킴 검증을 강제합니다.
3. 과거 `scratchpad/`에 저장되었던 파일 중 로컬 테스트 검증 외부에서 유효했던 레거시 외부 토큰이 있다면 교체(rotate)합니다.

## 한계점 (정직한 공개)

- 이 스냅샷은 정적 분석 및 보안 권고 데이터베이스 점검(`bandit`, `pip-audit`, `npm audit`)만 다루며, DAST나 Rust `cargo-audit` 실행 결과는 포함하지 않습니다.
- `pip-audit`와 `npm audit` 결과는 2026-11-16 기준 업스트림 권고 데이터베이스를 반영하며, `.github/workflows/dependency-audit.yml`을 통해 매주 정기 점검이 실행됩니다.
- CycloneDX SBOM(`sbom-python.json`, `sbom-npm.json`)은 로컬에서 `.venv/bin/python scripts/generate_sbom.py`를 실행하여 언제든 재생성할 수 있습니다.
