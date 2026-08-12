"""Enterprise extension seam (open-core boundary).

LatticeAI Community is fully functional on its own. This module defines the
*seam* where a future, separately-distributed Enterprise plugin can attach
advanced capabilities (SSO provisioning, SCIM, tenant isolation, compliance
retention, SIEM export, and so on) **without** any of that code living in the
public Community repository.

Design rules enforced here:

* Community is the default :class:`Edition`. Every :class:`EnterpriseCapability`
  is **disabled** unless an Enterprise provider is explicitly registered.
* Nothing in this module restricts or gates a Community feature. It only answers
  "is this *Enterprise* capability available?" — which is ``False`` in the open
  build.
* Enterprise behaviour is supplied at runtime through
  :class:`CapabilityProvider` implementations registered on the shared
  :data:`capability_registry`. The public code never imports Enterprise code.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Dict, Protocol, runtime_checkable


class Edition(str, Enum):
    """Distribution edition. Community is the open-source default."""

    COMMUNITY = "community"
    ENTERPRISE = "enterprise"


class EnterpriseCapability(str, Enum):
    """Capabilities reserved for the Enterprise edition.

    These are documented in ``docs/ENTERPRISE.md``. They are intentionally
    declared (so the seam is stable and discoverable) but never implemented in
    the Community build.
    """

    SSO_ADVANCED = "sso_advanced"
    IDP_PROVISIONING = "idp_provisioning"  # Entra ID / Okta provisioning
    SCIM = "scim"
    RBAC_ABAC_ADVANCED = "rbac_abac_advanced"
    TENANT_ISOLATION = "tenant_isolation"
    COMPLIANCE_RETENTION = "compliance_retention"
    SIEM_EXPORT = "siem_export"
    PRIVATE_VPC = "private_vpc"
    AIR_GAPPED_DEPLOYMENT = "air_gapped_deployment"
    DLP_POLICY = "dlp_policy"
    EDISCOVERY = "ediscovery"
    ADMIN_POLICY_PACKS = "admin_policy_packs"
    # Human review-before-promote governance for knowledge-graph concept
    # promotions (review 2026-07-25 Wave 4). Community users can opt in per
    # deployment via LATTICEAI_GRAPH_PROMOTION_REVIEW; this capability is the
    # seam for Enterprise policy packs that mandate reviewed promotions.
    GRAPH_PROMOTION_REVIEW = "graph_promotion_review"


@runtime_checkable
class CapabilityProvider(Protocol):
    """Contract an Enterprise plugin implements to light up capabilities.

    A provider is registered at runtime via
    :meth:`CapabilityRegistry.register_provider`. The Community build ships no
    provider, so :meth:`is_enabled` is effectively ``False`` everywhere.
    """

    def edition(self) -> Edition:
        ...

    def is_enabled(self, capability: EnterpriseCapability) -> bool:
        ...


class CommunityCapabilityProvider:
    """Default provider: Community edition, no Enterprise capabilities."""

    def edition(self) -> Edition:
        return Edition.COMMUNITY

    def is_enabled(self, capability: EnterpriseCapability) -> bool:  # noqa: ARG002
        return False


class CapabilityRegistry:
    """Runtime seam that an Enterprise plugin can attach a provider to.

    The registry holds at most one active provider. Until an Enterprise build
    registers one, the :class:`CommunityCapabilityProvider` answers every query
    with "Community / disabled".
    """

    def __init__(self) -> None:
        self._provider: CapabilityProvider = CommunityCapabilityProvider()

    def register_provider(self, provider: CapabilityProvider) -> None:
        if not isinstance(provider, CapabilityProvider):
            raise TypeError("provider must implement the CapabilityProvider protocol")
        self._provider = provider

    def reset(self) -> None:
        """Restore the Community default provider (used by tests)."""
        self._provider = CommunityCapabilityProvider()

    def edition(self) -> Edition:
        return self._provider.edition()

    def is_capability_enabled(self, capability: EnterpriseCapability) -> bool:
        try:
            return bool(self._provider.is_enabled(capability))
        except Exception:
            # A misbehaving plugin must never break a Community request.
            return False

    def describe(self) -> Dict[str, object]:
        """Edition + capability matrix for ``/workspace/editions`` and admin UI."""
        return {
            "edition": self.edition().value,
            "is_enterprise": self.edition() is Edition.ENTERPRISE,
            "capabilities": {
                cap.value: self.is_capability_enabled(cap) for cap in EnterpriseCapability
            },
            "community_notice": (
                "All listed capabilities are Enterprise-only extension points. "
                "The open-source Community edition ships none of them enabled; "
                "see docs/ENTERPRISE.md and docs/EDITION_STRATEGY.md."
            ),
        }


# Shared singleton seam. Enterprise plugins import this and call
# ``capability_registry.register_provider(...)`` during their own startup.
capability_registry = CapabilityRegistry()


def detect_edition() -> Edition:
    """Best-effort edition detection.

    Honours an explicit ``LATTICE_EDITION=enterprise`` opt-in for environments
    that have separately installed an Enterprise provider, but otherwise the
    registry's active provider is the source of truth.
    """
    env = os.environ.get("LATTICE_EDITION", "").strip().lower()
    if env == Edition.ENTERPRISE.value and capability_registry.edition() is Edition.ENTERPRISE:
        return Edition.ENTERPRISE
    return capability_registry.edition()


def is_capability_enabled(capability: EnterpriseCapability) -> bool:
    """Module-level convenience used by Community code at extension points."""
    return capability_registry.is_capability_enabled(capability)
