from lattice_brain.workflow import validate_definition
from latticeai.services.brain_automation import (
    build_brain_automation_workflow,
    find_installed_recipe_workflow,
    list_brain_automation_recipes,
)


def test_brain_automation_recipes_are_consent_first():
    payload = list_brain_automation_recipes()

    assert payload["principles"]["local_first"] is True
    assert payload["principles"]["drafts_before_automation"] is True
    assert len(payload["recipes"]) >= 3
    for recipe in payload["recipes"]:
        assert recipe["consent"]["default_state"] == "draft_disabled"
        assert recipe["consent"]["external_actions"] is False
        assert recipe["consent"]["requires_user_enable"] is True


def test_recipe_workflows_install_as_disabled_valid_drafts():
    for recipe in list_brain_automation_recipes()["recipes"]:
        definition = build_brain_automation_workflow(recipe["id"])

        assert validate_definition(definition) == []
        assert definition["nodes"][0]["config"]["enabled"] is False
        assert definition["metadata"]["automation_state"] == "draft_disabled"
        assert definition["metadata"]["external_actions"] is False


def test_recipe_can_be_built_as_enabled_only_when_requested():
    definition = build_brain_automation_workflow("daily-memory-digest", enabled=True)

    assert definition["nodes"][0]["config"]["enabled"] is True
    assert definition["metadata"]["automation_state"] == "enabled"


def test_recipe_metadata_carries_recipe_id_for_draft_dedup():
    """Supports UI guard: already-installed recipe drafts are detected via metadata.recipe_id + created_from."""
    for recipe in list_brain_automation_recipes()["recipes"]:
        definition = build_brain_automation_workflow(recipe["id"])
        assert definition["metadata"]["recipe_id"] == recipe["id"]
        assert definition["metadata"]["created_from"] == "brain_automation_recipe"


def test_find_installed_recipe_workflow_matches_only_same_recipe_drafts():
    """Idempotent install: re-installing a recipe returns the existing draft."""
    existing = {"id": "wf-1", "metadata": build_brain_automation_workflow("daily-memory-digest")["metadata"]}
    manual = {"id": "wf-2", "metadata": {"created_from": "desktop-act-ui"}}
    other_recipe = {"id": "wf-3", "metadata": build_brain_automation_workflow("weekly-project-review")["metadata"]}
    workflows = [manual, other_recipe, existing]

    assert find_installed_recipe_workflow(workflows, "daily-memory-digest") is existing
    assert find_installed_recipe_workflow(workflows, "follow-up-radar") is None
    assert find_installed_recipe_workflow([], "daily-memory-digest") is None
    assert find_installed_recipe_workflow(None, "daily-memory-digest") is None
