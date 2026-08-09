"""wp13 coverage — ``latticeai.tools.filesystem`` search, HTML and packaging.

``search_files`` is the cheap substring scanner (nine extensions, one hit per
file), ``inspect_html`` summarises a page the agent just wrote, and
``zip_workspace_dir`` hands a generated project back to the user. The last one
is a containment surface: it must refuse the workspace root, skip symlinks,
and still refuse a path that resolves outside the directory being archived
even if the symlink check were somehow bypassed.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import latticeai.tools as tools
from latticeai.tools import MAX_FILE_BYTES, ToolError
from latticeai.tools.filesystem import (
    _HTMLInspector,
    create_web_project,
    inspect_html,
    preview_url,
    search_files,
    zip_workspace_dir,
)

_PAGE = """<!doctype html>
<html>
  <head>
    <title>Demo Page</title>
    <link rel="stylesheet" href="/styles.css" />
    <script src="/app.js"></script>
  </head>
  <body>
    <h1>Headline</h1>
    <h2></h2>
    <h3>Third</h3>
    <p>Body copy that is not a heading.</p>
    <a href="/next">next</a>
    <a>no href</a>
    <img src="/logo.png" />
    <form><input name="q" /></form>
    <form><input name="r" /></form>
  </body>
</html>
"""


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_workspace"
    root.mkdir()
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    tools.ensure_agent_root()
    return root


# ── search_files ─────────────────────────────────────────────────────────────


def test_search_files_requires_a_query(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Query is required"):
        search_files("")


def test_search_files_refuses_a_path_that_is_not_a_directory(workspace: Path) -> None:
    (workspace / "notes.md").write_text("needle", encoding="utf-8")

    with pytest.raises(ToolError, match="not a directory"):
        search_files("needle", path="notes.md")


def test_search_files_returns_the_first_hit_per_file_case_insensitively(workspace: Path) -> None:
    (workspace / "a.py").write_text("nothing\nNEEDLE here\nneedle again\n", encoding="utf-8")

    result = search_files("needle")

    assert result["query"] == "needle"
    assert result["matches"] == [{"path": "a.py", "line": 2, "preview": "NEEDLE here"}]


def test_search_files_ignores_directories_and_unlisted_extensions(workspace: Path) -> None:
    (workspace / "pkg").mkdir()
    (workspace / "pkg" / "mod.py").write_text("needle\n", encoding="utf-8")
    (workspace / "notes.rst").write_text("needle\n", encoding="utf-8")
    (workspace / "archive.zip").write_text("needle\n", encoding="utf-8")

    paths = [m["path"] for m in search_files("needle")["matches"]]

    assert paths == ["pkg/mod.py"]


def test_search_files_skips_files_over_the_size_cap(workspace: Path) -> None:
    (workspace / "huge.txt").write_text("needle\n" + "x" * MAX_FILE_BYTES, encoding="utf-8")
    (workspace / "small.txt").write_text("needle\n", encoding="utf-8")

    assert [m["path"] for m in search_files("needle")["matches"]] == ["small.txt"]


def test_search_files_skips_a_file_that_is_not_valid_utf8(workspace: Path) -> None:
    (workspace / "mojibake.txt").write_bytes(b"\xff\xfe\x00needle")
    (workspace / "clean.txt").write_text("needle\n", encoding="utf-8")

    assert [m["path"] for m in search_files("needle")["matches"]] == ["clean.txt"]


def test_search_files_stops_at_max_results(workspace: Path) -> None:
    (workspace / "one.md").write_text("needle\n", encoding="utf-8")
    (workspace / "two.md").write_text("needle\n", encoding="utf-8")

    assert len(search_files("needle", max_results=1)["matches"]) == 1
    # The caller's count is clamped into 1..100 rather than trusted.
    assert len(search_files("needle", max_results=0)["matches"]) == 1
    assert len(search_files("needle", max_results=10_000)["matches"]) == 2


def test_search_files_truncates_a_long_preview(workspace: Path) -> None:
    (workspace / "wide.txt").write_text("needle" + "!" * 500, encoding="utf-8")

    assert len(search_files("needle")["matches"][0]["preview"]) == 240


# ── _HTMLInspector / inspect_html ────────────────────────────────────────────


def test_inspect_html_summarises_a_page(workspace: Path) -> None:
    (workspace / "index.html").write_text(_PAGE, encoding="utf-8")

    result = inspect_html("index.html")

    assert result["path"] == "index.html"
    assert result["title"] == "Demo Page"
    assert result["links"] == ["/next"]
    assert result["scripts"] == ["/app.js"]
    assert result["images"] == ["/logo.png"]
    assert result["forms"] == 2
    # Empty headings are dropped; the first text after a heading tag fills it.
    assert result["headings"] == [
        {"level": "h1", "text": "Headline"},
        {"level": "h3", "text": "Third"},
    ]


def test_inspect_html_does_not_collect_stylesheets_from_a_real_page(workspace: Path) -> None:
    """Known defect, pinned so a fix is a deliberate change.

    ``handle_starttag`` tests the rel attribute with
    ``"stylesheet" in " ".join(attr.get("rel", []))``. ``HTMLParser`` hands
    every attribute value over as a *string*, so joining it inserts a space
    between each character ("s t y l e s h e e t") and the membership test can
    never succeed. The collector below shows the branch does work — but only
    for a list-valued rel, which no parsed page produces.
    """
    (workspace / "index.html").write_text(_PAGE, encoding="utf-8")

    assert inspect_html("index.html")["stylesheets"] == []

    inspector = _HTMLInspector()
    inspector.handle_starttag("link", [("rel", ["stylesheet"]), ("href", "/styles.css")])
    assert inspector.stylesheets == ["/styles.css"]


def test_html_inspector_ignores_a_stylesheet_link_without_an_href() -> None:
    inspector = _HTMLInspector()
    inspector.handle_starttag("link", [("rel", ["stylesheet"])])

    assert inspector.stylesheets == []


def test_html_inspector_ignores_whitespace_and_text_outside_headings() -> None:
    inspector = _HTMLInspector()
    inspector.feed("<p>   </p><title>Split</title><title> Title</title><p>ignored</p>")

    assert inspector.title == "SplitTitle"
    assert inspector.headings == []


def test_html_inspector_keeps_only_the_first_text_of_a_heading() -> None:
    inspector = _HTMLInspector()
    inspector.feed("<h2>first<span>second</span></h2>")

    assert inspector.headings == [{"level": "h2", "text": "first"}]


def test_inspect_html_refuses_a_missing_file(workspace: Path) -> None:
    with pytest.raises(ToolError, match="HTML file does not exist"):
        inspect_html("ghost.html")


def test_inspect_html_refuses_a_non_html_file(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("plain", encoding="utf-8")

    with pytest.raises(ToolError, match="not an HTML file"):
        inspect_html("notes.txt")


def test_inspect_html_refuses_a_page_over_the_size_cap(workspace: Path) -> None:
    (workspace / "huge.html").write_text("<p>" + "x" * MAX_FILE_BYTES, encoding="utf-8")

    with pytest.raises(ToolError, match="too large to inspect"):
        inspect_html("huge.html")


def test_inspect_html_accepts_the_htm_extension(workspace: Path) -> None:
    (workspace / "page.HTM").write_text("<title>Old School</title>", encoding="utf-8")

    assert inspect_html("page.HTM")["title"] == "Old School"


# ── preview_url ──────────────────────────────────────────────────────────────


def test_preview_url_builds_a_loopback_link_for_a_workspace_file(workspace: Path) -> None:
    (workspace / "site").mkdir()
    (workspace / "site" / "index.html").write_text("<p>hi</p>", encoding="utf-8")

    result = preview_url("site/index.html")

    assert result["path"] == "site/index.html"
    assert result["local_url"] == "http://127.0.0.1:4825/agent-files/site/index.html"
    assert "127.0.0.1" in result["note"]


def test_preview_url_refuses_a_file_that_does_not_exist(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Preview file does not exist"):
        preview_url()


# ── create_web_project ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "framework,template",
    [("vue", "vite"), ("react", "webpack"), ("", ""), ("REACT", "parcel")],
)
def test_create_web_project_only_scaffolds_react_plus_vite(
    workspace: Path, framework: str, template: str
) -> None:
    with pytest.raises(ToolError, match="Only React \\+ Vite"):
        create_web_project("app", framework=framework, template=template)


def test_create_web_project_requires_a_path(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Project path is required"):
        create_web_project("", framework="React", template="Vite")


def test_create_web_project_writes_the_starter_inside_the_workspace(workspace: Path) -> None:
    result = create_web_project("my app")

    assert result["path"] == "my app"
    assert result["framework"] == "react"
    assert result["file_count"] == len(result["created_files"])
    assert "my app/src/App.jsx" in result["created_files"]
    assert result["bytes"] > 0
    package_json = workspace / "my app" / "package.json"
    assert '"name": "my-app"' in package_json.read_text(encoding="utf-8")


def test_create_web_project_cannot_escape_the_workspace(workspace: Path) -> None:
    with pytest.raises(ToolError, match="escapes the agent workspace"):
        create_web_project("../escaped")


# ── zip_workspace_dir ────────────────────────────────────────────────────────


def test_zip_workspace_dir_archives_a_project_under_its_own_name(workspace: Path) -> None:
    project = workspace / "todo-app"
    (project / "src").mkdir(parents=True)
    (project / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (project / "src" / "main.js").write_text("console.log(1)\n", encoding="utf-8")

    payload, filename = zip_workspace_dir("todo-app")

    assert filename == "todo-app.zip"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert sorted(archive.namelist()) == ["todo-app/index.html", "todo-app/src/main.js"]
        assert archive.read("todo-app/index.html") == b"<p>hi</p>"


def test_zip_workspace_dir_refuses_the_workspace_root(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Cannot zip the entire workspace root"):
        zip_workspace_dir(".")


def test_zip_workspace_dir_refuses_something_that_is_not_a_directory(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ToolError, match="not a directory in the workspace"):
        zip_workspace_dir("notes.txt")


def test_zip_workspace_dir_skips_symlinks(workspace: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do not archive me", encoding="utf-8")
    project = workspace / "app"
    project.mkdir()
    (project / "real.txt").write_text("ok", encoding="utf-8")
    (project / "leak.txt").symlink_to(secret)

    payload, _ = zip_workspace_dir("app")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["app/real.txt"]


def test_zip_workspace_dir_still_refuses_an_escaping_path_if_the_symlink_check_fails(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth: the resolved-path check is the second, independent gate.

    ``is_symlink`` is forced to False to stand in for the check being bypassed
    — a filesystem race, or a link type the first test misses. The archive must
    still come out with only the genuine in-directory file.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("do not archive me", encoding="utf-8")
    project = workspace / "app"
    project.mkdir()
    (project / "real.txt").write_text("ok", encoding="utf-8")
    (project / "leak.txt").symlink_to(secret)

    monkeypatch.setattr(Path, "is_symlink", lambda self: False)

    payload, _ = zip_workspace_dir("app")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["app/real.txt"]


def test_zip_workspace_dir_refuses_a_tree_over_the_byte_budget(workspace: Path) -> None:
    project = workspace / "app"
    project.mkdir()
    (project / "big.txt").write_text("x" * 4096, encoding="utf-8")

    with pytest.raises(ToolError, match="too large to zip"):
        zip_workspace_dir("app", max_total_bytes=1024)
