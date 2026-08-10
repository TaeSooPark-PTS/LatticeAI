"""v11.0.1 D10 + D11: the workspace router says only what it means.

D10 — ``POST /workspace/vscode/status`` used to build
``{"status": "ok", **_VSCODE_STATUS}``. The spread always overwrote the "ok"
literal, so the wire contract has always been "``status`` is the state the
extension reported". The literal was dead; these tests pin the contract that
survived it, byte for byte.

D11 — ``GET /workspace/orgs/{id}`` and ``.../summary`` carried an
``except FileNotFoundError -> 404`` arm that no request could reach: the
service checks ``read`` permission *before* the lookup, and an unknown
workspace has no members, so it is refused with ``PermissionError`` first.
The tests below prove that ordering from the service side *and* through the
HTTP surface, which is what makes removing the arm safe: an unknown id is
answered 403, deliberately indistinguishable from a real workspace the caller
cannot read (anti-enumeration).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latticeai.api import workspace as workspace_api
from tests.unit.test_cov_wp06_workspace_router import (
    OWNER,
    STRANGER,
    WorkspaceHarness,
)

# ── D10: the VS Code presence POST echoes stored state, nothing else ────────


def test_vscode_status_post_returns_exactly_the_stored_presence_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    harness = WorkspaceHarness(tmp_path)
    stored: dict = {
        "connected": False, "status": "offline", "index_status": "unknown", "last_seen_ms": 0,
    }
    monkeypatch.setattr(workspace_api, "_VSCODE_STATUS", stored)

    posted = harness.client.post("/workspace/vscode/status", json={
        "status": "indexing", "index_status": "building", "workspace_folder": "/repo",
        "extension_version": "2.0.0", "active_file": "main.py", "detail": "1/9 files",
    }).json()

    # Same keys, same values — the response IS the record, not a wrapper.
    assert posted == stored
    assert set(posted) == {
        "connected", "status", "index_status", "last_seen_ms", "workspace_folder",
        "extension_version", "active_file", "detail", "user_email",
    }
    # The reported status is the extension's, and "ok" is never emitted.
    assert posted["status"] == "indexing"
    assert "ok" not in posted.values()


def test_vscode_status_post_defaults_the_reported_status_when_none_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    harness = WorkspaceHarness(tmp_path)
    stored: dict = {
        "connected": False, "status": "offline", "index_status": "unknown", "last_seen_ms": 0,
    }
    monkeypatch.setattr(workspace_api, "_VSCODE_STATUS", stored)

    posted = harness.client.post("/workspace/vscode/status", json={}).json()

    assert posted["status"] == "connected"
    assert posted["index_status"] == "unknown"
    assert posted["user_email"] == OWNER
    assert posted == stored


def test_the_vscode_status_response_is_a_copy_not_the_live_module_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A later report must not retroactively rewrite an earlier response."""
    harness = WorkspaceHarness(tmp_path)
    monkeypatch.setattr(workspace_api, "_VSCODE_STATUS", {
        "connected": False, "status": "offline", "index_status": "unknown", "last_seen_ms": 0,
    })

    first = harness.client.post("/workspace/vscode/status", json={"status": "indexing"}).json()
    second = harness.client.post("/workspace/vscode/status", json={"status": "ready"}).json()

    assert first["status"] == "indexing"
    assert second["status"] == "ready"


# ── D11: unknown workspace ids are refused, never reported as missing ───────


def test_the_service_refuses_an_unknown_workspace_before_it_ever_looks_it_up(
    tmp_path: Path,
):
    """The premise behind removing the 404 arm, asserted at the service seam.

    ``store.get_workspace``/``workspace_summary`` are the only raisers of
    ``FileNotFoundError``; if the permission gate ran first they are never
    called at all. This test proves the ordering rather than trusting it.
    """
    harness = WorkspaceHarness(tmp_path)
    reached: list = []

    def _tripwire(*args, **kwargs):
        reached.append(args)
        raise AssertionError("permission gate must run before the lookup")

    harness.store.get_workspace = _tripwire  # type: ignore[method-assign]
    harness.store.workspace_summary = _tripwire  # type: ignore[method-assign]

    with pytest.raises(PermissionError, match="lacks 'read'"):
        harness.service.get_workspace("org-ghost", OWNER)
    with pytest.raises(PermissionError, match="lacks 'read'"):
        harness.service.workspace_summary("org-ghost", OWNER)

    assert reached == []


@pytest.mark.parametrize("path", ["/workspace/orgs/%s", "/workspace/orgs/%s/summary"])
def test_an_unknown_workspace_is_indistinguishable_from_one_you_cannot_read(
    tmp_path: Path, path: str
):
    harness = WorkspaceHarness(tmp_path)
    workspace_id = harness.org()

    harness.user = STRANGER
    unknown = harness.client.get(path % "org-ghost")
    forbidden = harness.client.get(path % workspace_id)

    # Same status and same detail shape: an outsider cannot learn from the
    # response whether "org-ghost" exists on this install.
    assert unknown.status_code == forbidden.status_code == 403
    assert "lacks 'read'" in unknown.json()["detail"]
    assert "lacks 'read'" in forbidden.json()["detail"]
    assert "not found" not in unknown.json()["detail"].lower()


def test_only_the_two_gated_read_routes_lost_their_404_arm(tmp_path: Path):
    """``activate``/``update``/``archive`` reach the store before any gate, so
    ``FileNotFoundError`` is genuinely reachable there and stays mapped to 404.
    Removing the dead arm must not have widened into those routes.
    """
    harness = WorkspaceHarness(tmp_path)

    activate = harness.client.post("/workspace/activate", json={"workspace_id": "org-ghost"})
    update = harness.client.patch("/workspace/orgs/org-ghost", json={"name": "x"})
    archive = harness.client.post("/workspace/orgs/org-ghost/archive")
    read = harness.client.get("/workspace/orgs/org-ghost")
    summary = harness.client.get("/workspace/orgs/org-ghost/summary")

    assert [activate.status_code, update.status_code, archive.status_code] == [404, 404, 404]
    assert "Workspace not found" in activate.json()["detail"]
    # ...while the two gated read routes answer 403 for that very same id.
    assert [read.status_code, summary.status_code] == [403, 403]
