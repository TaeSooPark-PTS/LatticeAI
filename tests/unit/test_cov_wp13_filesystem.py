"""wp13 coverage — ``latticeai.tools.filesystem`` listing, reading and editing.

Every function here resolves its path through the workspace sandbox, so the
fixture points ``AGENT_ROOT`` at ``tmp_path`` and each test builds a real tree
inside it. The cases that matter are the refusals — a path that is not a
directory, a file over the size cap, an edit that would grow a file past it —
because those are the only things standing between the agent and an unbounded
read or write.

Companion file: ``test_cov_wp13_filesystem_web.py`` covers search, HTML
inspection, scaffolding and zipping.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import latticeai.tools as tools
from latticeai.tools import MAX_FILE_BYTES, ToolError
from latticeai.tools.filesystem import (
    _TODO_REL_PATH,
    edit_file,
    grep,
    list_dir,
    read_file,
    todo_read,
    todo_write,
    workspace_tree,
    write_file,
)


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_workspace"
    root.mkdir()
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    tools.ensure_agent_root()
    return root


def _oversized(target: Path) -> Path:
    """A file just past the workspace read/edit cap."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("needle\n" + "x" * MAX_FILE_BYTES, encoding="utf-8")
    assert target.stat().st_size > MAX_FILE_BYTES
    return target


# ── list_dir ─────────────────────────────────────────────────────────────────


def test_list_dir_reports_directories_first_with_sizes(workspace: Path) -> None:
    (workspace / "zeta.txt").write_text("hello", encoding="utf-8")
    (workspace / "Alpha").mkdir()
    (workspace / "Alpha" / "nested.txt").write_text("x", encoding="utf-8")

    result = list_dir()

    assert result["root"] == str(workspace)
    assert result["path"] == "."
    assert [(i["name"], i["type"]) for i in result["items"]] == [
        ("Alpha", "directory"),
        ("zeta.txt", "file"),
    ]
    assert result["items"][0]["size"] is None
    assert result["items"][1]["size"] == 5
    assert result["items"][1]["path"] == "zeta.txt"


def test_list_dir_of_a_subdirectory_reports_its_relative_path(workspace: Path) -> None:
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = list_dir("src")

    assert result["path"] == "src"
    assert result["items"][0]["path"] == "src/app.py"


def test_list_dir_refuses_a_missing_directory(workspace: Path) -> None:
    with pytest.raises(ToolError, match="does not exist"):
        list_dir("ghost")


def test_list_dir_refuses_a_file(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ToolError, match="not a directory"):
        list_dir("notes.txt")


def test_list_dir_cannot_escape_the_workspace(workspace: Path) -> None:
    with pytest.raises(ToolError, match="escapes the agent workspace"):
        list_dir("../..")


# ── workspace_tree ───────────────────────────────────────────────────────────


def test_workspace_tree_walks_depth_first_and_records_depth(workspace: Path) -> None:
    (workspace / "src" / "deep").mkdir(parents=True)
    (workspace / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (workspace / "src" / "deep" / "inner.py").write_text("y = 2\n", encoding="utf-8")
    (workspace / "top.txt").write_text("t", encoding="utf-8")

    result = workspace_tree(max_depth=3)

    assert result["root"] == str(workspace)
    assert result["path"] == "."
    assert [(e["path"], e["depth"]) for e in result["entries"]] == [
        ("src", 1),
        ("src/deep", 2),
        ("src/deep/inner.py", 3),
        ("src/app.py", 2),
        ("top.txt", 1),
    ]
    assert result["entries"][0]["size"] is None
    assert result["entries"][-1]["size"] == 1


def test_workspace_tree_stops_at_max_depth(workspace: Path) -> None:
    (workspace / "a" / "b" / "c").mkdir(parents=True)
    (workspace / "a" / "b" / "c" / "buried.txt").write_text("x", encoding="utf-8")

    paths = [e["path"] for e in workspace_tree(max_depth=1)["entries"]]

    assert paths == ["a"]


def test_workspace_tree_clamps_an_absurd_depth(workspace: Path) -> None:
    (workspace / "a").mkdir()
    (workspace / "a" / "f.txt").write_text("x", encoding="utf-8")

    # 0 clamps up to 1, 99 clamps down to 8 — both still return a tree.
    assert [e["path"] for e in workspace_tree(max_depth=0)["entries"]] == ["a"]
    assert [e["path"] for e in workspace_tree(max_depth=99)["entries"]] == ["a", "a/f.txt"]


def test_workspace_tree_refuses_a_file(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ToolError, match="not a directory"):
        workspace_tree("notes.txt")


def test_workspace_tree_of_a_subdirectory_reports_that_subdirectory(workspace: Path) -> None:
    (workspace / "site").mkdir()
    (workspace / "site" / "index.html").write_text("<p>hi</p>", encoding="utf-8")

    result = workspace_tree("site")

    assert result["path"] == "site"
    assert [e["path"] for e in result["entries"]] == ["site/index.html"]


# ── read_file ────────────────────────────────────────────────────────────────


def test_read_file_refuses_a_missing_file(workspace: Path) -> None:
    with pytest.raises(ToolError, match="File does not exist"):
        read_file("ghost.txt")


def test_read_file_refuses_a_directory(workspace: Path) -> None:
    (workspace / "src").mkdir()

    with pytest.raises(ToolError, match="not a file"):
        read_file("src")


def test_read_file_refuses_a_file_over_the_size_cap(workspace: Path) -> None:
    _oversized(workspace / "huge.txt")

    with pytest.raises(ToolError, match="too large to read"):
        read_file("huge.txt")


# ── write_file ───────────────────────────────────────────────────────────────


def test_write_file_creates_parent_directories(workspace: Path) -> None:
    result = write_file("a/b/c.txt", "content")

    assert (workspace / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "content"
    assert result == {"path": "a/b/c.txt", "bytes": 7}


def test_write_file_refuses_content_over_the_size_cap(workspace: Path) -> None:
    with pytest.raises(ToolError, match="too large to write"):
        write_file("huge.txt", "x" * (MAX_FILE_BYTES + 1))

    assert not (workspace / "huge.txt").exists()


# ── edit_file ────────────────────────────────────────────────────────────────


def test_edit_file_refuses_a_missing_file(workspace: Path) -> None:
    with pytest.raises(ToolError, match="File does not exist"):
        edit_file("ghost.py", "a", "b")


def test_edit_file_refuses_a_file_over_the_size_cap(workspace: Path) -> None:
    _oversized(workspace / "huge.txt")

    with pytest.raises(ToolError, match="too large to edit"):
        edit_file("huge.txt", "needle", "hay")


def test_edit_file_refuses_an_edit_that_would_blow_past_the_cap(workspace: Path) -> None:
    """The read cap is enforced on the *result*, not only on the input."""
    (workspace / "small.txt").write_text("SEED", encoding="utf-8")

    with pytest.raises(ToolError, match="exceed the workspace size limit"):
        edit_file("small.txt", "SEED", "x" * (MAX_FILE_BYTES + 1))

    assert (workspace / "small.txt").read_text(encoding="utf-8") == "SEED"


# ── grep ─────────────────────────────────────────────────────────────────────


def test_grep_requires_a_pattern(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Pattern is required"):
        grep("")


def test_grep_refuses_a_path_that_is_not_a_directory(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("needle", encoding="utf-8")

    with pytest.raises(ToolError, match="not a directory"):
        grep("needle", path="notes.txt")


def test_grep_stops_scanning_files_once_max_results_is_reached(workspace: Path) -> None:
    (workspace / "one.txt").write_text("needle\n", encoding="utf-8")
    (workspace / "two.txt").write_text("needle\n", encoding="utf-8")

    result = grep("needle", max_results=1)

    assert len(result["matches"]) == 1
    assert result["truncated"] is True
    assert result["files_scanned"] == 1


def test_grep_stops_scanning_lines_once_max_results_is_reached(workspace: Path) -> None:
    (workspace / "only.txt").write_text("needle a\nneedle b\nneedle c\n", encoding="utf-8")

    result = grep("needle", max_results=1)

    assert [m["line"] for m in result["matches"]] == [1]
    assert result["files_with_matches"] == 1


def test_grep_skips_files_with_binary_extensions(workspace: Path) -> None:
    (workspace / "logo.png").write_text("needle", encoding="utf-8")
    (workspace / "notes.md").write_text("needle", encoding="utf-8")

    paths = [m["path"] for m in grep("needle")["matches"]]

    assert paths == ["notes.md"]


def test_grep_skips_files_over_the_size_cap(workspace: Path) -> None:
    _oversized(workspace / "huge.txt")
    (workspace / "small.txt").write_text("needle\n", encoding="utf-8")

    result = grep("needle")

    assert [m["path"] for m in result["matches"]] == ["small.txt"]
    assert result["files_scanned"] == 1


def test_grep_skips_a_file_that_is_not_valid_utf8(workspace: Path) -> None:
    (workspace / "mojibake.txt").write_bytes(b"\xff\xfe\x00needle")
    (workspace / "clean.txt").write_text("needle\n", encoding="utf-8")

    result = grep("needle")

    assert [m["path"] for m in result["matches"]] == ["clean.txt"]
    assert result["files_scanned"] == 1
    assert result["truncated"] is False


def test_grep_clamps_max_results_and_context_lines(workspace: Path) -> None:
    (workspace / "a.txt").write_text("\n".join("line " + str(i) for i in range(40)), encoding="utf-8")

    # context_lines is clamped to 8 either side, so a hit in the middle of the
    # file returns at most 17 context rows however large the request was.
    result = grep("line 20", context_lines=100)

    assert len(result["matches"][0]["context"]) == 17
    assert grep("line", max_results=10_000)["truncated"] is False


# ── todo_read / todo_write ───────────────────────────────────────────────────


def test_todo_read_recovers_from_a_corrupt_todo_file(workspace: Path) -> None:
    todo_file = workspace / _TODO_REL_PATH
    todo_file.parent.mkdir(parents=True, exist_ok=True)
    todo_file.write_text("{not json", encoding="utf-8")

    assert todo_read() == {"todos": [], "path": _TODO_REL_PATH}


def test_todo_read_ignores_a_todo_file_that_is_not_a_list(workspace: Path) -> None:
    todo_file = workspace / _TODO_REL_PATH
    todo_file.parent.mkdir(parents=True, exist_ok=True)
    todo_file.write_text(json.dumps({"todos": "nope"}), encoding="utf-8")

    assert todo_read()["todos"] == []


def test_todo_write_refuses_a_non_list(workspace: Path) -> None:
    with pytest.raises(ToolError, match="todos must be a list"):
        todo_write({"id": "1"})


def test_todo_write_refuses_more_than_fifty_todos(workspace: Path) -> None:
    todos = [{"id": str(i), "content": "task", "status": "pending"} for i in range(51)]

    with pytest.raises(ToolError, match="Too many todos"):
        todo_write(todos)


def test_todo_write_refuses_a_todo_that_is_not_an_object(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Todo #2 is not an object"):
        todo_write([{"content": "ok", "status": "pending"}, "just a string"])


def test_todo_write_assigns_ids_and_truncates_long_content(workspace: Path) -> None:
    result = todo_write([{"content": "y" * 300, "status": "pending"}])

    assert result["todos"][0]["id"] == "1"
    assert len(result["todos"][0]["content"]) == 240
    assert result["warning"] is None
    assert (workspace / _TODO_REL_PATH).exists()
