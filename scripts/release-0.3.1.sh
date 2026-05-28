#!/usr/bin/env bash
# Release 0.3.1 — finalize commit + push.
#
# 이 스크립트는 sandbox 권한 제약으로 Claude가 마지막 git 명령을 끝내지 못했기 때문에
# 사용자 본인 터미널에서 한 번 실행하기 위한 마무리 스크립트입니다.
#
# 사전 조건:
#   - 5개 피드백을 반영한 코드/문서/테스트/CI 변경이 워킹트리에 적용되어 있음
#   - 빌드 산출물(dist/ltcai-0.3.1*, ltcai-0.3.1.tgz)이 생성되어 있음 (gitignore라 commit에는 안 들어감)
#
# 사용법:
#   bash scripts/release-0.3.1.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# 1. stale lock 정리
if [ -f .git/index.lock ]; then
  rm -f .git/index.lock
fi

# 2. 변경된 파일 add
git add -A

# 3. 어떤 게 들어가는지 확인용 짧은 요약
echo "--- staged summary ---"
git diff --cached --stat | tail -40

# 4. commit
git commit -m "Release 0.3.1 — model loading reliability + auto graph + security console

External review (5 items) reflected:

  1. lattice_ai_model_recommend_download_load_issue.txt
     - latticeai/core/model_resolution.py: ModelResolution unifies
       input_id / engine / resolved_model / download_id / load_id /
       expected_current across all stages.
     - prepare_and_load_model() + /engines/prepare-model/stream now share
       the same ModelResolution; LM Studio instance_id is reconciled via
       resolution.update_after_load().

  2. lattice_ai_manual_model_select_auto_download_load_fix.txt
     - server.py runs _smoke_test_loaded_model() right after load and
       returns ready_to_chat / compatibility_status / smoke_test in the
       response. Cloud models are skipped to avoid user cost.
     - /models response now carries engine_options + compat_profiles.
     - chat.js trusts response.current (not the clicked model id);
       surfaces compatibility warnings; selectModelByCard() helper.

  3. lattice_ai_model_compat_fast_path.txt
     - latticeai/core/model_compat.py: family detection, family profiles
       (stop tokens, disable_draft, postprocess, generation params),
       fast_postprocess, validate_smoke_response, compat_cache. Fast /
       Slow / Recovery path structure.

  4. lattice_ai_auto_graph_direction.txt
     - latticeai/core/graph_curator.py: topic extraction → alias
       clustering (auto-merge) → promotion (with secret/dup/min-sources
       filters) → thread story edges → behavior-signal curation.

  5. lattice_ai_admin_security_dashboard_review.txt
     - latticeai/api/security_dashboard.py: 11 endpoints under
       /admin/security/* — overview / users / events / event detail /
       conversation summary+raw / files / file detail+content / raw /
       export. Hard-secret redaction enforced on every response;
       admin_view_sensitive_raw audit event recorded for raw access.
     - admin.html + admin.js: AI Security & Audit Command Center panel
       with Security Overview cards, User Risk stacked bar, sensitive-
       type donut, drill-down, raw explorer, JSON/CSV/XLSX/PDF export.

Tests / CI:
  - 28 new unit tests under tests/unit/test_{model_compat,
    model_resolution,graph_curator,security_dashboard}.py — all passing.
  - .github/workflows/ci.yml syntax-check extended for new modules.
  - .github/workflows/release.yml added — tag push v* triggers PyPI /
    npm / VS Code Marketplace / Open VSX publish. Secrets:
    PYPI_TOKEN, NPM_TOKEN, VSCE_PAT, OVSX_TOKEN (empty ones auto-skip).

Version bumps:
  - package.json 0.3.0 → 0.3.1
  - pyproject.toml 0.3.0 → 0.3.1
  - vscode-extension/package.json 0.3.0 → 0.3.1

Docs:
  - docs/CHANGELOG.md: 0.3.1 section
  - README.md: 'What's new in 0.3.1'
  - RELEASE.md: auto-publish via tag flow"

# 5. push to origin/main
git push origin main

# 6. annotated release tag (CI release.yml triggers on v* tags)
git tag -a v0.3.1 -m "Lattice AI 0.3.1 — model loading reliability + auto graph + security console"
git push origin v0.3.1

echo ""
echo "✅ Release 0.3.1 pushed."
echo ""
echo "다음 단계:"
echo "  1. GitHub → Repository Settings → Secrets and variables → Actions에"
echo "     PYPI_TOKEN / NPM_TOKEN / VSCE_PAT / OVSX_TOKEN 을 등록."
echo "  2. 등록된 secret이 있는 채널은 .github/workflows/release.yml의 v0.3.1"
echo "     trigger로 자동 publish 됩니다. 미등록 채널은 자동 skip."
echo "  3. 수동 publish가 필요하면 RELEASE.md 참고."
