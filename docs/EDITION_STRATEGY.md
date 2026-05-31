# Lattice AI — Edition Strategy (Open Core)

This document records the principles behind the Community / Enterprise split so
the boundary stays predictable for contributors and users.

## Editions

- **Community** — this repository, MIT licensed. Local-first **Agentic Workspace
  Platform**: local LLMs, knowledge graph, Personal **and** Organization
  workspaces, role-based membership, snapshots, memory, agents, workflows,
  skills, the auditable timeline, and the full v2.0 platform — **Plugin SDK**,
  **Workflow Designer**, **Multi-Agent Runtime 2.0**, and **Realtime
  Collaboration**. Community is a complete product.
- **Enterprise** — a separately-distributed plugin adding organization-scale
  governance, identity, compliance, and deployment capabilities. Distributed and
  licensed separately. See [ENTERPRISE.md](ENTERPRISE.md).

## Principles

1. **Community is never crippled.** No existing Community feature is removed,
   throttled, or gated to push Enterprise. The capability seam only answers "is
   this *Enterprise* capability available?" — `False` in the open build — and
   never disables a Community code path.
2. **Open-core boundary is a runtime seam, not a fork.** Enterprise attaches via
   `capability_registry.register_provider(...)`
   (`latticeai/core/enterprise.py`). The Community repository contains no
   Enterprise implementation and imports no Enterprise code.
3. **Capabilities are declared, not hidden.** `EnterpriseCapability` enumerates
   reserved capabilities so the contract is visible and stable, even though
   Community implements none of them.
4. **Local-first stays the default.** Enterprise adds options (tenant isolation,
   air-gapped deployment, SIEM export); it does not move the default experience
   off the user's machine.
5. **Graceful by default.** A misbehaving or absent provider must never break a
   Community request; the registry falls back to the Community provider.

## What lives where

| Concern | Community (this repo) | Enterprise (separate) |
|--------|------------------------|------------------------|
| Personal & Organization workspaces | ✅ | — |
| Base roles (owner/admin/member/viewer) | ✅ | — |
| Snapshots / memory / agents / workflows / skills | ✅ | — |
| Plugin SDK (manifest, lifecycle, permission boundary) | ✅ | RBAC/ABAC over plugin permissions |
| Workflow Designer (build/run/run-history) | ✅ | Org approval gates, scheduled triggers |
| Multi-Agent Runtime 2.0 (roles/handoff/retry) | ✅ | Policy-bounded autonomous runs |
| Realtime Collaboration (presence + activity feed) | ✅ | Cross-tenant fan-out, retention |
| Audit timeline (local) | ✅ | — |
| Capability seam & enum | ✅ (declares) | ✅ (implements) |
| SSO/SCIM/IdP provisioning | seam only | ✅ |
| Tenant isolation, compliance retention, eDiscovery | seam only | ✅ |
| SIEM export, DLP, admin policy packs | seam only | ✅ |
| Private VPC / air-gapped deployment | seam only | ✅ |

## Detecting edition at runtime

- `GET /workspace/editions` → edition + per-capability matrix.
- `GET /workspace/os` → `edition` block in the Workspace OS summary.
- `/admin#enterprise` → Admin policy, audit export, SIEM preview,
  organization settings, and capability status UI.
- `GET /admin/enterprise` and `GET /admin/enterprise/siem-export` → descriptive
  Community-safe Enterprise surfaces for integrations.
- `latticeai.core.enterprise.detect_edition()` for in-process checks.

In the Community build all of the above report `community` with every Enterprise
capability `false`.
