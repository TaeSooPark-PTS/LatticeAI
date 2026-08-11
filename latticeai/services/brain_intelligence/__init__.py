"""Proactive Brain Intelligence service (v9.3.0).

The Brain graduates from a passive store to an active steward of its own
knowledge. This service wires the previously dormant quality layer
(:mod:`lattice_brain.quality` — dedupe, merge, conflict and temporal
contradiction detection, retention) into router-facing capabilities:

* **health_report** — scored diagnosis of the Brain across freshness,
  connectivity, embedding coverage, and contradiction pressure, with
  recommended next actions. Every number is read from the live stores;
  a missing store degrades the dimension to ``unavailable``, never a guess.
* **insights** — a proactive digest: recent knowledge growth, most active
  types, stale knowledge, orphan (disconnected) nodes, and suggested
  questions grounded in real node titles.
* **contradictions** — surfaced conflicts across workspace memories
  (negation/preference conflicts, temporal contradictions) plus explicit
  CONTRADICTS edges already recorded in the graph.
* **consolidate** — duplicate-memory and duplicate-edge detection. Dry-run
  by default (consent-first, like every Brain automation); ``apply=True``
  prunes only exact duplicate workspace memories through the audited
  MemoryService path and never touches graph content.

Pure service: no FastAPI, no globals. Collaborators are injected.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``constants``
(windows + the two shared readings), ``proposals`` (the review-queue path and
the proactive brief), ``sampling`` (the shared graph slice and memory read),
``health`` (the scored diagnosis and vector freshness), ``digest`` (insights,
garden, graph-layer quality reads), ``consistency`` (contradiction and
consolidation scans), and ``service`` (the composed class). This module
re-exports every name the single file exposed, so
``latticeai.services.brain_intelligence.X`` keeps working.

Stubbing note: rebinding one of these *here* changes only this module's name.
The submodule that calls it holds its own reference, so a test standing in for
``LOGGER`` or ``_parse_ts`` patches the submodule that uses it.
"""

from __future__ import annotations

from lattice_brain.quality import GraphEdgeQualityManager as GraphEdgeQualityManager
from lattice_brain.quality import MemoryQualityManager as MemoryQualityManager

# noqa on the next line, not a redundant alias: the single file exposed this
# name *renamed* (``brain_intelligence._now``), and reproducing that surface
# exactly is the point — importing it as ``now_iso`` would add a name the
# module never had.
from latticeai.core.timeutil import now_iso as _now  # noqa: F401

from .constants import _GRAPH_SAMPLE_LIMIT as _GRAPH_SAMPLE_LIMIT
from .constants import _RECENT_DAYS as _RECENT_DAYS
from .constants import _STALE_DAYS as _STALE_DAYS
from .constants import LOGGER as LOGGER
from .constants import _no_graph_reason as _no_graph_reason
from .constants import _parse_ts as _parse_ts
from .service import BrainIntelligenceService as BrainIntelligenceService

__all__ = ["BrainIntelligenceService"]
