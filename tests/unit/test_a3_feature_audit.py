"""v11.2.0 feature audit: claims in FEATURE_STATUS.md, checked against the app.

Two kinds of drift bit this release and both are pinned here.

1. **A doc naming an endpoint the app does not serve.** FEATURE_STATUS.md is
   the canonical feature table and it cites routes by path. Nothing checked
   that those paths existed, so a renamed or removed route would keep its
   sentence. The gate below reads the *checked-in* ``frontend/openapi.json``,
   which ``scripts/check_openapi_drift.mjs`` already proves is regenerated
   from the live application — so this asserts against the real route table
   without booting a second runtime inside a unit test.

2. **A consumer reading a shape its producer never writes.**
   ``CommandCenterService`` read the Brain's health out of a nested
   ``overall`` block; ``BrainIntelligenceService.health_report()`` publishes
   ``overall_score``/``grade`` at the top level. Every existing test used a
   fake that returned the nested shape, so the suite stayed green while
   Today's Briefing showed a permanent dash and the ``check-health`` quick
   action could never fire. These tests wire the two *real* services together,
   which is the only arrangement that can catch it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.services.brain_intelligence import BrainIntelligenceService
from latticeai.services.command_center import CommandCenterService

REPO_ROOT = Path(__file__).resolve().parents[2]

# Backticked spans in FEATURE_STATUS.md that look like an HTTP path but are
# not one. Each entry states why, so the exemption is a fact and not a mute.
_NOT_HTTP_PATHS = {
    "/review": "Telegram bot command, not an HTTP route",
    # 11.4.0 Rust Foundation: gateway routes served by the opt-in
    # lattice-host process (rust/lattice-host), never by the Python app —
    # frontend/openapi.json cannot and should not list them. Their behavior
    # is pinned by the cargo test suite (rust/lattice-host/tests/).
    "/host/": "lattice-host (Rust) gateway route, not a Python app route",
}

# Path parameters are named for the reader, not copied from the route table.
_PARAM = re.compile(r"\{[^}]+\}")


def _documented_paths() -> List[str]:
    """Every HTTP path FEATURE_STATUS.md names inside a code span."""

    doc = (REPO_ROOT / "FEATURE_STATUS.md").read_text(encoding="utf-8")
    out: List[str] = []
    for span in re.findall(r"`([^`]+)`", doc):
        match = re.match(
            r"^(?:GET|POST|PUT|PATCH|DELETE)?\s*(/[A-Za-z0-9_\-/{}]+)\*?$", span.strip()
        )
        if not match:
            continue
        path = match.group(1)
        if path in _NOT_HTTP_PATHS:
            continue
        out.append(path)
    return sorted(set(out))


def _served_paths() -> List[str]:
    schema = json.loads(
        (REPO_ROOT / "frontend/openapi.json").read_text(encoding="utf-8")
    )
    return sorted(schema["paths"])


def _normalize(path: str) -> str:
    """Compare route shapes, not the names their parameters were given."""

    return _PARAM.sub("{}", path).rstrip("/")


def test_every_endpoint_feature_status_names_is_actually_served():
    served = {_normalize(path) for path in _served_paths()}
    documented = _documented_paths()
    # The extractor has to find something, or this gate passes by finding
    # nothing — the failure mode a regex-driven doc check dies of.
    assert len(documented) >= 10

    missing = []
    for path in documented:
        shape = _normalize(path)
        if shape in served:
            continue
        # A doc may name the root of a route family (`/api/knowledge-graph/share*`).
        if any(candidate.startswith(shape + "/") for candidate in served):
            continue
        missing.append(path)
    assert missing == [], f"FEATURE_STATUS.md names unserved routes: {missing}"


def test_the_path_extractor_skips_the_spans_that_only_look_like_routes():
    doc = (REPO_ROOT / "FEATURE_STATUS.md").read_text(encoding="utf-8")
    for span, why in _NOT_HTTP_PATHS.items():
        assert f"`{span}`" in doc, f"stale exemption ({why}): {span}"
    assert not set(_documented_paths()) & set(_NOT_HTTP_PATHS)


# ── the briefing reports the health the Brain actually computed ───────────


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class _Graph:
    """The slice of the store the health report reads, with store key names."""

    def __init__(self, nodes, edges, coverage: float) -> None:
        self._nodes = nodes
        self._edges = edges
        self._coverage = coverage

    def graph(self, limit: int, **kwargs: Any) -> Dict[str, Any]:
        return {"nodes": self._nodes[:limit], "edges": self._edges}

    def index_status(self) -> Dict[str, Any]:
        return {
            "status": "ready",
            "scale": {
                "coverage_ratio": self._coverage,
                "ready_items": 4,
                "pending_items": 0,
            },
        }


def _briefing(nodes, edges, coverage: float) -> Dict[str, Any]:
    """A briefing whose health section came from the real health report."""

    graph = _Graph(nodes, edges, coverage)
    brain = BrainIntelligenceService(knowledge_graph=graph, enable_graph=True)
    service = CommandCenterService(
        knowledge_graph=graph, brain_intelligence=brain, enable_graph=True
    )
    return service.briefing()


def _healthy_graph():
    nodes = [
        {"id": "a", "type": "Document", "title": "A", "updated_at": _iso(1)},
        {"id": "b", "type": "Decision", "title": "B", "updated_at": _iso(2)},
    ]
    edges = [
        {
            "id": "e1",
            "from": "a",
            "to": "b",
            "type": "MENTIONS",
            "confidence": 0.9,
            "evidence": [],
        }
    ]
    return nodes, edges


def test_briefing_health_carries_the_score_the_brain_published():
    nodes, edges = _healthy_graph()
    graph = _Graph(nodes, edges, 1.0)
    brain = BrainIntelligenceService(knowledge_graph=graph, enable_graph=True)
    report = brain.health_report()

    health = _briefing(nodes, edges, 1.0)["sections"]["health"]

    assert health["available"] is True
    assert health["score"] == report["overall_score"]
    assert health["grade"] == report["grade"]


def test_a_struggling_brain_raises_the_check_health_quick_action():
    """The branch that was unreachable while the section read a dead key."""

    nodes = [
        {"id": "a", "type": "Document", "title": "A", "updated_at": _iso(400)},
        {"id": "b", "type": "Document", "title": "B", "updated_at": _iso(400)},
    ]
    briefing = _briefing(nodes, [], 0.4)

    assert briefing["sections"]["health"]["score"] < 70
    assert "check-health" in [action["id"] for action in briefing["quick_actions"]]


class _RefusingBrain:
    def health_report(self, **kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("graph locked")


class _ScorelessBrain:
    """What the real report returns when no dimension could be measured."""

    def health_report(self, **kwargs: Any) -> Dict[str, Any]:
        return {"overall_score": None, "grade": None, "recommended_actions": []}


def _sections(brain: Optional[Any]) -> Dict[str, Any]:
    return CommandCenterService(brain_intelligence=brain).briefing()["sections"]


def test_an_unmeasurable_brain_reports_unavailable_rather_than_a_zero():
    assert _sections(_ScorelessBrain())["health"] == {
        "available": False,
        "grade": None,
        "score": None,
        "recommended_actions": [],
    }
    assert _sections(_RefusingBrain())["health"] == {"available": False}
    assert _sections(None)["health"] == {"available": False}
