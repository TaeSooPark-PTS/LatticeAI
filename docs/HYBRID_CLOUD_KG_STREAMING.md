# Hybrid Cloud + Local Knowledge Graph Streaming

> Status: **Phase 2 live** on branch `feature/hybrid-cloud-kg-streaming-phase2`  
> Previous: Phase 1 on `feature/hybrid-cloud-kg-streaming-phase1`  
> Principle: **Local Brain is the asset. Cloud is an opt-in worker.**

## Goal

Allow users to optionally use a cloud LLM in a **streaming** conversation while keeping the durable Knowledge Graph local.

Core loop:

1. User speaks / types.
2. System detects whether the session is in **Cloud-allowed** mode.
3. From the local Knowledge Graph, extract **only the minimal related nodes**.
4. Send that minimal context to the cloud LLM (token minimization + privacy).
5. Receive the answer as a **real-time stream**.
6. Expand the local Knowledge Graph with provenance (conversation + heuristic Concept/Decision/Task candidates).

## Phase status

### Phase 0
- Architecture + core contracts (`NetworkBoundaryMode`, `MinimalContext`, bridge/ingestor)

### Phase 1
- Persisted dial + HTTP API
- OpenAI-compatible streaming adapter
- hybrid_chat helpers + basic KG expansion staging
- Unit tests for mode / context / provenance

### Phase 2 (this branch) — implemented
- **Router wiring**: `/api/network-boundary` mounted next to permission-mode in `register_review_and_brain_tail_routers`
- **Chat path branch**: `latticeai/api/chat_hybrid.py` — when mode is `cloud_allowed`, `/chat` can return hybrid SSE instead of local stream
- **Token / cost guardrails**: `cloud_token_guard.py` (per-turn + per-session budgets via env)
- **Richer extraction**: `cloud_extraction.py` — Decision / Task / Concept candidates from answer text (always staged)
- **Transparency preview API**: `POST /api/network-boundary/preview` — nodes/keywords/token estimate that *would* leave the machine
- **SSE dual shape**: hybrid events (`hybrid_context`, `token`, `hybrid_done`) plus classic `chunk` for existing clients
- **Unit tests**: `tests/unit/test_hybrid_phase2.py`

### Phase 3 (future)
- Full React UI toggle + live transparency panel
- Optional video / multimodal streaming (same boundary)
- User-configurable sensitivity filters and auto-commit policy
- Stronger write path into Change Governor / Review Queue

## How `/chat` chooses the path (Phase 2)

```
resolve network mode (request.network_mode override → persisted dial)
    │
    ├── local_only  → existing local stream_chat / generate path
    └── cloud_allowed
            └── maybe_hybrid_stream_response(...)
                    → stream_hybrid_cloud_turn (minimal KG → cloud SSE → stage KG)
```

Integration helper: `latticeai.api.chat_hybrid.maybe_hybrid_stream_response`.
Call sites should invoke it **before** the local `stream_chat` path when the user message is a normal chat turn (not file-intent / clear / network-status shortcuts).

## Configuration

```bash
export LATTICEAI_CLOUD_API_KEY=sk-...
export LATTICEAI_CLOUD_BASE_URL=https://api.openai.com/v1   # optional
export LATTICEAI_CLOUD_MODEL=gpt-4o-mini                    # optional
export LATTICEAI_NETWORK_MODE=local_only                    # process default
export LATTICEAI_CLOUD_MAX_TOKENS_PER_TURN=2500
export LATTICEAI_CLOUD_MAX_TOKENS_PER_SESSION=50000
```

## API

```
GET  /api/network-boundary
GET  /api/network-boundary/catalog
POST /api/network-boundary
     { "mode": "cloud_allowed", "acknowledge_risk": true }

POST /api/network-boundary/preview
     { "message": "...", "workspace_id": null, "top_k": 6 }
     → node_ids, titles, token_estimate, would_block, compact_preview
```

## Privacy & Safety

1. Default remains `local_only`.
2. `cloud_allowed` requires explicit acknowledgement.
3. Only compact minimal context leaves the machine.
4. Sensitive metadata flags still hard-block nodes.
5. Cloud-derived nodes are **staged** (`auto_commit=False`) with full provenance.
6. Token budgets refuse oversized turns before any network call.

## Success criteria (Phase 2)

- Cloud path is a first-class option behind the dial, not a side script.
- Preview API lets UI show exactly what would be sent.
- Oversized context is refused before the provider is called.
- Cloud answers grow the local Brain as reviewable proposals, not silent writes.
