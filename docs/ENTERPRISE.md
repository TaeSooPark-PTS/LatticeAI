# Lattice AI — Enterprise Edition

> **Status:** Foundation / extension seam only. The open-source Community
> edition in this repository ships **none** of the capabilities below enabled,
> and contains **no** Enterprise implementation. This document describes the
> boundary and the roadmap, not shipped code.

Lattice AI follows an **open-core** model:

- **Community** (this repository, MIT) is fully functional on its own: local
  LLMs, knowledge graph, Personal and Organization workspaces, roles, snapshots,
  memory, agents, workflows, skills, the auditable timeline, and the full v2.0
  Agentic Workspace Platform (Plugin SDK, Workflow Designer, Multi-Agent Runtime
  2.0, Realtime Collaboration).
- **Enterprise** is a separately-distributed plugin that attaches advanced,
  organization-scale governance and deployment capabilities through a stable
  runtime seam. It is never bundled into the Community build.

See [EDITION_STRATEGY.md](EDITION_STRATEGY.md) for the principles that keep this
boundary honest (Community is never crippled to upsell Enterprise).

## The extension seam

The seam lives in [`latticeai/core/enterprise.py`](../latticeai/core/enterprise.py):

- `Edition` — `community` (default) or `enterprise`.
- `EnterpriseCapability` — the enum of reserved capabilities (below).
- `CapabilityProvider` — the protocol an Enterprise plugin implements.
- `capability_registry` — a process-wide registry. An Enterprise plugin calls
  `capability_registry.register_provider(...)` at startup. Until then, the
  default `CommunityCapabilityProvider` answers every capability query with
  "Community / disabled".

Community code consults the seam at extension points via
`is_capability_enabled(EnterpriseCapability.X)`. In the Community build this is
always `False`, so the Community code path is taken and nothing is gated off.

The live edition + capability matrix is exposed at `GET /workspace/editions`,
surfaced in the Workspace OS summary (`GET /workspace/os` → `edition`), and
shown in the Enterprise Admin UI at `/admin#enterprise`.

Community also exposes descriptive admin surfaces at `GET /admin/enterprise`
and `GET /admin/enterprise/siem-export`: they show policy/export/envelope
shapes while reporting Enterprise-only capabilities disabled and never
streaming external events.

## Enterprise capability roadmap

These are declared in `EnterpriseCapability` so the seam is stable and
discoverable. None are implemented in Community.

| Capability | Enum | Summary |
|-----------|------|---------|
| Advanced SSO | `SSO_ADVANCED` | Enforced SSO, session policy, SAML/OIDC federation |
| IdP provisioning | `IDP_PROVISIONING` | Entra ID / Okta user & group provisioning |
| SCIM | `SCIM` | SCIM 2.0 user/group lifecycle sync |
| Advanced RBAC/ABAC | `RBAC_ABAC_ADVANCED` | Custom roles, attribute-based policy beyond the 4 base roles |
| Tenant isolation | `TENANT_ISOLATION` | Hard multi-tenant data isolation |
| Compliance retention | `COMPLIANCE_RETENTION` | Legal-hold, retention windows, immutable audit |
| SIEM export | `SIEM_EXPORT` | Streaming audit/event export to Splunk/Sentinel/etc. |
| Private VPC | `PRIVATE_VPC` | Private-network / customer-VPC deployment controls |
| Air-gapped deployment | `AIR_GAPPED_DEPLOYMENT` | Fully offline install & update channel |
| DLP policy | `DLP_POLICY` | Data-loss-prevention scanning & enforcement |
| eDiscovery | `EDISCOVERY` | Search, hold, and export for legal discovery |
| Admin policy packs | `ADMIN_POLICY_PACKS` | Centrally-managed org policy bundles |

## How an Enterprise plugin attaches (illustrative)

```python
from latticeai.core.enterprise import (
    Edition, EnterpriseCapability, capability_registry,
)

class EnterpriseProvider:
    def edition(self) -> Edition:
        return Edition.ENTERPRISE

    def is_enabled(self, capability: EnterpriseCapability) -> bool:
        return capability in MY_LICENSED_CAPABILITIES

# Enterprise package startup:
capability_registry.register_provider(EnterpriseProvider())
```

No Enterprise code or credentials live in this repository; the Community build
imports none of the above.
