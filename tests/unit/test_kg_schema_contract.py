"""v3.6.0 Knowledge Graph First — entity/relationship schema additions.

The v3.6.0 release formalizes six first-class entities (Source, Repository,
Meeting, Organization, Workflow, Agent) and eight relationships (indexed_from,
modified_by, belongs_to_project, part_of, discussed_in, decided_by,
generated_by, used_by_agent). Additions must be lossless through ``from_legacy``
and must NOT change the fallback behavior for unknown types.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.schema import EdgeType, NodeType


NEW_NODE_TYPES = ["SOURCE", "REPOSITORY", "MEETING", "ORGANIZATION", "WORKFLOW", "AGENT"]
NEW_EDGE_TYPES = [
    "INDEXED_FROM", "MODIFIED_BY", "BELONGS_TO_PROJECT", "PART_OF",
    "DISCUSSED_IN", "DECIDED_BY", "GENERATED_BY", "USED_BY_AGENT",
]


def test_new_node_types_present_with_canonical_values():
    for name in NEW_NODE_TYPES:
        assert hasattr(NodeType, name), f"NodeType.{name} missing"
        assert getattr(NodeType, name).value == name


def test_new_edge_types_present_with_canonical_values():
    for name in NEW_EDGE_TYPES:
        assert hasattr(EdgeType, name), f"EdgeType.{name} missing"
        assert getattr(EdgeType, name).value == name


def test_node_from_legacy_normalizes_new_aliases_losslessly():
    cases = {
        "Source": NodeType.SOURCE,
        "source": NodeType.SOURCE,
        "repo": NodeType.REPOSITORY,
        "Repository": NodeType.REPOSITORY,
        "git_repo": NodeType.CONCEPT,  # not aliased -> documented fallback
        "gitrepo": NodeType.REPOSITORY,
        "Meeting": NodeType.MEETING,
        "organization": NodeType.ORGANIZATION,
        "company": NodeType.ORGANIZATION,
        "workflow": NodeType.WORKFLOW,
        "Agent": NodeType.AGENT,
    }
    for label, expected in cases.items():
        assert NodeType.from_legacy(label) == expected, label


def test_edge_from_legacy_normalizes_new_aliases_and_korean_verbs():
    cases = {
        "indexed_from": EdgeType.INDEXED_FROM,
        "색인됨": EdgeType.INDEXED_FROM,
        "modified_by": EdgeType.MODIFIED_BY,
        "수정함": EdgeType.MODIFIED_BY,
        "belongs_to_project": EdgeType.BELONGS_TO_PROJECT,
        "belongs_to": EdgeType.BELONGS_TO_PROJECT,
        "part_of": EdgeType.PART_OF,
        "discussed_in": EdgeType.DISCUSSED_IN,
        "decided_by": EdgeType.DECIDED_BY,
        "generated_by": EdgeType.GENERATED_BY,
        "used_by_agent": EdgeType.USED_BY_AGENT,
    }
    for label, expected in cases.items():
        assert EdgeType.from_legacy(label) == expected, label


def test_unknown_types_still_fall_back():
    # Backward-compat guarantee: unknown node -> CONCEPT, unknown edge -> MENTIONS.
    assert NodeType.from_legacy("totally-unknown-xyz") == NodeType.CONCEPT
    assert EdgeType.from_legacy("totally-unknown-verb") == EdgeType.MENTIONS


def test_graph_visible_types_include_new_entities():
    from lattice_brain.graph.store import KnowledgeGraphStore

    visible = KnowledgeGraphStore._GRAPH_VISIBLE_TYPES
    for camel in ["Source", "Repository", "Meeting", "Organization", "Workflow", "Agent"]:
        assert camel in visible, f"{camel} not graph-visible"
