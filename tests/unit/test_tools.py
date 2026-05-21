"""Unit tests for tools.py core functions."""
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tools import local_list, local_read, local_write, read_document, ToolError


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
