from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.core.tool_registry import TOOL_GOVERNANCE

REPO = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO / "skills"
NEW_SKILLS = ("meeting_notes", "weekly_review")


def _skill_dirs():
    return [
        path
        for path in sorted(SKILLS_DIR.iterdir())
        if path.is_dir() and (path / "SKILL.md").exists()
    ]


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_ships_the_whole_package(name):
    skill = SKILLS_DIR / name
    for filename in ("SKILL.md", "schema.json", "risk.json", "examples.md"):
        assert (skill / filename).exists(), f"{name} is missing {filename}"


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_is_discoverable_with_a_description(name):
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    description = [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("description:")
    ]
    assert description and len(description[0]) > 10


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_action_names_a_registered_tool(name):
    schema = json.loads((SKILLS_DIR / name / "schema.json").read_text(encoding="utf-8"))
    assert schema["action"] in TOOL_GOVERNANCE, (
        f"{name} declares action {schema['action']!r}, which is not a registered tool — "
        "its evals would fail at run time"
    )


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_evals_are_runnable(name):
    schema = json.loads((SKILLS_DIR / name / "schema.json").read_text(encoding="utf-8"))
    evals = schema.get("evals") or []
    assert len(evals) >= 2, "ship at least one success and one refusal case"
    for case in evals:
        assert case.get("id")
        assert isinstance(case.get("input"), dict)
        assert case.get("pass_criteria")
    assert any("false" in str(case["pass_criteria"]) for case in evals)


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_risk_profile_is_internally_consistent(name):
    risk = json.loads((SKILLS_DIR / name / "risk.json").read_text(encoding="utf-8"))
    assert risk["destructive"] is False
    assert risk["shell"] is False
    assert risk["network"] is False
    assert risk["risk"] == "write"
    assert risk["rollback"] in {"git", "snapshot"}
    assert risk["sandbox"] == "workspace"


def test_every_shipped_skill_keeps_the_package_contract():
    for skill in _skill_dirs():
        for filename in ("schema.json", "risk.json"):
            assert (skill / filename).exists(), f"{skill.name} is missing {filename}"
        schema = json.loads((skill / "schema.json").read_text(encoding="utf-8"))
        assert schema.get("title") == skill.name
        assert schema.get("evals"), f"{skill.name} ships no evals"


def test_the_new_skills_are_actually_new():
    names = {skill.name for skill in _skill_dirs()}
    assert set(NEW_SKILLS) <= names
    assert {
        "code_review",
        "data_analysis",
        "file_edit",
        "summarize_document",
        "web_search",
    } <= names
