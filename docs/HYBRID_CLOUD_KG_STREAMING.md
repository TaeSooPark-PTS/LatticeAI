# Hybrid Cloud + Local Knowledge Graph Streaming

> Status: **Phase 1 scaffolding live** on branch `feature/hybrid-cloud-kg-streaming-phase1`  
> Base design branch: `feature/hybrid-cloud-kg-streaming`  
> Principle: **Local Brain is the asset. Cloud is an opt-in worker.**

## Goal

Allow users to optionally use a cloud LLM in a **streaming** conversation while keeping the durable Knowledge Graph local.

Core loop:

1. User speaks / types.
2. System detects whether the session is in **Cloud-allowed** mode.
3. From the local Knowledge Graph, extract **only the minimal related nodes** that match the user’s keywords / entities / intent.
4. Send that minimal context to the cloud LLM (token minimization + privacy).
5. Receive the answer as a **real-time stream**.
6. Use the streamed answer (plus the nodes that were sent) to **expand the local Knowledge Graph** with clear provenance.

Local-only users never leave the machine. Cloud users still grow their local Brain.

## User-facing Modes

| Mode | Default | Network | What is sent | KG expansion |
|------|---------|---------|--------------|--------------|
| `local_only` | **Yes** | None | Nothing | Local model answers only |
| `cloud_allowed` | No (explicit opt-in) | Opt-in cloud LLM | Minimal related nodes + compact summary | Cloud response is staged into local KG with provenance |

Switching to `cloud_allowed` requires an explicit acknowledgement (same pattern as PermissionMode.bypass).

## Architecture Overview

```
User message
    │
    ▼
NetworkBoundaryMode  ─────── local_only → normal local chat path
    │ cloud_allowed
    ▼
MinimalContextExtractor
    • keyword / entity / intent extraction
    • hybrid_search on local KG (top-k, type filters, sensitivity filter)
    • compact context assembly (title + short summary + node ids)
    ▼
CloudStreamingBridge + OpenAICompatibleAdapter
    • send minimal context + user message
    • receive token / chunk stream (SSE)
    • emit intermediate events to UI
    ▼
CloudResponseIngestor
    • turn streamed answer into candidate nodes / edges
    • attach provenance: "derived_from_cloud" + source_node_ids that were sent
    • quality gate / proposal (auto_commit=False by default)
    • stage into local Knowledge Graph
```

## Phase status

### Phase 0 (design branch)
- Architecture document
- `NetworkBoundaryMode` enum + contract
- `MinimalContext` + extractor skeleton
- `CloudStreamingBridge` + `CloudResponseIngestor` interfaces

### Phase 1 (this branch) — implemented
- **NetworkBoundaryService** — persisted dial (user / workspace / default), ack required for cloud
- **HTTP API** — `GET/POST /api/network-boundary`, catalog endpoint
- **Wiring** — `latticeai/runtime/network_boundary_wiring.py` (mirror of permission_mode_wiring)
- **ChatRequest.network_mode** — optional per-request override
- **OpenAICompatibleAdapter** — real streaming via `openai` SDK; env-configured (`LATTICEAI_CLOUD_API_KEY`, `LATTICEAI_CLOUD_BASE_URL`, `LATTICEAI_CLOUD_MODEL`)
- **hybrid_chat** — `run_hybrid_cloud_turn` + `stream_hybrid_cloud_turn` (SSE: hybrid_context → token* → hybrid_done)
- **KG expansion** — conversation node + `grounded_on` edges; staged (`auto_commit=False`)
- **Unit tests** — `tests/unit/test_network_boundary.py`

### Phase 2 (next)
- Wire hybrid path as a first-class branch inside `/chat` when mode is cloud_allowed
- UI mode switch + transparency panel (nodes about to be sent)
- Token accounting / cost guardrails
- Richer concept/decision extraction from cloud answers

### Phase 3
- Optional video / multimodal streaming (same boundary)
- User-configurable sensitivity filters and auto-commit policy

## Configuration (Phase 1)

```bash
# Required only when using cloud path
export LATTICEAI_CLOUD_API_KEY=sk-...
# Optional
export LATTICEAI_CLOUD_BASE_URL=https://api.openai.com/v1
export LATTICEAI_CLOUD_MODEL=gpt-4o-mini
# Process default network mode (still requires per-user ack to switch to cloud)
export LATTICEAI_NETWORK_MODE=local_only
```

## API (Phase 1)

```
GET  /api/network-boundary
GET  /api/network-boundary/catalog
POST /api/network-boundary
     { "mode": "cloud_allowed", "acknowledge_risk": true, "workspace_id": null }
```

## Privacy & Safety Rules

1. Default is always `local_only`.
2. `cloud_allowed` requires explicit user acknowledgement.
3. Only the compact text assembled by MinimalContextExtractor leaves the machine.
4. Sensitive node types / tags are blocked by a hard filter (mode-invariant).
5. Every cloud turn records which node_ids were sent and stages expansion with provenance.
6. User can later purge all “derived_from_cloud” subgraphs.

## Non-Goals (for now)

- Making cloud the default path
- Sending the entire graph or long conversation history
- Automatic background cloud calls without user intent
- Replacing the local model runtime

## Success Criteria

- A user in `local_only` never generates an outbound network call for chat.
- A user in `cloud_allowed` can see exactly which nodes left the machine.
- Cloud answers produce durable, provenance-linked growth in the local Knowledge Graph.
- Token usage stays low because only minimal related nodes are sent.

---

This document is the source of truth for the hybrid design.
