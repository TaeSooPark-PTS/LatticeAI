"""Enterprise PoC surfaces (admin policies, audit export, SIEM stub, org settings).

This module is **structure only** — it prepares concrete, discoverable shapes for
Enterprise governance features while keeping the open-source Community edition
fully functional and ungated. Every capability here is consulted through
:data:`latticeai.core.enterprise.capability_registry`; in the Community build
each is reported ``enabled=False`` and the Community behaviour (local audit
export, the four base roles, single-tenant local storage) is always available.

Nothing in this module restricts a Community feature. It answers "what *would*
an Enterprise provider light up, and is it active?" so the admin UI can show an
honest edition/capability matrix and a SIEM export *preview envelope* without
shipping any Enterprise implementation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from latticeai.core.enterprise import (
    EnterpriseCapability,
    capability_registry,
)

COMMUNITY_NOTICE = (
    "Community edition: this is an Enterprise extension point and is not "
    "enforced. Local-first behaviour is always available. See "
    "docs/ENTERPRISE.md and docs/EDITION_STRATEGY.md."
)


def _cap(capability: EnterpriseCapability) -> bool:
    return capability_registry.is_capability_enabled(capability)


def admin_policies() -> Dict[str, Any]:
    """Admin policy-pack status + the effective (open) Community policy."""
    enabled = _cap(EnterpriseCapability.ADMIN_POLICY_PACKS)
    return {
        "capability": EnterpriseCapability.ADMIN_POLICY_PACKS.value,
        "enabled": enabled,
        "enforced": enabled,
        "effective_policy": {
            # Community defaults — descriptive, not enforced by a policy engine.
            "base_roles": ["owner", "admin", "member", "viewer"],
            "local_file_access": "approval-token gated (per path/user/action)",
            "package_install": "admin-only with audit trail",
            "network_binding": "127.0.0.1 by default",
            "managed_policy_packs": [] if not enabled else "provided-by-enterprise",
        },
        "note": COMMUNITY_NOTICE,
    }


def audit_export_descriptor() -> Dict[str, Any]:
    """What audit export is available locally vs. via Enterprise SIEM streaming."""
    siem_enabled = _cap(EnterpriseCapability.SIEM_EXPORT)
    retention_enabled = _cap(EnterpriseCapability.COMPLIANCE_RETENTION)
    return {
        "local_export": {
            "available": True,
            "endpoint": "/admin/security/export",
            "formats": ["json", "csv", "xlsx", "txt", "pdf"],
            "note": "Community local audit export is always available to admins.",
        },
        "siem_streaming": {
            "capability": EnterpriseCapability.SIEM_EXPORT.value,
            "enabled": siem_enabled,
            "note": COMMUNITY_NOTICE,
        },
        "compliance_retention": {
            "capability": EnterpriseCapability.COMPLIANCE_RETENTION.value,
            "enabled": retention_enabled,
            "note": COMMUNITY_NOTICE,
        },
    }


def siem_export_stub(events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """A preview of the envelope an Enterprise SIEM exporter would emit.

    In the Community build this is a *stub*: it returns the envelope *shape*
    (so integrators can see the contract) but ``streamed=False`` and no events
    are actually pushed to an external SIEM.
    """
    enabled = _cap(EnterpriseCapability.SIEM_EXPORT)
    sample = events or [
        {
            "id": "evt_sample",
            "type": "audit_event",
            "timestamp": "1970-01-01T00:00:00Z",
            "actor": "admin@example.com",
            "action": "model_load",
            "severity": "informational",
        }
    ]
    envelope = {
        "format": "ltcai.siem.v1",
        "encoding": "ndjson",
        "vendor": "LatticeAI",
        "product": "Workspace OS",
        "records": [
            {
                "ts": e.get("timestamp"),
                "actor": e.get("actor"),
                "act": e.get("action"),
                "sev": e.get("severity", "informational"),
                "kind": e.get("type"),
                "id": e.get("id"),
            }
            for e in sample
        ],
    }
    return {
        "capability": EnterpriseCapability.SIEM_EXPORT.value,
        "enabled": enabled,
        "streamed": False if not enabled else True,
        "destination": None if not enabled else "configured-by-enterprise",
        "preview_envelope": envelope,
        "note": COMMUNITY_NOTICE,
    }


def organization_settings() -> Dict[str, Any]:
    """Org-scale governance capabilities and their (Community=off) state."""
    governance_caps = [
        EnterpriseCapability.TENANT_ISOLATION,
        EnterpriseCapability.RBAC_ABAC_ADVANCED,
        EnterpriseCapability.SCIM,
        EnterpriseCapability.IDP_PROVISIONING,
        EnterpriseCapability.SSO_ADVANCED,
        EnterpriseCapability.DLP_POLICY,
        EnterpriseCapability.EDISCOVERY,
        EnterpriseCapability.PRIVATE_VPC,
        EnterpriseCapability.AIR_GAPPED_DEPLOYMENT,
    ]
    return {
        "community_baseline": {
            "workspaces": ["personal", "organization"],
            "roles": ["owner", "admin", "member", "viewer"],
            "data_isolation": "single-tenant local storage (~/.ltcai)",
        },
        "governance_capabilities": {
            cap.value: _cap(cap) for cap in governance_caps
        },
        "note": COMMUNITY_NOTICE,
    }


def poc_overview() -> Dict[str, Any]:
    """Combined Enterprise PoC surface for the admin dashboard."""
    return {
        "edition": capability_registry.describe(),
        "admin_policies": admin_policies(),
        "audit_export": audit_export_descriptor(),
        "siem_export": siem_export_stub(),
        "organization_settings": organization_settings(),
    }
