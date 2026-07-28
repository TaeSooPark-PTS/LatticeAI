# v10.1.0 — Hybrid Brain (2026-07-28)

## Summary

Local-first hybrid path. The Knowledge Graph stays on-device; cloud LLMs are an
opt-in worker. The default network boundary is `local_only` and cloud use
requires an explicit acknowledgement. Only minimal related nodes leave the
machine; streamed answers expand the local Brain with provenance, token
guardrails, and Review Queue gates.

## What's new

- **Network boundary** — `NetworkBoundaryMode` + `MinimalContext` contracts
  (`latticeai/core/network_boundary.py`, `latticeai/api/network_boundary.py`),
  persisted dial and runtime wiring (`runtime/network_boundary_wiring.py`,
  `services/network_boundary_service.py`).
- **Cloud streaming worker** — OpenAI-compatible stream adapter
  (`services/openai_compatible_adapter.py`), `cloud_streaming.py`,
  `cloud_extraction.py`, `cloud_token_guard.py`.
- **Hybrid chat** — `api/chat_hybrid.py`, `services/hybrid_chat.py`,
  `hybrid_context.py`, `hybrid_policy.py`; the `/chat` route branches through it.
- **Multimodal + UI** — `services/multimodal_streaming.py` and
  `static/app/network-boundary-panel.js`.

## Tests

`tests/unit/test_network_boundary.py`, `test_hybrid_phase2.py`,
`test_hybrid_phase3.py`, plus updated `test_chat_service_decomposition.py`.

## Compatibility

Additive. Default behaviour is unchanged (`local_only`); no existing caller is
affected.

See [docs/HYBRID_CLOUD_KG_STREAMING.md](docs/HYBRID_CLOUD_KG_STREAMING.md).
