# Hybrid Cloud + Local Knowledge Graph Streaming

> Status: **design + scaffolding** on branch `feature/hybrid-cloud-kg-streaming`  
> Target release window: experimental / post-10.0.x  
> Principle: **Local Brain is the asset. Cloud is an opt-in worker.**

## Goal

Allow users to optionally use a cloud LLM (and later video models) in a **streaming** conversation while keeping the durable Knowledge Graph local.

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
| `cloud_allowed` | No (explicit opt-in) | Opt-in cloud LLM | Minimal related nodes + compact summary | Cloud response is ingested back into local KG |

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
CloudStreamingBridge
    • send minimal context + user message
    • receive token / chunk stream
    • emit intermediate events to UI
    ▼
CloudResponseIngestor
    • turn streamed answer into candidate nodes / edges
    • attach provenance: "derived_from_cloud" + source_node_ids that were sent
    • quality gate or proposal (configurable)
    • write into local Knowledge Graph
```

## Key Contracts

### 1. NetworkBoundaryMode (`latticeai/core/network_boundary.py`)

Mirrors the style of `PermissionMode`:

- `LOCAL_ONLY` (default)
- `CLOUD_ALLOWED` (requires_ack = true)

Rules:
- Mode is resolved per user + workspace (or per session).
- Circuit-breaker style: certain node types or sensitivity tags can never be sent even in `CLOUD_ALLOWED`.
- Changing mode mid-conversation is allowed but should be logged in the audit trail.

### 2. MinimalContextExtractor (`latticeai/services/hybrid_context.py`)

Input: user message + current mode + workspace scope  
Output: `MinimalContext` object

```python
@dataclass
class MinimalContext:
    query: str
    keywords: list[str]
    node_ids: list[str]
    compact_text: str          # what actually goes to the cloud
    nodes: list[dict]          # full local nodes (for provenance)
    token_estimate: int
    quality: dict              # re-uses context_quality_signal shape
```

Selection rules (v1):
- Use existing `hybrid_search` / `context_for_query_with_meta`.
- Hard limit: top 6–8 nodes, prefer Decision / Concept / Document / Task.
- Drop nodes marked sensitive or belonging to excluded types.
- Prefer summary over full body; never send raw large blobs.

### 3. CloudStreamingBridge (`latticeai/services/cloud_streaming.py`)

Responsibilities:
- Hold the cloud provider adapter (OpenAI-compatible, Anthropic, etc.).
- Accept `MinimalContext` + user message.
- Yield an async stream of text chunks (and later media chunks).
- On completion, return a `CloudTurnResult` containing the full answer + the exact node_ids that were sent.

No cloud call is allowed unless `NetworkBoundaryMode == CLOUD_ALLOWED`.

### 4. CloudResponseIngestor

Takes `CloudTurnResult` and expands the local KG:

- Create a Conversation / Message node for the cloud turn.
- Extract candidate Concept / Decision / Task nodes from the answer (lightweight, deterministic first; LLM extraction later if needed).
- Link them with provenance edges:
  - `derived_from_cloud`
  - `grounded_on` → the original local node_ids that were sent
- Default behaviour (v1): stage as **proposals** or low-confidence nodes that go through the existing quality / review path.
- Optional aggressive mode (user setting): auto-commit high-confidence extractions.

## Privacy & Safety Rules

1. Default is always `local_only`.
2. `cloud_allowed` requires explicit user acknowledgement.
3. Only the compact text assembled by MinimalContextExtractor leaves the machine.
4. Sensitive node types / tags are blocked by a hard filter (mode-invariant).
5. Every cloud turn records:
   - which node_ids were sent
   - which model / provider was used
   - the resulting new node ids
6. User can later purge all “derived_from_cloud” subgraphs.

## Integration Points (existing code)

| Existing piece | How it is reused |
|----------------|------------------|
| `PermissionMode` | Orthogonal; network boundary is a separate dial |
| `hybrid_search` / `context_for_query_with_meta` | Core of MinimalContextExtractor |
| `context_quality_signal` | Exposed to UI so the user sees how thin the sent context was |
| Change Governor / Review Queue | Cloud-derived nodes can be staged as proposals |
| Chat streaming path | CloudStreamingBridge plugs into the same event channel |
| Audit / process_audit | Mode changes and cloud turns are audited |

## UI Sketch

- Composer or top-bar toggle: **로컬만** | **클라우드 스트리밍 허용**
- When switching to cloud: confirmation dialog (same tone as Bypass mode).
- During a cloud turn: small indicator “클라우드로 최소 근거 N개 전송 중…” + optional expandable list of the node titles that were sent.
- After the turn: “Brain이 이 답변으로 기억을 확장했습니다” with link to the new nodes.

## Implementation Phases

### Phase 0 (this branch)
- Architecture document
- `NetworkBoundaryMode` enum + contract
- `MinimalContext` + extractor skeleton
- `CloudStreamingBridge` + `CloudResponseIngestor` interfaces / no-op implementations
- Unit-test stubs

### Phase 1
- Wire mode into chat request path
- Real MinimalContextExtractor using existing hybrid_search
- One concrete cloud provider adapter (OpenAI-compatible streaming)
- Basic KG expansion (conversation node + provenance only)

### Phase 2
- Quality-gated concept/decision extraction from cloud answers
- UI mode switch + transparency panel
- Token accounting and cost guardrails

### Phase 3
- Optional video / multimodal streaming (still behind the same boundary)
- User-configurable sensitivity filters and auto-commit policy

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

This document is the source of truth for the hybrid design on this branch.
