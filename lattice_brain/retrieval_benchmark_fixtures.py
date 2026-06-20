"""Corpus-scale retrieval fixture for the 7.4.0 Brain quality gate."""

from __future__ import annotations

FIXTURE_NAME = "7.4.0-corpus-scale-hybrid-retrieval"
TOP_K = 5

DOCUMENTS = [
    {"id": "doc:agent-runtime-contract", "type": "Decision", "title": "Agent runtime contract", "content": "Workflow run, agent run, audit event, and realtime event records must carry the agent-run-contract/v1 family envelope with run id, runtime, status, timeline, and artifacts."},
    {"id": "doc:workflow-observability", "type": "Document", "title": "Workflow observability", "content": "Workflow Designer persists queued, running, awaiting approval, cancelled, interrupted, partial, failed, and ok runs with replayable timeline events."},
    {"id": "doc:audit-trust", "type": "Security", "title": "Audit trust", "content": "Audit events are append-only, redacted before persistence, and linked to execution contracts for admin review without leaking secrets."},
    {"id": "doc:realtime-feed", "type": "Document", "title": "Realtime execution feed", "content": "The realtime SSE bus publishes workspace, agent, workflow, and execution events with workspace scope, event type, payload, sequence, and contract metadata."},
    {"id": "doc:retrieval-benchmark", "type": "Benchmark", "title": "Retrieval benchmark strategy", "content": "Hybrid retrieval quality must be checked with corpus-scale fixtures, judged queries, recall, precision, ndcg, and must-include hit rate."},
    {"id": "doc:hybrid-search", "type": "Architecture", "title": "Hybrid search", "content": "SearchService fuses keyword, vector, and graph retrieval channels with weighted scores and scoped workspace filtering."},
    {"id": "doc:knowledge-graph", "type": "Architecture", "title": "Knowledge graph stabilization", "content": "The Knowledge Graph preserves legacy read compatibility, non-destructive migrations, vector indexing, relationship traversal, and provenance."},
    {"id": "doc:local-first", "type": "Strategy", "title": "Local-first product wedge", "content": "Lattice AI is local-first: durable knowledge, private workspace memory, local LLM routing, and model-independent context are the product wedge."},
    {"id": "doc:brain-proof", "type": "UX", "title": "Brain proof and citations", "content": "Brain proof shows durable recall, source citation, graph counts, vector counts, and model continuity evidence in chat."},
    {"id": "doc:onboarding", "type": "UX", "title": "Five minute onboarding loop", "content": "The first loop asks users to add a source, ask a question, see Brain proof, and understand expected installation time."},
    {"id": "doc:vscode-sync", "type": "Integration", "title": "VS Code extension state", "content": "VS Code extension integration shows app connection state, indexing status, command routing, and extension-to-app sync health."},
    {"id": "doc:workspace-admin", "type": "Workspace", "title": "Workspace profile admin discovery", "content": "Personal workspace, organization workspace, profile, admin, members, roles, permissions, and security surfaces must be discoverable."},
    {"id": "doc:empty-error-consent", "type": "UX", "title": "Empty error consent states", "content": "Empty states, error states, consent prompts, retries, and privacy feedback should be explicit across ingestion, graph, chat, and workspace flows."},
    {"id": "doc:tool-registry", "type": "Architecture", "title": "Tool registry separation", "content": "ToolRegistry separates tool definitions, permissions, dispatch, audit, and UI discovery from AgentRuntime orchestration."},
    {"id": "doc:config-centralization", "type": "Architecture", "title": "Config centralization", "content": "Configuration should be centralized into explicit objects instead of hidden globals so runtime, tests, and packaging are predictable."},
    {"id": "doc:server-decomposition", "type": "Architecture", "title": "Server decomposition", "content": "Server decomposition moves routers, services, runtime context, and persistence seams into focused modules with dependency injection."},
    {"id": "doc:incremental-ingestion", "type": "Pipeline", "title": "Incremental ingestion", "content": "Background indexing needs duplicate detection, conflict resolution, incremental updates, and merge behavior for large personal corpora."},
    {"id": "doc:vector-index-evolution", "type": "Pipeline", "title": "Vector index evolution", "content": "sqlite-vec migration, HNSW evaluation, query caching, embedding caching, and retrieval caching support larger corpora."},
    {"id": "doc:security-zero-trust", "type": "Security", "title": "Zero trust defaults", "content": "Zero-trust defaults include audit logging, secret redaction, dependency vulnerability monitoring, and automated security scanning."},
    {"id": "doc:agentic-hitl", "type": "Workflow", "title": "Agentic human review", "content": "Agentic workflows need human-in-the-loop review, approval gates, rollback paths, review queues, and auditable decisions."},
    {"id": "doc:temporal-reasoning", "type": "Brain", "title": "Temporal reasoning", "content": "Temporal graph states, historical memory, contradiction detection, synthesis, and recommendations make the Brain proactive."},
    {"id": "doc:multimodal-memory", "type": "Brain", "title": "Multimodal memory", "content": "Images, audio, video, documents, and chat should ingest into one durable Brain with retrieval and citation proof."},
    {"id": "doc:developer-api", "type": "API", "title": "Developer API", "content": "A secure developer API exposes third-party access to workspace knowledge, tools, runs, contracts, and audit trails."},
    {"id": "doc:team-brains", "type": "Business", "title": "Team Brains", "content": "Team Brains, encrypted sharing, premium cloud sync, hosted models, plugin marketplace, and public benchmarks support monetization."},
    {"id": "doc:docker-tauri", "type": "Deployment", "title": "Deployment operations", "content": "Docker Compose deployment, one-click installation, Tauri auto-updates, rollback support, and exact release artifacts improve operations."},
    {"id": "doc:model-routing", "type": "Runtime", "title": "Model orchestration", "content": "Ollama, LM Studio, MLX, and cloud models need automatic fallback, routing, and replaceable model boundaries."},
    {"id": "doc:strict-quality", "type": "Quality", "title": "Strict code quality", "content": "Async-first architecture, dependency injection, centralized exceptions, mypy or pyright, TypeScript strict mode, E2E tests, and property tests reduce regressions."},
    {"id": "distractor:theme-editor", "type": "Distractor", "title": "Theme editor", "content": "Color palettes, typography, and sidebar icon sizing are useful but unrelated to retrieval contract benchmarks."},
    {"id": "distractor:calendar", "type": "Distractor", "title": "Calendar reminders", "content": "Calendar reminders and meeting agenda formatting are integrations but not the runtime event contract path."},
    {"id": "distractor:billing", "type": "Distractor", "title": "Billing invoices", "content": "Invoices, payment receipts, and subscription emails can be stored but are not the Brain quality gate."},
    {"id": "distractor:mobile-shell", "type": "Distractor", "title": "Mobile shell", "content": "A mobile companion can improve access but should not replace local-first desktop retrieval and run observability."},
    {"id": "distractor:marketing", "type": "Distractor", "title": "Marketing landing page", "content": "Landing pages and hero copy do not prove durable knowledge or agent runtime correctness."},
]

QUERIES = [
    {"query": "agent workflow audit realtime contract run status timeline", "relevant": {"doc:agent-runtime-contract": 3, "doc:workflow-observability": 2, "doc:audit-trust": 2, "doc:realtime-feed": 2}, "must_include": ["doc:agent-runtime-contract"]},
    {"query": "workflow queued running cancelled interrupted replay approval", "relevant": {"doc:workflow-observability": 3, "doc:agentic-hitl": 2}, "must_include": ["doc:workflow-observability"]},
    {"query": "audit log redacted secrets admin review contract", "relevant": {"doc:audit-trust": 3, "doc:security-zero-trust": 2}, "must_include": ["doc:audit-trust"]},
    {"query": "SSE realtime workspace event payload sequence contract", "relevant": {"doc:realtime-feed": 3, "doc:agent-runtime-contract": 2}, "must_include": ["doc:realtime-feed"]},
    {"query": "hybrid retrieval keyword vector graph weighted scoped search", "relevant": {"doc:hybrid-search": 3, "doc:knowledge-graph": 2, "doc:retrieval-benchmark": 2}, "must_include": ["doc:hybrid-search"]},
    {"query": "corpus scale recall precision ndcg judged queries", "relevant": {"doc:retrieval-benchmark": 3, "doc:hybrid-search": 2}, "must_include": ["doc:retrieval-benchmark"]},
    {"query": "local first model independent durable memory brain proof citation", "relevant": {"doc:local-first": 3, "doc:brain-proof": 2}, "must_include": ["doc:local-first"]},
    {"query": "incremental indexing duplicate conflict merge background ingestion", "relevant": {"doc:incremental-ingestion": 3, "doc:vector-index-evolution": 2}, "must_include": ["doc:incremental-ingestion"]},
    {"query": "workspace admin profile organization members roles permissions", "relevant": {"doc:workspace-admin": 3, "doc:security-zero-trust": 2}, "must_include": ["doc:workspace-admin"]},
    {"query": "tool registry separation dependency injection runtime context", "relevant": {"doc:tool-registry": 3, "doc:server-decomposition": 2, "doc:config-centralization": 2}, "must_include": ["doc:tool-registry"]},
    {"query": "human in the loop approval review queue rollback", "relevant": {"doc:agentic-hitl": 3, "doc:workflow-observability": 2}, "must_include": ["doc:agentic-hitl"]},
    {"query": "model routing ollama lm studio mlx cloud fallback replaceable", "relevant": {"doc:model-routing": 3, "doc:local-first": 2}, "must_include": ["doc:model-routing"]},
]
