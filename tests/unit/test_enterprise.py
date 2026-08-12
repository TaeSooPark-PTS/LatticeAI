"""Tests for the open-core Enterprise extension seam."""

import pytest

from latticeai.core.enterprise import (
    CapabilityRegistry,
    Edition,
    EnterpriseCapability,
    capability_registry,
    detect_edition,
    is_capability_enabled,
)


def test_community_is_default_and_disables_all_capabilities():
    registry = CapabilityRegistry()
    assert registry.edition() is Edition.COMMUNITY
    for capability in EnterpriseCapability:
        assert registry.is_capability_enabled(capability) is False


def test_shared_registry_default_is_community():
    # The process-wide seam ships Community with nothing enabled.
    assert capability_registry.edition() is Edition.COMMUNITY
    assert is_capability_enabled(EnterpriseCapability.SCIM) is False
    assert detect_edition() is Edition.COMMUNITY


def test_describe_lists_every_capability_disabled():
    described = CapabilityRegistry().describe()
    assert described["edition"] == "community"
    assert described["is_enterprise"] is False
    caps = described["capabilities"]
    assert set(caps) == {c.value for c in EnterpriseCapability}
    assert all(value is False for value in caps.values())


def test_enterprise_provider_can_attach_via_seam():
    registry = CapabilityRegistry()

    class FakeEnterprise:
        def edition(self):
            return Edition.ENTERPRISE

        def is_enabled(self, capability):
            return capability is EnterpriseCapability.SCIM

    registry.register_provider(FakeEnterprise())
    assert registry.edition() is Edition.ENTERPRISE
    assert registry.is_capability_enabled(EnterpriseCapability.SCIM) is True
    assert registry.is_capability_enabled(EnterpriseCapability.DLP_POLICY) is False

    registry.reset()
    assert registry.edition() is Edition.COMMUNITY


def test_misbehaving_provider_never_breaks_community_path():
    registry = CapabilityRegistry()

    class BrokenProvider:
        def edition(self):
            return Edition.ENTERPRISE

        def is_enabled(self, capability):
            raise RuntimeError("boom")

    registry.register_provider(BrokenProvider())
    # A raising provider must be treated as "capability disabled", not crash.
    assert registry.is_capability_enabled(EnterpriseCapability.SIEM_EXPORT) is False


def test_invalid_provider_rejected():
    registry = CapabilityRegistry()
    with pytest.raises(TypeError):
        registry.register_provider(object())
