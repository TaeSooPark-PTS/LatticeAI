from fastapi import FastAPI
from fastapi.testclient import TestClient

from lattice_brain.workflow import validate_definition
from latticeai.api.workflow_designer import create_workflow_designer_router
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
        assert definition["nodes"][0]["config"]["review_queue"] is True
        assert definition["metadata"]["automation_state"] == "draft_disabled"
        assert definition["metadata"]["external_actions"] is False
        assert definition["nodes"][1]["config"]["goal"]
        assert definition["nodes"][1]["config"]["goal"] == definition["nodes"][1]["config"]["prompt"]
        assert definition["nodes"][1]["config"]["roles"][0] == "researcher"


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


def test_existing_recipe_draft_can_be_explicitly_enabled_without_duplication():
    class Store:
        def __init__(self):
            self.workflows = []

        def list_workflows(self, **_kwargs):
            return {"workflows": self.workflows}

        def create_workflow(self, **kwargs):
            workflow = {
                "id": "wf-recipe",
                "name": kwargs["name"],
                "nodes": kwargs["nodes"],
                "metadata": kwargs["metadata"],
                "workspace_id": kwargs.get("workspace_id"),
            }
            self.workflows.append(workflow)
            return workflow

        def update_workflow_definition(self, workflow_id, **kwargs):
            workflow = next(item for item in self.workflows if item["id"] == workflow_id)
            for key in ("name", "nodes", "metadata"):
                if kwargs.get(key) is not None:
                    workflow[key] = kwargs[key]
            return workflow

    store = Store()
    app = FastAPI()
    app.include_router(create_workflow_designer_router(
        store=store,
        require_user=lambda _request: "user@example.com",
        get_current_user=lambda _request: "user@example.com",
        gate_read=lambda _request: "personal",
        gate_write=lambda _request: "personal",
        workspace_graph=lambda: None,
        build_runners=lambda _user, _scope: {},
        append_audit_event=lambda *_args, **_kwargs: None,
    ))
    client = TestClient(app)

    draft = client.post("/workflows/api/automation/recipes/follow-up-radar", json={"enabled": False})
    assert draft.status_code == 200
    assert draft.json()["enabled"] is False
    assert len(store.workflows) == 1

    store.workflows[0]["name"] = "My reviewed follow-up radar"
    store.workflows[0]["nodes"][1]["config"]["goal"] = "Use my reviewed prompt"
    store.workflows[0]["nodes"][1]["config"]["roles"] = ["researcher", "reviewer"]

    enabled = client.post("/workflows/api/automation/recipes/follow-up-radar", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert enabled.json()["already_installed"] is True
    assert len(store.workflows) == 1
    assert store.workflows[0]["metadata"]["automation_state"] == "enabled"
    assert store.workflows[0]["nodes"][0]["config"]["enabled"] is True
    assert store.workflows[0]["name"] == "My reviewed follow-up radar"
    assert store.workflows[0]["nodes"][1]["config"]["goal"] == "Use my reviewed prompt"
    assert store.workflows[0]["nodes"][1]["config"]["roles"] == ["researcher", "reviewer"]
