"""wp06: the invitation router's guards, not just its happy path.

``tests/unit/test_identity_policy_invitations.py`` already proves an invitation
can be created and accepted. What was never exercised is every way the router
says *no*: listing (admin-only), the three failure modes of the
``manage_members`` pre-check on a targeted workspace, an unknown role, an
anonymous acceptor, a bad/expired token, and a workspace join that fails after
the token was already consumed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.invitations import create_invitations_router
from latticeai.core.invitations import InvitationStore
from latticeai.core.users import stable_user_id, user_id_for_email
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.workspace_service import WorkspaceService

ADMIN = "admin@example.com"
OUTSIDER = "outsider@example.com"


class InvitationHarness:
    """Real InvitationStore + WorkspaceService over ``tmp_path``, fake auth."""

    def __init__(self, tmp_path: Path) -> None:
        self.users: Dict[str, Any] = {
            ADMIN: {"id": stable_user_id(ADMIN), "role": "admin"},
            OUTSIDER: {"id": stable_user_id(OUTSIDER), "role": "user"},
        }
        self.store = WorkspaceOSStore(tmp_path / "data")
        self.service = WorkspaceService(self.store, resolve_user_id=self.user_id)
        self.invitations = InvitationStore(tmp_path / "invitations.json")
        self.audit: List[Tuple[str, Dict[str, Any]]] = []
        # Mutable so a test can change who is calling without rebuilding the app.
        self.admin_email: str = ADMIN
        self.acting_email: str = OUTSIDER

        app = FastAPI()
        app.include_router(create_invitations_router(
            invitation_store=self.invitations,
            workspace_service=self.service,
            require_admin=lambda _request: (self.admin_email, self.users),
            require_user=lambda _request: self.acting_email,
            user_id_for_email=self.user_id,
            append_audit_event=lambda event_type, **payload: self.audit.append((event_type, payload)),
        ))
        self.client = TestClient(app)

    def user_id(self, email: Optional[str]) -> Optional[str]:
        return user_id_for_email(self.users, email)

    def org(self, name: str = "Acme", owner: str = ADMIN) -> str:
        record = self.service.create_organization_workspace(
            name=name, owner_user_id=owner, settings={}
        )
        return str(record["workspace_id"])


def test_listing_invitations_is_admin_only_and_shows_pending_records(tmp_path: Path):
    harness = InvitationHarness(tmp_path)
    created = harness.client.post("/invitations", json={"email": OUTSIDER}).json()

    listed = harness.client.get("/invitations")

    assert listed.status_code == 200
    records = listed.json()["invitations"]
    assert [item["id"] for item in records] == [created["invitation"]["id"]]
    assert records[0]["status"] == "pending"
    # The stored record never echoes the raw token back through the list view.
    assert "token" not in records[0]


def test_targeting_a_workspace_reports_missing_wrong_type_and_forbidden(tmp_path: Path):
    harness = InvitationHarness(tmp_path)
    org_id = harness.org()

    missing = harness.client.post("/invitations", json={"workspace_id": "org-ghost"})
    personal = harness.client.post("/invitations", json={"workspace_id": "personal"})
    harness.admin_email = OUTSIDER
    forbidden = harness.client.post("/invitations", json={"workspace_id": org_id})

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Workspace not found: org-ghost"
    assert personal.status_code == 400
    assert "organization workspaces" in personal.json()["detail"]
    assert forbidden.status_code == 403
    assert "manage_members" in forbidden.json()["detail"]
    # None of the rejected requests created an invitation or an audit event.
    assert harness.invitations.list() == []
    assert harness.audit == []


def test_unknown_invitation_role_is_refused_before_the_token_is_minted(tmp_path: Path):
    harness = InvitationHarness(tmp_path)

    response = harness.client.post("/invitations", json={"email": OUTSIDER, "role": "superuser"})

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown invitation role"
    assert harness.invitations.list() == []


def test_accepting_requires_an_identified_user(tmp_path: Path):
    harness = InvitationHarness(tmp_path)
    token = harness.client.post("/invitations", json={}).json()["invitation"]["token"]
    harness.acting_email = ""

    response = harness.client.post(f"/invitations/{token}/accept")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
    assert harness.invitations.list()[0]["status"] == "pending"


def test_accept_reports_unknown_tokens_and_refuses_a_second_use(tmp_path: Path):
    harness = InvitationHarness(tmp_path)
    token = harness.client.post("/invitations", json={}).json()["invitation"]["token"]

    unknown = harness.client.post("/invitations/not-a-token/accept")
    first = harness.client.post(f"/invitations/{token}/accept")
    second = harness.client.post(f"/invitations/{token}/accept")

    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Invitation not found"
    assert first.status_code == 200
    assert first.json()["invitation"]["status"] == "accepted"
    assert second.status_code == 403
    assert second.json()["detail"] == "invitation is accepted"
    assert [event[0] for event in harness.audit] == ["invitation_created", "invitation_accepted"]


def test_a_join_that_fails_after_the_token_is_consumed_surfaces_as_conflict(tmp_path: Path):
    harness = InvitationHarness(tmp_path)
    # Minted straight through the store so the router's create-time
    # ``manage_members`` pre-check cannot reject the dangling workspace first.
    invitation = harness.invitations.create(
        email=None,
        workspace_id="org-vanished",
        role="member",
        created_by=harness.user_id(ADMIN),
    )

    response = harness.client.post(f"/invitations/{invitation['token']}/accept")

    assert response.status_code == 409
    assert "org-vanished" in response.json()["detail"]
    # The token is spent even though the join failed — the caller must be told,
    # which is exactly why this maps to 409 rather than a silent success.
    assert harness.invitations.list()[0]["status"] == "accepted"
    assert harness.audit == []
