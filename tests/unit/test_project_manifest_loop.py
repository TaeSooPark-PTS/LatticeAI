"""Artifact Loop — multi-file project manifest, bundle validation, zip safety.

"todo 앱 html+css+js로 만들어줘" must yield a real linked project bundle
(index.html + style.css + app.js), each file passing the same model-agnostic
validation pipeline as single files, plus a bundle-level reference check and
a traversal-safe zip download. Single-file requests stay on the unchanged
single-file path.
"""

import asyncio
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from latticeai.api.chat_intents import ChatIntentController
from latticeai.core.file_generation import (
    infer_project_manifest,
    repair_bundle_references,
    validate_project_bundle,
)
from latticeai.tools import ToolError
from latticeai.tools.filesystem import zip_workspace_dir


BUNDLE_HTML = (
    "<!DOCTYPE html>\n<html>\n<head><meta charset=\"utf-8\"><title>Todo</title>"
    "<link rel=\"stylesheet\" href=\"style.css\"></head>\n"
    "<body><h1>Todo</h1><script src=\"app.js\"></script></body>\n</html>"
)
BUNDLE_CSS = "body { font-family: sans-serif; }\n"
BUNDLE_JS = "document.addEventListener('DOMContentLoaded', () => {});\n"


# ── manifest inference ──────────────────────────────────────────────────

def test_korean_html_css_js_request_yields_three_file_manifest():
    manifest = infer_project_manifest("todo 앱 html+css+js로 만들어줘")
    assert manifest is not None
    assert manifest["name"] == "todo-app"
    paths = [f["path"] for f in manifest["files"]]
    assert paths == ["index.html", "style.css", "app.js"]
    # briefs must pin the exact sibling filenames for weak models
    assert "style.css" in manifest["files"][0]["brief"]
    assert "app.js" in manifest["files"][0]["brief"]


def test_html_plus_css_only_yields_two_file_manifest():
    manifest = infer_project_manifest("make a landing page with html and css")
    assert manifest is not None
    paths = [f["path"] for f in manifest["files"]]
    assert paths == ["index.html", "style.css"]


def test_single_type_requests_do_not_become_projects():
    assert infer_project_manifest("html 파일 만들어줘") is None
    assert infer_project_manifest("css 파일 만들어줘") is None
    assert infer_project_manifest("간단한 웹페이지 만들어줘") is None


def test_explicit_filename_keeps_single_file_flow():
    assert infer_project_manifest("todo.html 만들어줘 css랑 js도 넣어서") is None


def test_manifest_requires_creation_verb():
    assert infer_project_manifest("html css js 차이가 뭐야?") is None
    assert infer_project_manifest("") is None


# ── bundle validation + reference repair ────────────────────────────────

def test_bundle_with_resolvable_references_validates():
    verdict = validate_project_bundle({
        "index.html": BUNDLE_HTML,
        "style.css": BUNDLE_CSS,
        "app.js": BUNDLE_JS,
    })
    assert verdict["ok"], verdict["issues"]
    assert all(entry["valid"] for entry in verdict["files"].values())


def test_bundle_flags_missing_referenced_file():
    verdict = validate_project_bundle({
        "index.html": BUNDLE_HTML,
        "style.css": BUNDLE_CSS,
        # app.js missing — the html reference must be reported
    })
    assert not verdict["ok"]
    assert any("app.js" in issue for issue in verdict["issues"])


def test_bundle_flags_invalid_member_file():
    verdict = validate_project_bundle({
        "index.html": "<h1>fragment, not a document</h1>",
    })
    assert not verdict["ok"]


def test_external_references_are_not_bundle_issues():
    html = BUNDLE_HTML.replace(
        "<link rel=\"stylesheet\" href=\"style.css\">",
        "<link rel=\"stylesheet\" href=\"https://cdn.example.com/x.css\">",
    )
    verdict = validate_project_bundle({"index.html": html, "app.js": BUNDLE_JS})
    assert verdict["ok"], verdict["issues"]


def test_repair_rewrites_dangling_reference_to_unique_same_ext_file():
    html = BUNDLE_HTML.replace("style.css", "styles.css")  # model typo
    files = {"index.html": html, "style.css": BUNDLE_CSS, "app.js": BUNDLE_JS}
    repaired, fixes = repair_bundle_references(files)
    assert fixes and "styles.css" in fixes[0]
    assert "styles.css" not in repaired["index.html"]
    assert "style.css" in repaired["index.html"]
    assert validate_project_bundle(repaired)["ok"]


def test_repair_leaves_resolvable_bundles_untouched():
    files = {"index.html": BUNDLE_HTML, "style.css": BUNDLE_CSS, "app.js": BUNDLE_JS}
    repaired, fixes = repair_bundle_references(files)
    assert fixes == []
    assert repaired == files


# ── zip download safety ─────────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import latticeai.tools as tools

    monkeypatch.setattr(tools, "AGENT_ROOT", tmp_path)
    return tmp_path


def test_zip_contains_the_project_files(workspace):
    project = workspace / "todo-app"
    project.mkdir()
    (project / "index.html").write_text(BUNDLE_HTML, encoding="utf-8")
    (project / "style.css").write_text(BUNDLE_CSS, encoding="utf-8")
    payload, filename = zip_workspace_dir("todo-app")
    assert filename == "todo-app.zip"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert sorted(archive.namelist()) == ["todo-app/index.html", "todo-app/style.css"]


def test_zip_rejects_path_traversal(workspace, tmp_path):
    outside = tmp_path.parent / "outside-secret"
    outside.mkdir(exist_ok=True)
    with pytest.raises(ToolError):
        zip_workspace_dir("../outside-secret")
    with pytest.raises(ToolError):
        zip_workspace_dir("/etc")


def test_zip_rejects_workspace_root_and_missing_dirs(workspace):
    with pytest.raises(ToolError):
        zip_workspace_dir("")
    with pytest.raises(ToolError):
        zip_workspace_dir("no-such-dir")


def test_zip_skips_symlinks_that_escape_the_project(workspace, tmp_path):
    secret = tmp_path.parent / "secret-file.txt"
    secret.write_text("secret", encoding="utf-8")
    project = workspace / "todo-app"
    project.mkdir()
    (project / "index.html").write_text(BUNDLE_HTML, encoding="utf-8")
    (project / "leak.txt").symlink_to(secret)
    payload, _ = zip_workspace_dir("todo-app")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["todo-app/index.html"]


# ── controller loop: manifest → generate → validate → bundle write ──────

class _BundleRouter:
    """Fake LLM that answers per-file generation prompts deterministically."""

    current_model_id = "local-test"

    async def generate_as(self, model_id, *, message, context, max_tokens, temperature):
        if "index.html" in context.splitlines()[0]:
            # deliberately misspell the stylesheet ref to exercise bundle repair
            return BUNDLE_HTML.replace("style.css", "styles.css")
        if "style.css" in context.splitlines()[0]:
            return f"```css\n{BUNDLE_CSS}```"
        return BUNDLE_JS


def _controller(tmp_path) -> ChatIntentController:
    from latticeai.tools import filesystem

    def execute_tool(name, args):
        assert name == "write_file"
        return filesystem.write_file(args["path"], args["content"])

    return ChatIntentController(
        model_router=_BundleRouter(),
        config=SimpleNamespace(is_public=False),
        public_model="",
        chat_service=None,
        notify=lambda *a, **k: None,
        clear_history=lambda *a, **k: {},
        clear_conversation=lambda *a, **k: {},
        history_scope_for_user=lambda *a, **k: {},
        append_audit_event=lambda *a, **k: None,
        enable_graph=False,
        knowledge_graph=None,
        enforce_tool_policy=lambda *a, **k: None,
        network_status=lambda: {},
        tool_error=ToolError,
        execute_tool=execute_tool,
        agent_controller=None,
        agent_root=tmp_path,
        ingestion_pipeline=None,
    )


def test_direct_project_action_writes_validated_bundle(workspace):
    controller = _controller(workspace)
    req = SimpleNamespace(
        message="todo 앱 html+css+js로 만들어줘",
        stream=False,
        max_tokens=0,
        temperature=0.2,
        source="web",
        conversation_id=None,
    )
    response = asyncio.run(controller.direct_file_action(req, model_id="local-test"))
    payload = json.loads(response.body)

    assert payload["status"] == "ok"
    assert payload["action_route"] == "direct_project_bundle"
    assert payload["final_state"] == "DONE"

    # all three files exist on disk under one project dir
    project_dir = payload["project"]["dir"]
    assert project_dir == "todo-app"
    for name in ("index.html", "style.css", "app.js"):
        assert (workspace / project_dir / name).exists()

    # the artifacts[] contract carries every bundle member
    filenames = sorted(a["filename"] for a in payload["artifacts"])
    assert filenames == ["app.js", "index.html", "style.css"]
    assert all(a["kind"] == "file" and a["valid"] for a in payload["artifacts"])

    # bundle-level validation ran and the dangling styles.css ref was repaired
    assert payload["project"]["bundle_validation"]["ok"]
    assert payload["project"]["reference_fixes"]
    written_html = (workspace / project_dir / "index.html").read_text(encoding="utf-8")
    assert "style.css" in written_html and "styles.css" not in written_html
    # the fenced css reply was extracted before writing
    written_css = (workspace / project_dir / "style.css").read_text(encoding="utf-8")
    assert "```" not in written_css

    # zip download URL points at the project directory
    assert payload["project"]["zip_url"] == "/tools/download_zip?path=todo-app"


def test_direct_project_action_dedupes_existing_project_dir(workspace):
    (workspace / "todo-app").mkdir()
    controller = _controller(workspace)
    req = SimpleNamespace(
        message="todo 앱 html+css+js로 만들어줘",
        stream=False,
        max_tokens=0,
        temperature=0.2,
        source="web",
        conversation_id=None,
    )
    response = asyncio.run(controller.direct_file_action(req, model_id="local-test"))
    payload = json.loads(response.body)
    assert payload["project"]["dir"] == "todo-app_2"
    assert (workspace / "todo-app_2" / "index.html").exists()


def test_single_file_requests_keep_the_single_file_route(workspace):
    controller = _controller(workspace)
    req = SimpleNamespace(
        message="html 파일 만들어줘",
        stream=False,
        max_tokens=0,
        temperature=0.2,
        source="web",
        conversation_id=None,
    )

    async def single_html(model_id, *, message, context, max_tokens, temperature):
        return BUNDLE_HTML.replace("style.css", "x.css").replace("app.js", "x.js")

    controller.router = SimpleNamespace(current_model_id="local-test", generate_as=single_html)
    response = asyncio.run(controller.direct_file_action(req, model_id="local-test"))
    payload = json.loads(response.body)
    assert payload["action_route"] == "direct_write_file"
    assert "project" not in payload
    assert len(payload["artifacts"]) == 1


def test_project_without_model_returns_no_model_response(workspace):
    controller = _controller(workspace)
    req = SimpleNamespace(
        message="todo 앱 html+css+js로 만들어줘",
        stream=False,
        max_tokens=0,
        temperature=0.2,
        source="web",
        conversation_id=None,
    )
    response = asyncio.run(controller.direct_file_action(req, model_id=None))
    payload = json.loads(response.body)
    assert response.status_code == 400
    assert payload["error"] == "no_model_loaded"


def test_project_files_route_helper_path_is_relative(workspace):
    # Path objects in the payload must be workspace-relative strings
    controller = _controller(workspace)
    req = SimpleNamespace(
        message="todo 앱 html+css+js로 만들어줘",
        stream=False,
        max_tokens=0,
        temperature=0.2,
        source="web",
        conversation_id=None,
    )
    response = asyncio.run(controller.direct_file_action(req, model_id="local-test"))
    payload = json.loads(response.body)
    for artifact in payload["artifacts"]:
        assert not Path(artifact["path"]).is_absolute()
        assert artifact["path"].startswith("todo-app/")
