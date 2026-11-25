from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.tools import ToolError


def test_filesystem_remaining_branches(tmp_path: Path, monkeypatch):
    import latticeai.tools as tools
    from latticeai.tools.filesystem import (
        grep,
        inspect_html,
        list_dir,
        preview_url,
        read_file,
        search_files,
        todo_read,
        workspace_tree,
    )

    root = tmp_path / "ws"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "sub" / "note.md").write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    (root / "sub" / "deep" / "more.md").write_text("gamma\n", encoding="utf-8")
    (root / "pic.png").write_bytes(b"\x89PNG")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "x.js").write_text("secret", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01")
    (root / "page.html").write_text(
        "<html><head><title>T</title><link rel='Stylesheet Preload' href='/s.css'>"
        "<script src='/a.js'></script></head>"
        "<body><a href='/l'>L</a><img src='/i.png'><form></form>"
        "<h1>H</h1><h2></h2></body></html>",
        encoding="utf-8",
    )
    (root / ".lattice").mkdir()
    (root / ".lattice" / "todos.json").write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)

    with pytest.raises(ToolError):
        list_dir("missing")
    (root / "file_only.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError):
        list_dir("file_only.txt")
    listed = list_dir(".")
    assert listed["items"]

    with pytest.raises(ToolError):
        workspace_tree("missing")
    tree = workspace_tree(".", max_depth=0)
    assert tree["entries"]
    tree = workspace_tree("sub", max_depth=9)
    assert any(e["path"].endswith("more.md") for e in tree["entries"])

    with pytest.raises(ToolError):
        read_file("missing.txt")
    with pytest.raises(ToolError):
        read_file("sub")
    body = read_file("sub/note.md", offset=1, limit=1, line_numbers=False)
    assert "beta" in body["content"]
    body = read_file("sub/note.md", offset=0, limit=0, line_numbers=True)
    assert "numbered" in body

    huge = root / "huge.txt"
    huge.write_bytes(b"x" * (tools.MAX_FILE_BYTES + 1))
    with pytest.raises(ToolError):
        read_file("huge.txt")

    with pytest.raises(ToolError):
        grep("alpha", path="missing")
    hits = grep(
        "alpha",
        path=".",
        glob="*.md",
        max_results=1,
        context_lines=9,
        case_insensitive=True,
    )
    assert hits["truncated"] is True
    hits = grep("nomatch", path=".")
    assert hits["matches"] == []
    grep("secret", path=".")

    with pytest.raises(ToolError):
        search_files("")
    with pytest.raises(ToolError):
        search_files("x", path="missing")
    found = search_files("alpha", path=".", max_results=1)
    assert found["matches"]
    search_files("nope-xyz")

    with pytest.raises(ToolError):
        inspect_html("missing.html")
    with pytest.raises(ToolError):
        inspect_html("sub/note.md")
    html = inspect_html("page.html")
    assert html["title"] == "T"
    assert html["stylesheets"]
    huge_html = root / "big.html"
    huge_html.write_bytes(b"<html></html>" + b"x" * (tools.MAX_FILE_BYTES + 1))
    with pytest.raises(ToolError):
        inspect_html("big.html")

    todos = todo_read()
    assert todos["todos"] == []
    (root / ".lattice" / "todos.json").write_text('{"not":"list"}', encoding="utf-8")
    assert todo_read()["todos"] == []
    (root / ".lattice" / "todos.json").write_text('[{"id":1}]', encoding="utf-8")
    assert todo_read()["todos"]

    with pytest.raises(ToolError):
        preview_url("missing.html")
    assert "local_url" in preview_url("page.html")

    (root / "bad.md").write_bytes(b"\xff\xfe")
    grep("alpha", path=".")
    search_files("alpha", path=".")


def test_knowledge_remaining_branches(tmp_path: Path, monkeypatch):
    from latticeai.tools.knowledge import (
        knowledge_scope_root,
        knowledge_search,
        knowledge_tree,
        obsidian_search,
        obsidian_tree,
    )

    brain = tmp_path / "brain"
    brain.mkdir()
    monkeypatch.setattr("latticeai.tools.knowledge.BRAIN_DIR", brain)
    monkeypatch.setattr(
        "latticeai.tools.knowledge.STRUCTURE", {"10_Wiki": "x", "00_Raw": "y"}
    )

    assert knowledge_scope_root() == brain
    with pytest.raises(ToolError):
        knowledge_scope_root(workspace_id="ws")
    scoped = knowledge_scope_root(workspace_id="ws", user_email="a@b.c")
    assert ".lattice-scopes" in str(scoped)

    with pytest.raises(ToolError):
        knowledge_search("")
    (brain / "10_Wiki").mkdir()
    (brain / "10_Wiki" / "hit.md").write_text("remember this", encoding="utf-8")
    (brain / "10_Wiki" / "name-only.md").write_text("zzz", encoding="utf-8")
    (brain / "10_Wiki" / "bad.md").write_bytes(b"\xff\xfe")
    hits = knowledge_search("remember", max_results=1)
    assert hits["results"]
    hits = knowledge_search("name-only", max_results=20)
    assert hits["results"]

    tree = knowledge_tree()
    assert tree["entries"]
    scoped_tree = knowledge_tree(workspace_id="ws", user_email="a@b.c")
    assert scoped_tree["root"]
    obs = obsidian_search("remember")
    assert "vault_root" in obs
    assert obsidian_tree()["root"]


def test_search_router_remaining_lines():
    from latticeai.api.search import create_search_router

    class Svc:
        def embeddings_status(self, resolved=None, refresh=False):
            if refresh:
                raise ValueError("no embedder")
            return {"ok": True, "resolved": resolved}

    app = FastAPI()
    app.include_router(
        create_search_router(
            service=Svc(),
            require_user=lambda _r: "u",
            embedding_info=None,
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    ok = client.get("/api/embeddings/status")
    assert ok.status_code == 200
    err = client.get("/api/embeddings/status", params={"refresh": True})
    assert err.status_code == 404

    app2 = FastAPI()
    app2.include_router(
        create_search_router(
            service=Svc(),
            require_user=lambda _r: "u",
            embedding_info=lambda: {
                "profiles": [{"id": "hash"}],
                "active_provider": "hash",
            },
        )
    )
    client2 = TestClient(app2, raise_server_exceptions=False)
    resolved = client2.get("/api/embeddings/status")
    assert resolved.json()["resolved"]["active_provider"] == "hash"


def test_filesystem_knowledge_documents_local_and_commands(tmp_path: Path, monkeypatch):
    import latticeai.tools as tools
    from latticeai.tools.commands import git_diff
    from latticeai.tools.documents import document_output_target, read_document
    from latticeai.tools.filesystem import inspect_html
    from latticeai.tools.knowledge import knowledge_search
    from latticeai.tools.local_files import local_list, local_read

    root = tmp_path / "ws"
    root.mkdir()
    (root / "page.html").write_text(
        "<html><link rel='stylesheet'></html>", encoding="utf-8"
    )
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    inspect_html("page.html")

    brain = tmp_path / "brain"
    (brain / "10_Wiki").mkdir(parents=True)
    (brain / "10_Wiki" / "a.md").write_text("remember one", encoding="utf-8")
    (brain / "10_Wiki" / "b.md").write_text("remember two", encoding="utf-8")
    monkeypatch.setattr("latticeai.tools.knowledge.BRAIN_DIR", brain)
    monkeypatch.setattr("latticeai.tools.knowledge.STRUCTURE", {"10_Wiki": "x"})
    hits = knowledge_search("remember", max_results=1)
    assert len(hits["results"]) == 1

    assert document_output_target("nope", "x") is None
    with pytest.raises(ToolError):
        read_document(str(tmp_path / "missing.txt"))
    with pytest.raises(ToolError):
        read_document(str(root))
    huge = root / "huge.txt"
    from latticeai.tools import DOCUMENT_MAX_READ_BYTES

    huge.write_bytes(b"x" * (DOCUMENT_MAX_READ_BYTES + 1))
    with pytest.raises(ToolError):
        read_document(str(huge))

    bad_pdf = root / "bad.pdf"
    bad_pdf.write_bytes(b"not-a-pdf")
    with pytest.raises(ToolError):
        read_document(str(bad_pdf))
    bad_docx = root / "bad.docx"
    bad_docx.write_bytes(b"not-docx")
    with pytest.raises(ToolError):
        read_document(str(bad_docx))
    bad_xlsx = root / "bad.xlsx"
    bad_xlsx.write_bytes(b"not-xlsx")
    with pytest.raises(ToolError):
        read_document(str(bad_xlsx))
    bad_pptx = root / "bad.pptx"
    bad_pptx.write_bytes(b"not-pptx")
    with pytest.raises(ToolError):
        read_document(str(bad_pptx))

    note = root / "ok.md"
    note.write_text("hello", encoding="utf-8")
    assert read_document(str(note))["content"]
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("no"))
    )
    with pytest.raises(ToolError):
        read_document(str(note))

    with pytest.raises(ToolError):
        local_list(str(tmp_path / "missing-dir"))
    with pytest.raises(ToolError):
        local_list(str(note))
    with pytest.raises(ToolError):
        local_read(str(tmp_path / "missing.txt"))

    class Done:
        returncode = 0
        stdout = "ok"
        stderr = ""

    from latticeai.tools import commands as command_tools

    monkeypatch.setattr(command_tools.subprocess, "run", lambda *a, **k: Done())
    (root / "notes.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(tools, "AGENT_ROOT", root)
    assert git_diff(path="notes.md")["returncode"] == 0
