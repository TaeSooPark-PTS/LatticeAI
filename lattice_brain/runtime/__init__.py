"""Agent and hook runtime subsystem of the Brain Core.

Physically hosts the hooks registry/dispatch lifecycle, the multi-agent
orchestrator, and the agent runtime service. Lazy-loaded so importing
``lattice_brain.runtime`` stays cheap.

=== lattice_brain/runtime 책임 + 의존성 그래프 (A방향 + Act/automation 기준) ===
- multi_agent.py (핵심 실행체)
  책임: OrchestrationContext, AgentContextPacket, AgentHandoff, AgentRunResult,
        MultiAgentOrchestrator, default_role_runner, llm_role_runner,
        CORE_PIPELINE/AGENT_ROLES, handoff/review/retry 타임라인 생성.
  의존: 없음 (순수, deterministic/LLM runner 주입).
  진입: orchestrator_factory 로 AgentRuntime에 주입됨.

- hooks.py (라이프사이클 확장)
  책임: HookContext/HookResult, HooksRegistry (builtin+user, order/enabled persist),
        dispatch_tool (pre_tool/post_tool 통합), fire_hook/run_hooks,
        BUILTIN_HOOKS (redact, memory-snapshot, tool-permission-gate 등).
  의존: subprocess (user command hooks).
  진입: AgentRuntime, tool_dispatch, api/tools, core/agent 등에 주입/등록.

- agent_runtime.py (공개 퍼사드 / 바운더리)
  책임: AgentRuntime (store+orchestrator_factory+workspace_graph+audit+hooks 주입),
        start/reserve_run/complete_reserved_run, stop, status/health/config,
        list_runs/get_run/events/replay, _fire_pre_run / _post_run_hooks.
  의존: .multi_agent, .hooks (간접), store (WORKSPACE_OS).
  진입점: **여기가 제품 경계**. app_factory에서 AGENT_RUNTIME으로 생성,
         api/agents.py (런타임 라우터), RunExecutor (async agent/workflow),
         workflow_designer에 주입. frontend BrainAutomationPanel / Act 가
         /agents/* 를 통해 이 바운더리만 의존.

- __init__.py : lazy re-export (import 비용 최소화).

실제 진입점 매핑 (app_factory + 호출 스택):
  app_factory.py:1508
    AGENT_RUNTIME = AgentRuntime(store=WORKSPACE_OS, orchestrator_factory=PLATFORM.build_orchestrator, hooks=HOOKS_REGISTRY, ...)
    RUN_EXECUTOR = RunExecutor(..., agent_runtime=AGENT_RUNTIME)
    AGENT_RUNTIME.attach_executor(RUN_EXECUTOR)
    create_agents_router(..., agent_runtime=AGENT_RUNTIME)
    create_workflow_designer_router(..., run_executor=RUN_EXECUTOR, trigger_service=TRIGGER_SERVICE)
  api/agents.py:49
    from lattice_brain.runtime.agent_runtime import AgentRuntime
    runtime = agent_runtime or AgentRuntime(...)  # fallback
    /agents/api/runtime/* , POST /agents (start via runtime.start or executor)
  api/chat.py
    from latticeai.services.tool_dispatch import build_agent_runtime
    -> latticeai/core/agent.py:SingleAgentRuntime (별도 state/plan/transcript 머신, single-agent /agent 경로. dispatch_tool 만 공유)
  latticeai/services/tool_dispatch.py:14
    from latticeai.core.agent import SingleAgentRuntime
    (tool governance + core single-agent용)
  latticeai/services/platform_runtime.py
    from lattice_brain.runtime.{hooks, multi_agent}
  tests: test_hooks_dispatch.py, test_t7_triggers.py 등에서 직접 import lattice_brain.runtime.* 

core/tool_registry.py (신규) + services/tool_dispatch.py 가 tool build 주도.
lattice_brain/runtime 는 multi-agent + hooks + facade 에 집중. core/agent 는 chat/agent 단일 루프.
기존 latticeai.core.agent.AgentRuntime import 는 SingleAgentRuntime 호환 alias 로 유지.

이 매핑으로 중복 제거 및 wiring 명확화 완료 (feat(Act, automation) 방향).
"""

from __future__ import annotations

__all__ = [
    "AgentRuntime",
    "AgentRuntimeUnavailable",
    "MultiAgentOrchestrator",
    "RuntimeBoundaryProtocol",
    "HooksRegistry",
    "dispatch_tool",
]


def __getattr__(name: str):
    if name in {"AgentRuntime", "AgentRuntimeUnavailable"}:
        from . import agent_runtime

        return getattr(agent_runtime, name)
    if name == "MultiAgentOrchestrator":
        from .multi_agent import MultiAgentOrchestrator

        return MultiAgentOrchestrator
    if name == "RuntimeBoundaryProtocol":
        from .contracts import RuntimeBoundaryProtocol

        return RuntimeBoundaryProtocol
    if name in {"HooksRegistry", "dispatch_tool"}:
        from . import hooks

        return getattr(hooks, name)
    raise AttributeError(name)
