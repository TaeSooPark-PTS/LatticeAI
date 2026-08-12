# Hybrid Cloud + Local Knowledge Graph Streaming

> Status: **shipped in 10.1.0**; in-app control added in **10.1.1**  
> Principle: **Local Brain is the asset. Cloud is an opt-in worker.**  
> Surface: `환경설정 → 내 지식이 나가는 범위`
> (`frontend/src/components/NetworkBoundaryPanel.tsx`), plus
> `/api/network-boundary` and `LATTICEAI_NETWORK_MODE`.

## Goal

Local Knowledge Graph stays on-device. Cloud LLMs (and optional multimodal) are opt-in workers. Minimal related nodes leave the machine; streamed answers expand the local Brain with provenance and review gates.

## Phase summary

| Phase | Branch | Delivered |
|-------|--------|-----------|
| 0 | `feature/hybrid-cloud-kg-streaming` | Architecture + core contracts |
| 1 | `…-phase1` | Dial, OpenAI adapter, hybrid helpers, basic expansion |
| 2 | `…-phase2` | `/chat` branch, token guardrails, extraction, preview API |
| **3** | `…-phase3` | **Policy, Review Queue write path, multimodal contracts, UI panel module** |

## Phase 3 details

### Hybrid policy (`HybridPolicyService`)
Persisted dial for:
- `blocked_node_types` / `blocked_metadata_flags` (unioned with hard circuit breakers)
- `auto_commit` (default **false**)
- `allow_multimodal` (default **false**)
- `min_extraction_confidence`

API:
```
GET  /api/network-boundary/policy
POST /api/network-boundary/policy
     { "auto_commit": false, "allow_multimodal": false, "blocked_node_types": ["Secret"] }
```

### Review Queue write path
`CloudResponseIngestor` now:
1. Always enqueues a `change_proposal` review item when a ReviewQueueService is bound
2. Only auto-writes to the graph when `plan.auto_commit` is true **and** a store write API exists

Users approve cloud-derived memory growth in the existing Review Center.

### Multimodal / video
There is no cloud multi-modal *streaming* path. A `multimodal_streaming` module
was written to a provider-adapter shape and never wired to a provider or a
route; 11.5.2 deleted it rather than keep a design sketch reading as a feature.

Multi-modal ingestion itself is local and unrelated to this document's cloud
boundary — see `lattice_brain/ingestion/routing.py` and the `allow_multimodal`
gate. If a cloud video provider ever lands, the module is recoverable from git
history (`latticeai/services/multimodal_streaming.py`, removed in 11.5.2).

### UI
`NetworkBoundaryPanel` (React) + `GET /api/network-boundary/ui-state`:
- Toggle local ↔ cloud (with ack)
- Transparency preview of nodes about to be sent
- Shows current policy flags

Mounted in `frontend/src/pages/System.tsx` beside `PermissionModePanel`, so it
appears on the settings tab of every install. The standalone
`static/app/network-boundary-panel.js` module 10.1.0 shipped was removed in
10.1.1: nothing mounted it, and it lived in Vite's build output directory,
which `npm run build:assets` wipes.

## End-to-end flow (Phase 3)

```
User toggles cloud_allowed (UI panel or API)
    → optional policy: auto_commit / multimodal / sensitivity
User sends chat message
    → /chat resolves mode
    → if cloud_allowed: minimal KG context + token budget check
    → cloud stream (SSE)
    → rich extraction (Decision/Task/Concept)
    → Review Queue item (change_proposal)
    → optional auto-commit when policy allows
```

## Configuration

```bash
export LATTICEAI_CLOUD_API_KEY=sk-...
export LATTICEAI_CLOUD_MODEL=gpt-4o-mini
export LATTICEAI_NETWORK_MODE=local_only
export LATTICEAI_CLOUD_MAX_TOKENS_PER_TURN=2500
export LATTICEAI_CLOUD_MAX_TOKENS_PER_SESSION=50000
```

## Privacy (unchanged core rules)

1. Default `local_only`
2. Cloud requires explicit ack
3. Only minimal compact context leaves the host
4. Hard + user sensitivity filters
5. Cloud-derived nodes go through Review Queue unless user opts into auto_commit
6. Multimodal is off unless policy explicitly enables it

## Success criteria (Phase 3)

- User can configure sensitivity and auto-commit without code changes
- Cloud KG growth appears in Review Center as actionable proposals
- Multimodal path cannot fire without both cloud mode and policy flag
- UI panel works without rebuilding the main React bundle
