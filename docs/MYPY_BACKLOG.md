# mypy backlog

`[tool.mypy] files` in `pyproject.toml` lists every module that type-checks
today — **193 of 270** as of 10.3.0, up from 13 in 10.2.0.
This file is the remainder, smallest first, so the boundary is a measured
fact rather than an open-ended intention.

Adding a module to the checked set is a decision to fix that module, not its
dependencies (`follow_imports = silent`). Work from the top: the short ones
are usually one missing annotation.

Three of these turned out to be real defects when 10.3.0 measured them:
`hooks.py` referenced a non-existent `self._path` inside the handler for an
unreadable registry, `_kg_fsutil.py` used `Iterable` without importing it,
and `agent_runtime.py` called `.get` on a value that could be `None`. Those
are fixed; the list below is what is left.

| errors | module | dominant codes |
| ---: | --- | --- |
| 1 | `lattice_brain/graph/_kg_fsutil.py` | `union-attr` ×1 |
| 1 | `latticeai/api/admin.py` | `call-overload` ×1 |
| 1 | `latticeai/api/computer_use.py` | `var-annotated` ×1 |
| 1 | `latticeai/api/hooks.py` | `unreachable` ×1 |
| 1 | `latticeai/api/local_files.py` | `arg-type` ×1 |
| 1 | `latticeai/api/static_routes.py` | `assignment` ×1 |
| 1 | `latticeai/core/marketplace.py` | `arg-type` ×1 |
| 1 | `latticeai/core/model_compat.py` | `unreachable` ×1 |
| 1 | `latticeai/core/plugins.py` | `unreachable` ×1 |
| 1 | `latticeai/core/project_sessions.py` | `var-annotated` ×1 |
| 1 | `latticeai/core/workspace_os.py` | `unreachable` ×1 |
| 1 | `latticeai/core/workspace_os_state.py` | `arg-type` ×1 |
| 1 | `latticeai/runtime/sso_config_runtime.py` | `arg-type` ×1 |
| 1 | `latticeai/services/command_center.py` | `assignment` ×1 |
| 1 | `latticeai/services/search_service.py` | `assignment` ×1 |
| 1 | `latticeai/services/triggers.py` | `truthy-function` ×1 |
| 1 | `latticeai/setup/auto_setup.py` | `attr-defined` ×1 |
| 2 | `lattice_brain/ingestion.py` | `assignment` ×2 |
| 2 | `latticeai/api/chat_stream.py` | `var-annotated` ×1, `arg-type` ×1 |
| 2 | `latticeai/api/mcp.py` | `var-annotated` ×2 |
| 2 | `latticeai/core/mcp_registry.py` | `index` ×1, `return-value` ×1 |
| 2 | `latticeai/core/run_explain.py` | `union-attr` ×1, `call-overload` ×1 |
| 2 | `latticeai/core/workspace_snapshots.py` | `var-annotated` ×2 |
| 2 | `latticeai/runtime/review_wiring.py` | `call-arg` ×2 |
| 2 | `latticeai/services/brain_intelligence.py` | `operator` ×1, `index` ×1 |
| 2 | `latticeai/services/memory_service.py` | `misc` ×1, `index` ×1 |
| 2 | `latticeai/services/model_loading.py` | `arg-type` ×1, `call-overload` ×1 |
| 2 | `latticeai/services/multimodal_streaming.py` | `attr-defined` ×1, `arg-type` ×1 |
| 2 | `latticeai/setup/wizard.py` | `attr-defined` ×1, `dict-item` ×1 |
| 2 | `latticeai/tools/documents.py` | `arg-type` ×2 |
| 3 | `lattice_brain/quality.py` | `var-annotated` ×2, `return-value` ×1 |
| 3 | `latticeai/api/models.py` | `call-overload` ×2, `attr-defined` ×1 |
| 3 | `latticeai/cli/entrypoint.py` | `operator` ×1, `union-attr` ×1, `arg-type` ×1 |
| 3 | `latticeai/core/audit.py` | `assignment` ×1, `index` ×1, `operator` ×1 |
| 3 | `latticeai/integrations/telegram_bot.py` | `union-attr` ×2, `arg-type` ×1 |
| 3 | `latticeai/runtime/stages.py` | `arg-type` ×3 |
| 3 | `latticeai/services/cloud_streaming.py` | `arg-type` ×2, `attr-defined` ×1 |
| 3 | `latticeai/services/hybrid_chat.py` | `arg-type` ×3 |
| 3 | `latticeai/services/tool_dispatch.py` | `arg-type` ×3 |
| 4 | `lattice_brain/graph/_kg_common.py` | `misc` ×3, `return-value` ×1 |
| 4 | `latticeai/api/chat_agent_http.py` | `arg-type` ×2, `misc` ×1, `assignment` ×1 |
| 4 | `latticeai/api/chat_helpers.py` | `call-overload` ×2, `union-attr` ×1, `arg-type` ×1 |
| 4 | `latticeai/api/security_dashboard.py` | `assignment` ×3, `unreachable` ×1 |
| 4 | `latticeai/services/evidence_actions.py` | `union-attr` ×4 |
| 5 | `lattice_brain/context.py` | `arg-type` ×4, `misc` ×1 |
| 5 | `latticeai/api/permissions.py` | `arg-type` ×5 |
| 5 | `latticeai/services/model_engines.py` | `attr-defined` ×2, `call-overload` ×2, `unreachable` ×1 |
| 5 | `latticeai/services/run_executor.py` | `unreachable` ×3, `arg-type` ×1, `return-value` ×1 |
| 6 | `lattice_brain/runtime/contracts.py` | `union-attr` ×6 |
| 6 | `latticeai/core/agent.py` | `arg-type` ×4, `return-value` ×2 |
| 7 | `lattice_brain/graph/proactive.py` | `assignment` ×2, `arg-type` ×2, `index` ×1 |
| 7 | `latticeai/app_factory.py` | `arg-type` ×3, `assignment` ×2, `attr-defined` ×2 |
| 7 | `latticeai/core/embedding_providers.py` | `unreachable` ×2, `assignment` ×2, `union-attr` ×2 |
| 7 | `latticeai/services/openai_compatible_adapter.py` | `arg-type` ×7 |
| 8 | `lattice_brain/graph/curator.py` | `operator` ×8 |
| 8 | `lattice_brain/workflow.py` | `arg-type` ×5, `index` ×2, `assignment` ×1 |
| 8 | `latticeai/api/setup.py` | `index` ×5, `attr-defined` ×3 |
| 8 | `latticeai/services/local_knowledge.py` | `union-attr` ×3, `misc` ×2, `operator` ×2 |
| 10 | `latticeai/api/tools.py` | `arg-type` ×10 |
| 13 | `latticeai/api/search.py` | `return` ×12, `return-value` ×1 |
| 15 | `lattice_brain/graph/store.py` | `name-defined` ×15 |
| 16 | `latticeai/api/chat.py` | `arg-type` ×12, `misc` ×3, `call-overload` ×1 |
| 16 | `latticeai/tools/computer.py` | `union-attr` ×16 |
| 19 | `lattice_brain/graph/retrieval_docgen.py` | `name-defined` ×12, `attr-defined` ×7 |
| 35 | `latticeai/services/model_runtime.py` | `index` ×15, `arg-type` ×8, `attr-defined` ×6 |
| 45 | `lattice_brain/graph/retrieval_reads.py` | `name-defined` ×30, `attr-defined` ×15 |
| 46 | `latticeai/models/router.py` | `arg-type` ×26, `assignment` ×7, `attr-defined` ×4 |
| 57 | `lattice_brain/graph/write_master.py` | `name-defined` ×48, `attr-defined` ×9 |
| 64 | `lattice_brain/graph/documents.py` | `name-defined` ×45, `attr-defined` ×19 |
| 71 | `lattice_brain/graph/provenance.py` | `name-defined` ×59, `attr-defined` ×12 |
| 77 | `lattice_brain/graph/retrieval.py` | `name-defined` ×61, `attr-defined` ×16 |
| 79 | `lattice_brain/graph/discovery.py` | `name-defined` ×72, `attr-defined` ×7 |
| 107 | `lattice_brain/graph/retrieval_vector.py` | `name-defined` ×68, `attr-defined` ×39 |
| 108 | `latticeai/api/workspace.py` | `misc` ×105, `arg-type` ×2, `call-overload` ×1 |
| 127 | `lattice_brain/graph/discovery_index.py` | `name-defined` ×98, `attr-defined` ×29 |
| 143 | `lattice_brain/graph/projection.py` | `name-defined` ×122, `attr-defined` ×21 |
| 177 | `lattice_brain/graph/ingest.py` | `name-defined` ×122, `attr-defined` ×55 |

Total outstanding: **1407** errors across **77** modules.
