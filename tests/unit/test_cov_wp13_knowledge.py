"""wp13 coverage — ``latticeai.tools.knowledge``.

The vault tools carry one security-relevant rule: a request that names a
workspace but no user (or the reverse) must fail rather than silently fall
back to the shared single-user vault. Everything else is file layout — which
folder a note lands in, how a colliding title is disambiguated, what a search
skips. The tests point ``BRAIN_DIR`` at ``tmp_path`` so every write happens in
a throwaway tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.tools import MAX_FILE_BYTES, ToolError
from latticeai.tools import knowledge as knowledge_module
from latticeai.tools.knowledge import (
    knowledge_save,
    knowledge_scope_root,
    knowledge_search,
    knowledge_tree,
    obsidian_save,
    obsidian_search,
    obsidian_tree,
)

_SCOPE = {"workspace_id": "ws-1", "user_email": "Owner@Example.com"}


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the brain directory to a throwaway tree."""
    root = tmp_path / "brain"
    root.mkdir()
    monkeypatch.setattr(knowledge_module, "BRAIN_DIR", root)
    return root


# ── scope resolution ─────────────────────────────────────────────────────────


def test_unscoped_calls_keep_the_legacy_single_user_vault(vault: Path) -> None:
    assert knowledge_scope_root() == vault


def test_scoped_calls_get_a_private_partition(vault: Path) -> None:
    scoped = knowledge_scope_root(**_SCOPE)

    assert vault in scoped.parents
    assert scoped.parts[-3] == ".lattice-scopes"
    assert scoped.parts[-2].startswith("workspace-")
    assert scoped.parts[-1].startswith("user-")


def test_scope_partitions_differ_per_workspace_and_per_user(vault: Path) -> None:
    base = knowledge_scope_root(**_SCOPE)
    other_workspace = knowledge_scope_root(workspace_id="ws-2", user_email=_SCOPE["user_email"])
    other_user = knowledge_scope_root(workspace_id="ws-1", user_email="someone@else.com")

    assert base != other_workspace
    assert base != other_user


def test_email_case_does_not_split_a_users_vault(vault: Path) -> None:
    assert knowledge_scope_root(workspace_id="ws-1", user_email="OWNER@EXAMPLE.COM") == (
        knowledge_scope_root(workspace_id="ws-1", user_email="owner@example.com")
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workspace_id": "ws-1"},
        {"user_email": "owner@example.com"},
        {"workspace_id": "ws-1", "user_email": "   "},
        {"workspace_id": "  ", "user_email": "owner@example.com"},
    ],
)
def test_a_half_scoped_request_fails_closed(vault: Path, kwargs) -> None:
    """A missing identity must never resolve to the shared legacy vault."""
    with pytest.raises(ToolError, match="both workspace_id and user_email"):
        knowledge_scope_root(**kwargs)


# ── knowledge_save ───────────────────────────────────────────────────────────


def test_save_rejects_an_unknown_folder(vault: Path) -> None:
    with pytest.raises(ToolError, match="Unknown knowledge folder"):
        knowledge_save("body", folder="99_Nope")


def test_save_rejects_empty_content(vault: Path) -> None:
    with pytest.raises(ToolError, match="content is required"):
        knowledge_save("", folder="00_Raw")


def test_save_rejects_oversized_content(vault: Path) -> None:
    with pytest.raises(ToolError, match="too large"):
        knowledge_save("x" * (MAX_FILE_BYTES + 1), folder="00_Raw")


def test_save_derives_a_safe_filename_from_the_first_line(vault: Path) -> None:
    result = knowledge_save("Release / notes: v1!\nbody text", folder="10_Wiki")

    assert result["folder"] == "10_Wiki"
    assert result["filename"] == "Release_notes_v1.md"
    written = vault / "10_Wiki" / "Release_notes_v1.md"
    assert written.read_text(encoding="utf-8").startswith("Release / notes")


def test_save_falls_back_to_note_when_the_title_has_no_safe_characters(vault: Path) -> None:
    assert knowledge_save("!!!\nbody", folder="00_Raw")["filename"] == "note.md"


def test_save_never_overwrites_an_existing_note(vault: Path) -> None:
    first = knowledge_save("Daily", folder="40_Log", title="daily")
    second = knowledge_save("Daily again", folder="40_Log", title="daily")
    third = knowledge_save("Daily once more", folder="40_Log", title="daily")

    assert [Path(r["path"]).name for r in (first, second, third)] == [
        "daily.md",
        "daily_2.md",
        "daily_3.md",
    ]
    assert (vault / "40_Log" / "daily.md").read_text(encoding="utf-8") == "Daily"


def test_scoped_saves_land_in_the_scoped_partition(vault: Path) -> None:
    result = knowledge_save("scoped body", folder="00_Raw", title="scoped", **_SCOPE)

    assert not (vault / "00_Raw" / "scoped.md").exists()
    assert Path(result["path"]).parent == knowledge_scope_root(**_SCOPE) / "00_Raw"


# ── knowledge_search ─────────────────────────────────────────────────────────


def test_search_requires_a_query(vault: Path) -> None:
    with pytest.raises(ToolError, match="Query is required"):
        knowledge_search("")


def test_search_matches_content_and_filename(vault: Path) -> None:
    knowledge_save("mentions the needle inside", folder="00_Raw", title="body-hit")
    knowledge_save("nothing relevant", folder="00_Raw", title="needle-in-name")
    knowledge_save("unrelated", folder="00_Raw", title="miss")

    results = knowledge_search("NEEDLE")["results"]
    names = sorted(Path(r["path"]).name for r in results)

    assert names == ["body-hit.md", "needle-in-name.md"]
    assert all(r["relative_path"].startswith("00_Raw") for r in results)
    assert results[0]["preview"]


def test_search_stops_at_max_results(vault: Path) -> None:
    for index in range(6):
        knowledge_save("needle " + str(index), folder="00_Raw", title="note" + str(index))

    assert len(knowledge_search("needle", max_results=2)["results"]) == 2
    # The clamp is applied to the caller's number, not trusted blindly.
    assert len(knowledge_search("needle", max_results=0)["results"]) == 1
    assert len(knowledge_search("needle", max_results=999)["results"]) == 6


def test_search_skips_a_note_that_is_not_valid_utf8(vault: Path) -> None:
    raw = vault / "00_Raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "binary.md").write_bytes(b"\xff\xfe\x00needle")
    (raw / "text.md").write_text("needle in text", encoding="utf-8")

    results = knowledge_search("needle")["results"]

    assert [Path(r["path"]).name for r in results] == ["text.md"]


def test_search_is_confined_to_its_own_scope(vault: Path) -> None:
    knowledge_save("shared needle", folder="00_Raw", title="legacy")
    knowledge_save("scoped needle", folder="00_Raw", title="scoped", **_SCOPE)

    scoped = knowledge_search("needle", **_SCOPE)["results"]

    assert [Path(r["path"]).name for r in scoped] == ["scoped.md"]


# ── knowledge_tree ───────────────────────────────────────────────────────────


def test_tree_creates_every_structure_folder_and_lists_notes(vault: Path) -> None:
    knowledge_save("alpha", folder="20_Skills", title="alpha")
    knowledge_save("beta", folder="00_Raw", title="beta")

    tree = knowledge_tree()

    assert tree["root"] == str(vault)
    for folder in knowledge_module.STRUCTURE:
        assert (vault / folder).is_dir()
    listed = {(e["folder"], Path(e["relative_path"]).name) for e in tree["entries"]}
    assert listed == {("20_Skills", "alpha.md"), ("00_Raw", "beta.md")}
    assert all(e["size"] > 0 for e in tree["entries"])


def test_tree_of_a_fresh_scope_is_empty(vault: Path) -> None:
    knowledge_save("legacy only", folder="00_Raw", title="legacy")

    tree = knowledge_tree(**_SCOPE)

    assert tree["entries"] == []
    assert tree["root"] == str(knowledge_scope_root(**_SCOPE))


# ── the obsidian aliases ─────────────────────────────────────────────────────


def test_obsidian_save_adds_vault_root_and_uri_hint(vault: Path) -> None:
    result = obsidian_save("body", folder="30_Projects", title="plan", **_SCOPE)

    assert result["vault_root"] == str(knowledge_scope_root(**_SCOPE))
    assert result["obsidian_uri_hint"] == "obsidian://open?path=" + result["path"]
    assert Path(result["path"]).read_text(encoding="utf-8") == "body"


def test_obsidian_search_wraps_knowledge_search(vault: Path) -> None:
    obsidian_save("needle here", folder="00_Raw", title="hit")

    result = obsidian_search("needle")

    assert result["vault_root"] == str(vault)
    assert [Path(r["path"]).name for r in result["results"]] == ["hit.md"]


def test_obsidian_tree_is_the_knowledge_tree(vault: Path) -> None:
    obsidian_save("body", folder="10_Wiki", title="wiki")

    assert obsidian_tree() == knowledge_tree()
