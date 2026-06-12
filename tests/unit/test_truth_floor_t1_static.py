"""T1 truth-floor regression guards for items 6-7 (docs/V4_IMPLEMENTATION_PLAN.md).

Item 6: the hybrid-search view must not render fabricated fusion meters. v4.1.0
replaces the v3 handwritten frontend, so the guard now checks the React source
and built Vite bundle for the retired hardcoded-meter patterns.

Item 7: README must not overclaim LLM-driven agent execution while the default
multi-agent runner is deterministic and LLM-free (FEATURE_STATUS.md).
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"

VIEW_SOURCE = REPO_ROOT / "frontend" / "src" / "pages" / "Brain.tsx"

# A meter fed by a literal (e.g. ``c.meter(0.85, ...)`` or a literal-only
# ternary) is fabricated data. Real usage passes a computed variable.
_LITERAL_METER = re.compile(r"\bmeter\(\s*(?:\d|[\"'])")
# The illustrative ternary that used to fake the intro meters.
_ILLUSTRATIVE_TERNARY = re.compile(r"0\.85\s*:\s*s\.key")


def _shipped_hybrid_search_bundle() -> Path:
    manifest = json.loads(
        (STATIC_DIR / "app" / "asset-manifest.json").read_text(encoding="utf-8")
    )
    public_url = next(v for k, v in manifest["assets"].items() if k.endswith("index.html"))
    return REPO_ROOT / public_url.removeprefix("/")


def test_hybrid_search_view_has_no_hardcoded_meter_values():
    source = VIEW_SOURCE.read_text(encoding="utf-8")
    assert not _LITERAL_METER.search(source)
    assert not _ILLUSTRATIVE_TERNARY.search(source)
    assert "latticeApi.hybridSearch" in source


def test_hybrid_search_fix_reached_the_shipped_bundle():
    bundle = _shipped_hybrid_search_bundle()
    assert bundle.exists(), f"manifest points at missing bundle: {bundle}"
    shipped = bundle.read_text(encoding="utf-8")
    assert not _LITERAL_METER.search(shipped)
    assert not _ILLUSTRATIVE_TERNARY.search(shipped)


def test_readme_does_not_overclaim_llm_driven_agents():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    overclaims = [
        # Implied autonomous automation while the runner never calls a model.
        "Automate with agents you can inspect",
        "Create repeatable agent workflows for research, coding, analysis",
        "Agents turn a goal into an inspectable run",
    ]
    for phrase in overclaims:
        assert phrase not in readme, f"README reintroduced overclaim: {phrase!r}"
    # The honest framing must state the runner's deterministic, model-free state.
    assert "deterministic" in readme
    assert "does not call a model" in readme or "LLM-free" in readme
