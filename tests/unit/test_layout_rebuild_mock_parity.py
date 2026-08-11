"""Visual-mock ↔ real API parity for the layout-rebuild capture surfaces.

Release screenshots are taken against ``tests/visual/mock_server.cjs``. A mock
that invents a field, pins the wrong bucket, or drifts from the real enum makes
the published evidence describe a product that does not exist. Each test here
pins one mock payload against the router that answers the same path for real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.admin import create_admin_router
from latticeai.api.automation_intelligence import create_automation_intelligence_router
from latticeai.services.automation_intelligence import AutomationIntelligenceService

from ._layout_rebuild_common import (
    _deep_key_set,
    _extract_mock_json_object,
    _mock_server_source,
    _parse_mock_sysinfo,
    _readiness_copy_from_workspace_i18n,
    _workspace_i18n_keys,
)


def test_host_capacity_readiness_buckets():
    """System basic mode must not re-derive host capacity copy on the client."""
    from latticeai.api.static_routes import host_capacity_readiness

    assert host_capacity_readiness(cpu_pct=10, ram_pct=20, gpu_mem_pct=5) == "roomy"
    assert host_capacity_readiness(cpu_pct=55, ram_pct=40, gpu_mem_pct=10) == "roomy"
    assert host_capacity_readiness(cpu_pct=34, ram_pct=61, gpu_mem_pct=48) == "tight"
    assert host_capacity_readiness(cpu_pct=80, ram_pct=10, gpu_mem_pct=10) == "tight"
    assert host_capacity_readiness(cpu_pct=10, ram_pct=10, gpu_mem_pct=81) == "low"
    assert host_capacity_readiness(cpu_pct=99, ram_pct=99, gpu_mem_pct=99) == "low"


def test_mock_sysinfo_readiness_matches_capture_bucket():
    """Mock /local/sysinfo percents must derive the same readiness the mock pins.

    Failure mode this catches: mock still returns ram_pct=61 but readiness is
    missing or wrong (e.g. roomy). The System basic-mode UI keys off the
    readiness field via ``system.readiness.*`` in workspace.ts; a wrong bucket
    selects the roomy/"넉넉" sentence while the load profile is tight.

    This test does **not** OCR release screenshots. Stale 08-system.png is a
    separate evidence-binding gate (``scripts/check_release_evidence_bound.mjs``).
    """
    from latticeai.api.static_routes import host_capacity_readiness

    mock = _parse_mock_sysinfo()
    assert mock.get("ram_pct") == 61
    assert mock.get("cpu_pct") == 34
    assert mock.get("gpu_mem_pct") == 48
    assert mock.get("readiness") == "tight", (
        f"mock must pin readiness=tight for the capture load profile; got {mock!r}"
    )

    derived = host_capacity_readiness(
        cpu_pct=float(mock["cpu_pct"]),
        ram_pct=float(mock["ram_pct"]),
        gpu_mem_pct=float(mock["gpu_mem_pct"]),
    )
    assert derived == mock["readiness"] == "tight"

    copy = _readiness_copy_from_workspace_i18n()
    assert set(copy) == {"roomy", "tight", "low"}
    assert len(set(copy.values())) == 3, "ko readiness phrases must stay distinct"

    capture_text = copy[mock["readiness"]]
    roomy_text = copy["roomy"]
    assert capture_text != roomy_text
    assert "타이트" in capture_text
    assert "넉넉" not in capture_text
    assert "넉넉" in roomy_text


def test_system_settings_basic_branch_reads_sysinfo_readiness():
    """System.tsx basic branch must consume response ``readiness``, not a hardcode.

    Failure mode this catches: mock↔i18n agreement stays green while SettingsPanel
    basic mode goes back to always rendering ``system.readiness.plenty`` (or any
    fixed key) and never reads ``data.readiness``. Capture then shows "넉넉" even
    when /local/sysinfo correctly returns readiness=tight.
    """
    import re

    system_path = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "pages"
        / "System.tsx"
    )
    source = system_path.read_text(encoding="utf-8")

    # SettingsPanel owns the host-capacity DataPanel; isolate its body so a
    # coincidental ``readiness`` mention elsewhere cannot satisfy the gate.
    panel_match = re.search(
        r"function SettingsPanel\b[\s\S]*?(?=\nfunction |\nexport |\Z)",
        source,
    )
    assert panel_match, "System.tsx must define SettingsPanel"
    panel = panel_match.group(0)
    assert re.search(r'mode\s*===\s*"basic"', panel), (
        "SettingsPanel must keep a mode === \"basic\" branch for readiness copy"
    )

    # The basic branch must pull readiness off the sysinfo payload object.
    assert re.search(
        r"(?:\?\.\s*readiness|\[['\"]readiness['\"]\]|\.readiness)\b",
        panel,
    ), "SettingsPanel must read data.readiness (or data?.readiness) from /local/sysinfo"

    # i18n key must be derived from that bucket: system.readiness.${readiness}
    # (or equivalent concat). A static system.readiness.plenty alone is the
    # regression the review called out.
    dynamic_key = (
        re.search(r"`system\.readiness\.\$\{[^}]+\}`", panel) is not None
        or re.search(r'["\']system\.readiness\.["\']\s*\+\s*\w+', panel) is not None
        or re.search(r"system\.readiness\.\$\{\s*readiness\s*\}", panel) is not None
    )
    assert dynamic_key, (
        "SettingsPanel basic branch must build system.readiness.<bucket> from the "
        "readiness field; hardcoding system.readiness.plenty alone is a regression"
    )


def test_mock_permissions_pending_matches_real_api_shape(tmp_path: Path):
    """Mock /permissions/pending must match real action_label bridge values.

    Failure mode (capture 09): mock invents a label that does not tokenize to
    an i18n key (or drifts from _PERMISSION_ACTION_LABELS). Act.tsx prefers
    action_label for key derivation and t() has no defaultValue — raw keys ship.
    """
    import time

    from latticeai.api.permissions import (
        _PERMISSION_ACTION_LABELS,
        create_permissions_router,
    )

    mock = _extract_mock_json_object(_mock_server_source(), "/permissions/pending")
    assert "pending" in mock and "count" in mock
    assert isinstance(mock["pending"], dict) and mock["pending"]
    assert mock["count"] == len(mock["pending"])

    # Mock must include one mapped label (same string as the real map) AND one
    # unmapped fallback (raw English action).
    labels = {item.get("action_label") for item in mock["pending"].values()}
    actions = {item.get("action") for item in mock["pending"].values()}
    expected_read_label = _PERMISSION_ACTION_LABELS["read"]
    assert expected_read_label in labels, (
        f"mock must use real action_label for action=read "
        f"({expected_read_label!r}); got {labels!r}"
    )
    assert "read" in actions
    assert any(
        item.get("action") == "delete" and item.get("action_label") == "delete"
        for item in mock["pending"].values()
    ), "mock must include unmapped action=delete → action_label='delete' fallback"

    class _Cfg:
        discord_permission_webhook = ""
        discord_bot_token = ""
        discord_permission_channel = ""
        permission_monitor_secret = ""

    def require_admin(_request):
        return "admin@example.com", {}

    router, gateway = create_permissions_router(
        config=_Cfg(),
        data_dir=tmp_path,
        require_user=lambda _r: "admin@example.com",
        require_admin=require_admin,
        get_current_user=lambda _r: "admin@example.com",
    )
    # Seed mapped + unmapped actions exactly like the mock contract.
    now = time.time()
    gateway.local_approvals["hash-read"] = {
        "path": "/tmp/report.md",
        "action": "read",
        "user_email": "admin@example.com",
        "approved": False,
        "expires_at": now + 300,
        "token_hint": "perm-token",
    }
    gateway.local_approvals["hash-delete"] = {
        "path": "/tmp/legacy-cache.bin",
        "action": "delete",
        "user_email": "admin@example.com",
        "approved": False,
        "expires_at": now + 240,
        "token_hint": "perm-token-delete",
    }
    app = FastAPI()
    app.include_router(router)
    real = TestClient(app).get("/permissions/pending").json()

    mock_item_keys = set()
    for item in mock["pending"].values():
        mock_item_keys |= set(item.keys())
    real_item_keys = set()
    for item in real["pending"].values():
        real_item_keys |= set(item.keys())
    # Real response keys must be a superset of mock keys (mock ⊆ real).
    assert mock_item_keys.issubset(real_item_keys), (
        f"mock pending item keys {mock_item_keys} not ⊆ real {real_item_keys}"
    )
    assert {"pending", "count"}.issubset(real.keys())
    assert {"pending", "count"}.issubset(mock.keys())

    # Enum domain: mapped labels come from _PERMISSION_ACTION_LABELS; unmapped
    # fall back to the raw action string.
    for item in real["pending"].values():
        action = str(item.get("action") or "")
        expected = _PERMISSION_ACTION_LABELS.get(action, action)
        assert item.get("action_label") == expected, item
    read_item = next(v for v in real["pending"].values() if v.get("action") == "read")
    assert read_item["action_label"] == _PERMISSION_ACTION_LABELS["read"]
    delete_item = next(v for v in real["pending"].values() if v.get("action") == "delete")
    assert delete_item["action_label"] == "delete"
    # Contract: every pending item always carries a non-empty action string.
    for item in real["pending"].values():
        assert isinstance(item.get("action"), str) and item["action"], item


def test_permission_action_labels_have_matching_i18n_keys():
    """F1 contract: i18n keys are derived from ``action``, not action_label.

    Frontend (F1) builds:
      token = action.toLowerCase().replace(/[\\s-]+/g, '_')
      t(`act.approval.action.${token}`, { defaultValue: action_label || action })

    ``action_label`` is human-readable fallback only (Discord / missing key).
    Deriving keys from action_label fixed a pre-F1 bridge bug into the contract
    and left unmapped actions (e.g. delete) untested — capture 09 then showed
    the raw key ``act.approval.action.delete``.

    Cover every action PermissionGateway can surface: mapped labels (list /
    read / write) plus at least the unmapped ``delete`` exercised by the mock
    and real pending responses.
    """
    import re

    from latticeai.api.permissions import _PERMISSION_ACTION_LABELS

    i18n_keys = _workspace_i18n_keys()
    assert i18n_keys, "workspace.ts must define act.approval.action.* keys"

    # Mapped enum + unmapped actions that actually flow through pending.
    actions = set(_PERMISSION_ACTION_LABELS.keys()) | {"delete"}

    missing: list[str] = []
    for action in sorted(actions):
        # F1: key token comes from ``action``, not action_label.
        token = re.sub(r"[\s-]+", "_", str(action).lower())
        key = f"act.approval.action.{token}"
        if key not in i18n_keys:
            missing.append(f"action={action!r} → {key!r}")
    assert not missing, (
        "action-derived i18n keys missing from frontend/src/i18n/workspace.ts "
        "(F1 builds act.approval.action.<action>; t() has no defaultValue). "
        "Missing:\n  - "
        + "\n  - ".join(missing)
    )


def test_mock_activity_runs_and_health_summary_key_superset():
    """Mock keys for activity runs + health-summary ⊆ real router response keys.

    Failure mode: UI authored against a mock that invents fields (or omits
    issue_count) looks green in capture while production shows wrong counts.
    """
    mock_src = _mock_server_source()
    mock_runs = _extract_mock_json_object(mock_src, "/api/activity/runs")
    mock_health = _extract_mock_json_object(mock_src, "/admin/health-summary")

    # ── /api/activity/runs ──────────────────────────────────────────────
    class _Store:
        def list_combined_runs(self, *, limit=20, workspace_id=None):
            return {
                "runs": [
                    {
                        "id": "wf-run-approval",
                        "source": "workflow",
                        "title": "Agent Review Workflow",
                        "status": "awaiting_approval",
                        "started_at": "2026-06-06T12:05:00",
                        "finished_at": None,
                        "can_stop": False,
                        "can_resume": True,
                        "workflow_id": "wf-agent-review",
                    },
                    {
                        "id": "agent-run-1",
                        "source": "agent",
                        "title": "Summarize release",
                        "status": "ok",
                        "started_at": "2026-06-06T12:30:00",
                        "finished_at": "2026-06-06T12:31:00",
                        "can_stop": False,
                        "can_resume": False,
                        "agent_id": "agent:executor",
                    },
                ],
                "total": 2,
                "truncated": False,
            }

        def list_workflows(self, workspace_id=None):
            return {"workflows": []}

    store = _Store()
    service = AutomationIntelligenceService(store=store)
    runs_app = FastAPI()
    runs_app.include_router(
        create_automation_intelligence_router(
            service=service,
            store=store,
            require_user=lambda _request: "user@example.com",
            gate_read=lambda _request: "personal",
            gate_write=lambda _request: "personal",
            append_audit_event=lambda *args, **kwargs: None,
            workspace_graph=lambda: None,
        )
    )
    real_runs = TestClient(runs_app).get("/api/activity/runs", params={"limit": 20}).json()

    mock_run_keys = _deep_key_set(mock_runs)
    real_run_keys = _deep_key_set(real_runs)
    # Top-level + first-row keys the capture UI reads must exist on both sides;
    # real may add fields the mock omits (superset).
    required_top = {"runs", "total", "truncated"}
    assert required_top.issubset(mock_runs.keys())
    assert required_top.issubset(real_runs.keys())
    mock_row_keys = set(mock_runs["runs"][0].keys()) if mock_runs.get("runs") else set()
    real_row_keys: set[str] = set()
    for row in real_runs.get("runs") or []:
        real_row_keys |= set(row.keys())
    assert mock_row_keys.issubset(real_row_keys), (
        f"mock activity-run row keys {mock_row_keys} not ⊆ real {real_row_keys}"
    )
    assert any(r.get("status") == "awaiting_approval" for r in mock_runs["runs"]), (
        "mock /api/activity/runs must include awaiting_approval for capture 09"
    )
    # Source domain must match product enum.
    for row in list(mock_runs["runs"]) + list(real_runs["runs"]):
        assert row.get("source") in {"agent", "workflow"}, row

    # ── /admin/health-summary ───────────────────────────────────────────
    live_users = {
        "admin@example.com": {"role": "admin", "disabled": False},
        "disabled@example.com": {"role": "user", "disabled": True},
    }
    health_app = FastAPI()
    health_app.include_router(
        create_admin_router(
            require_admin=lambda _request: ("admin@example.com", live_users),
            require_user=lambda _request: "admin@example.com",
            load_users=lambda: live_users,
            save_users=lambda _users: None,
            get_user_role=lambda email, _users: (_users.get(email) or {}).get("role", "user"),
            get_history=lambda: [],
            get_audit_log=lambda: [],
            public_user=lambda email, user, _users: {"email": email, **user},
            load_vpc_config=lambda: {},
            save_vpc_config=lambda _cfg: None,
            build_admin_audit_report=lambda _users, events: {"recent_events": events},
            build_sensitivity_report=lambda _history: {
                "summary": {"severity_counts": {"high": 0}, "risky_messages": 0}
            },
            append_audit_event=lambda *args, **kwargs: None,
            public_sso_config=lambda: {},
            save_sso_config=lambda _cfg: None,
            get_graph_stats=lambda: {"nodes": 1},
            enable_graph=True,
            invite_code="x",
            invite_gate_enabled=False,
            default_port=4825,
            product_hardening_status=lambda: {"startup": {"network_exposed": False}},
        )
    )
    real_health = TestClient(health_app).get("/admin/health-summary").json()

    mock_health_keys = set(mock_health.keys())
    real_health_keys = set(real_health.keys())
    assert mock_health_keys.issubset(real_health_keys), (
        f"mock health-summary keys {mock_health_keys} not ⊆ real {real_health_keys}"
    )
    assert {"status", "issue_count", "issues"}.issubset(real_health_keys)
    assert isinstance(real_health["issue_count"], int)
    assert real_health["issue_count"] == len(real_health["issues"])
    assert mock_health.get("status") in {"ok", "attention"}
    assert real_health.get("status") in {"ok", "attention"}
    # Mock is intentionally "attention" so capture 10 shows the non-ok layout.
    assert mock_health.get("status") == "attention"
    assert isinstance(mock_health.get("issue_count"), int)
    assert mock_health["issue_count"] == len(mock_health.get("issues") or [])

    # Silence unused helpers when only top-level keys matter for health.
    assert mock_run_keys  # parsed successfully
    assert real_run_keys


def test_prepare_stream_emits_load_before_smoke_test(monkeypatch):
    """Install UI stage order is install → download → load → validate.

    The stream must emit ``load`` before ``smoke_test`` (frontend maps
    smoke_test → validate). Without this gate the UI can reverse the order
    and every other suite still passes.
    """
    import asyncio
    import json
    import re

    from latticeai.services import model_loading

    class _Resolution:
        def __init__(self, model_id, engine=None, user_email=None, engine_aliases=None):
            self.load_id = model_id
            self.engine = engine
            self.user_email = user_email
            self.actual_current = None

        @classmethod
        def from_request(cls, model_id, *, engine=None, user_email=None, engine_aliases=None):
            return cls(model_id, engine=engine, user_email=user_email)

        def update_after_load(self, *, actual_current):
            self.actual_current = actual_current

        def to_dict(self):
            return {"load_id": self.load_id, "actual_current": self.actual_current}

    class _Router:
        def __init__(self):
            self.current_model_id = "local_mlx:some-model"
            self.load_calls = 0

        async def load_model(self, model_id, adapter_path, **kwargs):
            self.load_calls += 1
            self.current_model_id = model_id
            return f"loaded {model_id}"

    def _progress(stage, message, **kwargs):
        payload: Dict[str, Any] = {"stage": stage, "message": message}
        for key, value in kwargs.items():
            if value is not None:
                payload[key] = value
        return payload

    async def _smoke(resolution, api_key_override=None):
        return {"ok": True, "status": "ok"}

    router = _Router()
    deps = {
        "normalize_local_model_request": lambda mid, engine: mid,
        "_ModelResolution": _Resolution,
        "parse_model_ref": lambda mid: ("local_mlx", mid.split(":", 1)[-1])
        if ":" in mid
        else ("local_mlx", mid),
        "_model_runtime_compatibility": lambda model, engine=None: {"supported": True},
        "engine_installed": lambda provider: True,
        "_download_allowed": lambda allow: True,
        "_engine_install_block": lambda provider: None,
        "ensure_engine_ready": lambda provider: {"installed_now": False},
        "hf_model_ready": lambda model, engine: True,
        "_download_block": lambda provider, model: None,
        "download_hf_model": lambda model, engine, progress_emit=None: {
            "provider": engine,
            "model": model,
            "cached": True,
        },
        "ensure_ollama_server": lambda: None,
        "local_binary": lambda name: f"/usr/bin/{name}",
        "get_ollama_pulled_models": lambda: [],
        "ensure_vllm_server": lambda model: None,
        "ensure_llamacpp_server": lambda model: None,
        "get_lmstudio_models": lambda: [],
        "ensure_lmstudio_model": lambda model: {"instance_id": model},
        "get_current_user": lambda request: "me@local",
        "get_user_api_key": lambda email, provider: None,
        "router": router,
        "_smoke_test_loaded_model": _smoke,
        "MODEL_ENGINE_ALIASES": {},
        "_friendly_model_runtime_error": lambda exc, **kw: str(exc),
        "hf_model_dir": lambda model: Path("/tmp/models") / model,
        "model_download_progress_payload": _progress,
        "get_lmstudio_models_raw": lambda: [],
        "pull_ollama_model_with_progress": lambda *a, **k: None,
    }
    monkeypatch.setattr(model_loading, "_get_model_runtime_deps", lambda state: deps)

    async def _collect() -> List[str]:
        stages: List[str] = []
        async for frame in model_loading.prepare_and_load_model_stream(
            "local_mlx:some-model",
            request=object(),
            runtime_state=object(),
            allow_download=True,
        ):
            # SSE frames: event: progress\ndata: {...}\n\n
            for match in re.finditer(r"data: (.+?)(?:\n\n|\n$)", frame, re.DOTALL):
                try:
                    payload = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                stage = payload.get("stage")
                if isinstance(stage, str) and stage:
                    stages.append(stage)
        return stages

    stages = asyncio.run(_collect())
    assert "load" in stages, f"stream must emit load; got {stages}"
    assert "smoke_test" in stages, f"stream must emit smoke_test; got {stages}"
    assert stages.index("load") < stages.index("smoke_test"), (
        f"load must precede smoke_test (frontend maps smoke_test→validate); got {stages}"
    )
    # After smoke_test comes done; never reverse load/validate again.
    assert stages.index("smoke_test") < stages.index("done") if "done" in stages else True
    assert router.load_calls == 1

