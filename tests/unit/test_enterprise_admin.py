"""Tests for the Enterprise PoC surfaces.

The open-core invariant: in the Community build every Enterprise capability is
disabled, nothing is enforced, and Community-local behaviour (audit export, base
roles, single-tenant storage) is always reported as available.
"""

import pytest

from latticeai.core import enterprise_admin
from latticeai.core.enterprise import (
    Edition,
    EnterpriseCapability,
    capability_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    capability_registry.reset()
    yield
    capability_registry.reset()


def test_community_admin_policies_not_enforced():
    policies = enterprise_admin.admin_policies()
    assert policies["enabled"] is False
    assert policies["enforced"] is False
    assert "owner" in policies["effective_policy"]["base_roles"]


def test_community_local_audit_export_always_available():
    desc = enterprise_admin.audit_export_descriptor()
    assert desc["local_export"]["available"] is True
    assert "/admin/security/export" == desc["local_export"]["endpoint"]
    # SIEM streaming is an Enterprise capability — off in Community.
    assert desc["siem_streaming"]["enabled"] is False


def test_siem_export_is_stub_in_community():
    stub = enterprise_admin.siem_export_stub()
    assert stub["enabled"] is False
    assert stub["streamed"] is False
    assert stub["destination"] is None
    # The envelope *shape* is still exposed for integrators.
    assert stub["preview_envelope"]["format"] == "ltcai.siem.v1"
    assert isinstance(stub["preview_envelope"]["records"], list)


def test_organization_settings_governance_all_disabled():
    org = enterprise_admin.organization_settings()
    assert all(v is False for v in org["governance_capabilities"].values())
    assert "personal" in org["community_baseline"]["workspaces"]
    assert "organization" in org["community_baseline"]["workspaces"]


def test_poc_overview_reports_community_edition():
    overview = enterprise_admin.poc_overview()
    assert overview["edition"]["edition"] == Edition.COMMUNITY.value
    assert overview["edition"]["is_enterprise"] is False
    for key in ("admin_policies", "audit_export", "siem_export", "organization_settings"):
        assert key in overview


def test_enterprise_provider_lights_up_capabilities():
    """A registered Enterprise provider flips capabilities on — proving the seam
    is real, not hard-coded to disabled."""

    class _Provider:
        def edition(self):
            return Edition.ENTERPRISE

        def is_enabled(self, capability):
            return capability == EnterpriseCapability.SIEM_EXPORT

    capability_registry.register_provider(_Provider())
    stub = enterprise_admin.siem_export_stub()
    assert stub["enabled"] is True
    assert stub["streamed"] is True
