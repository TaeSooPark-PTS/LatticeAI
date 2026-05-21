"""Unit tests for tools.py core functions."""
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import tools as tools_module
from tools import (
    ToolError,
    edit_file,
    grep,
    local_list,
    local_read,
    local_write,
    read_document,
    read_file,
    todo_read,
    todo_write,
    write_file,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Redirect tools' AGENT_ROOT to a temp directory for the duration of a test."""
    monkeypatch.setattr(tools_module, "AGENT_ROOT", tmp_path)
    tools_module.ensure_agent_root()
    return tmp_path


# ---------------------------------------------------------------------------
# local_list
# ---------------------------------------------------------------------------

def test_local_list_returns_items(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    result = local_list(str(tmp_path))
    names = [i["name"] for i in result["items"]]
    assert "a.txt" in names
    assert "sub" in names


def test_local_list_dirs_before_files(tmp_path):
    (tmp_path / "z_file.txt").write_text("x")
    (tmp_path / "a_dir").mkdir()
    result = local_list(str(tmp_path))
    types = [i["type"] for i in result["items"]]
    assert types[0] == "directory"


def test_local_list_nonexistent_raises(tmp_path):
    with pytest.raises(ToolError):
        local_list(str(tmp_path / "does_not_exist"))


def test_local_list_file_raises(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ToolError):
        local_list(str(f))


# ---------------------------------------------------------------------------
# local_read
# ---------------------------------------------------------------------------

def test_local_read_text_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world")
    result = local_read(str(f))
    assert "hello world" in result["content"]
    assert result["path"] == str(f)


def test_local_read_missing_file_raises(tmp_path):
    with pytest.raises(ToolError):
        local_read(str(tmp_path / "missing.txt"))


def test_local_read_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "testfile.txt"
    f.write_text("tilde test")
    result = local_read("~/testfile.txt")
    assert "tilde test" in result["content"]


def test_local_read_returns_size(tmp_path):
    f = tmp_path / "sized.txt"
    f.write_text("abc")
    result = local_read(str(f))
    assert result["size"] == 3


# ---------------------------------------------------------------------------
# local_write
# ---------------------------------------------------------------------------

def test_local_write_creates_file(tmp_path):
    target = tmp_path / "out.txt"
    result = local_write(str(target), "new content")
    assert target.read_text() == "new content"
    assert "bytes" in result


def test_local_write_overwrites_file(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old")
    local_write(str(target), "new")
    assert target.read_text() == "new"


def test_local_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "file.txt"
    local_write(str(target), "deep write")
    assert target.exists()


def test_local_write_returns_path(tmp_path):
    target = tmp_path / "x.txt"
    result = local_write(str(target), "hi")
    assert result["path"] == str(target)


# ---------------------------------------------------------------------------
# read_document
# ---------------------------------------------------------------------------

def test_read_document_plain_text(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("plain text content")
    result = read_document(str(f))
    assert "plain text content" in result.get("text", "") or "plain text content" in str(result)


def test_read_document_missing_file_raises(tmp_path):
    with pytest.raises(ToolError):
        read_document(str(tmp_path / "missing.pdf"))


def test_read_document_csv(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("col1,col2\n1,2\n3,4\n")
    result = read_document(str(f))
    # should not raise; returns some text content
    assert result is not None


# ---------------------------------------------------------------------------
# read_file (workspace, with line numbers and offset/limit)
# ---------------------------------------------------------------------------

def test_read_file_returns_numbered_view(workspace):
    (workspace / "a.txt").write_text("alpha\nbeta\ngamma\n")
    result = read_file("a.txt")
    assert result["total_lines"] == 3
    assert result["start_line"] == 1
    assert result["end_line"] == 3
    assert "1\talpha" in result["numbered"]
    assert "3\tgamma" in result["numbered"]


def test_read_file_offset_and_limit(workspace):
    (workspace / "a.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    result = read_file("a.txt", offset=3, limit=2)
    assert result["start_line"] == 4
    assert result["end_line"] == 5
    assert result["content"].splitlines() == ["line4", "line5"]


def test_read_file_disable_line_numbers(workspace):
    (workspace / "a.txt").write_text("only\n")
    result = read_file("a.txt", line_numbers=False)
    assert "numbered" not in result
    assert result["content"] == "only\n"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def test_edit_file_replaces_unique_match(workspace):
    (workspace / "code.py").write_text("def foo():\n    return 1\n")
    result = edit_file("code.py", "return 1", "return 42")
    assert (workspace / "code.py").read_text() == "def foo():\n    return 42\n"
    assert result["replacements"] == 1
    assert result["first_edit_line"] == 2


def test_edit_file_missing_string_raises(workspace):
    (workspace / "code.py").write_text("hello\n")
    with pytest.raises(ToolError, match="not found"):
        edit_file("code.py", "missing", "world")


def test_edit_file_ambiguous_raises_unless_replace_all(workspace):
    (workspace / "code.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="ambiguous"):
        edit_file("code.py", "x = 1", "x = 2")
    result = edit_file("code.py", "x = 1", "x = 2", replace_all=True)
    assert result["replacements"] == 2
    assert (workspace / "code.py").read_text() == "x = 2\nx = 2\n"


def test_edit_file_rejects_identical(workspace):
    (workspace / "code.py").write_text("same\n")
    with pytest.raises(ToolError, match="identical"):
        edit_file("code.py", "same", "same")


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------

def test_grep_finds_regex_matches(workspace):
    (workspace / "a.py").write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    (workspace / "b.py").write_text("x = 1\n")
    result = grep(r"^def \w+", path=".")
    paths = sorted({m["path"] for m in result["matches"]})
    assert "a.py" in paths
    assert result["files_with_matches"] == 1


def test_grep_respects_glob(workspace):
    (workspace / "a.py").write_text("needle\n")
    (workspace / "a.txt").write_text("needle\n")
    result = grep("needle", path=".", glob="*.py")
    paths = [m["path"] for m in result["matches"]]
    assert "a.py" in paths
    assert "a.txt" not in paths


def test_grep_case_insensitive(workspace):
    (workspace / "a.txt").write_text("HELLO world\n")
    result = grep("hello", path=".", case_insensitive=True)
    assert any("HELLO" in m["match"] for m in result["matches"])


def test_grep_context_lines(workspace):
    (workspace / "a.txt").write_text("before\nhit\nafter\n")
    result = grep("hit", path=".", context_lines=1)
    assert result["matches"]
    ctx_lines = [c["text"] for c in result["matches"][0]["context"]]
    assert "before" in ctx_lines and "after" in ctx_lines


def test_grep_invalid_regex_raises(workspace):
    (workspace / "a.txt").write_text("hello\n")
    with pytest.raises(ToolError, match="regex"):
        grep("[unterminated", path=".")


def test_grep_skips_binary_dirs(workspace):
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "x.js").write_text("needle\n")
    (workspace / "src.py").write_text("needle\n")
    result = grep("needle", path=".")
    paths = [m["path"] for m in result["matches"]]
    assert "src.py" in paths
    assert all("node_modules" not in p for p in paths)


# ---------------------------------------------------------------------------
# todo_read / todo_write
# ---------------------------------------------------------------------------

def test_todo_read_empty_when_unset(workspace):
    result = todo_read()
    assert result["todos"] == []


def test_todo_write_round_trip(workspace):
    todos = [
        {"id": "1", "content": "design API", "status": "completed"},
        {"id": "2", "content": "write tests", "status": "in_progress"},
        {"id": "3", "content": "deploy", "status": "pending"},
    ]
    todo_write(todos)
    fresh = todo_read()
    assert [t["content"] for t in fresh["todos"]] == ["design API", "write tests", "deploy"]
    assert fresh["todos"][1]["status"] == "in_progress"


def test_todo_write_rejects_invalid_status(workspace):
    with pytest.raises(ToolError, match="status"):
        todo_write([{"id": "1", "content": "x", "status": "blocked"}])


def test_todo_write_rejects_missing_content(workspace):
    with pytest.raises(ToolError, match="content"):
        todo_write([{"id": "1", "content": "", "status": "pending"}])


def test_todo_write_warns_multiple_in_progress(workspace):
    result = todo_write([
        {"id": "1", "content": "a", "status": "in_progress"},
        {"id": "2", "content": "b", "status": "in_progress"},
    ])
    assert result["warning"]


# ---------------------------------------------------------------------------
# Sandbox: workspace tools must not escape AGENT_ROOT
# ---------------------------------------------------------------------------

def test_read_file_blocks_path_escape(workspace):
    with pytest.raises(ToolError, match="escapes"):
        read_file("../../etc/passwd")


def test_edit_file_blocks_path_escape(workspace):
    with pytest.raises(ToolError, match="escapes"):
        edit_file("../../etc/passwd", "a", "b")


def test_grep_blocks_path_escape(workspace):
    with pytest.raises(ToolError, match="escapes"):
        grep("x", path="../../etc")
