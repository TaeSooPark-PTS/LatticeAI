"""Admin API router: dashboard, users, VPC, SSO, audit, permissions."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from latticeai.core.workspace_os import DEFAULT_WORKSPACE_ID


class AdminUserUpdate(BaseModel):
    role: Optional[str] = None
    disabled: Optional[bool] = None


class VpcConfigUpdate(BaseModel):
    provider: Optional[str] = None
    region: Optional[str] = None
    cidr_block: Optional[str] = None
    private_subnets: Optional[List[str]] = None
    endpoint: Optional[str] = None
    vpn_status: Optional[str] = None
    peering_status: Optional[str] = None
    notes: Optional[str] = None


class SsoConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    provider_name: Optional[str] = None
    discovery_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: Optional[str] = None


def create_admin_router(
    *,
    require_admin: Callable,
    require_user: Callable,
    load_users: Callable,
    save_users: Callable,
    get_user_role: Callable,
    get_history: Callable,
    get_audit_log: Callable,
    public_user: Callable,
    load_vpc_config: Callable,
    save_vpc_config: Callable,
    build_admin_audit_report: Callable,
    build_sensitivity_report: Callable,
    append_audit_event: Callable,
    public_sso_config: Callable,
    save_sso_config: Callable,
    get_graph_stats: Callable,
    enable_graph: bool,
    invite_code: str,
    invite_gate_enabled: bool,
    default_port: int,
    policy_matrix: Optional[Callable[[], List[Dict[str, object]]]] = None,
    product_hardening_status: Optional[Callable[[], Dict[str, object]]] = None,
) -> APIRouter:
    router = APIRouter()

    def _workspace_scope(request: Request) -> Optional[str]:
        header = request.headers.get("X-Workspace-Id")
        if header and header.strip():
            return header.strip()
        query = request.query_params.get("workspace_id")
        return query.strip() if query and query.strip() else None

    def _matches_scope(item: Dict[str, object], workspace_id: Optional[str]) -> bool:
        if not workspace_id:
            return True
        item_scope = item.get("workspace_id")
        if not item_scope and workspace_id == DEFAULT_WORKSPACE_ID:
            return True
        return str(item_scope or "") == str(workspace_id)

    def _scoped_history(request: Request) -> List[Dict]:
        scope = _workspace_scope(request)
        return [item for item in get_history() if _matches_scope(item, scope)]

    def _scoped_audit_log(request: Request) -> List[Dict]:
        scope = _workspace_scope(request)
        return [item for item in get_audit_log() if _matches_scope(item, scope)]

    def _filter_audit_log(
        events: List[Dict],
        *,
        q: Optional[str] = None,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        needle = (q or "").strip().lower()
        actor_filter = (actor or "").strip().lower()
        action_filter = (action or "").strip().lower()
        severity_filter = (severity or "").strip().lower()

        def matches(event: Dict) -> bool:
            public = _event_public_text(event)
            if needle and needle not in public:
                return False
            if actor_filter and actor_filter not in str(event.get("user_email") or event.get("actor") or "").lower():
                return False
            event_action = str(event.get("event_type") or event.get("action") or "").lower()
            if action_filter and action_filter not in event_action:
                return False
            event_severity = str(event.get("severity") or event.get("sev") or "").lower()
            if severity_filter and event_severity != severity_filter:
                return False
            return True

        capped_limit = max(1, min(int(limit or 50), 250))
        return [event for event in events if matches(event)][-capped_limit:]

    def _event_public_text(event: Dict) -> str:
        parts = [
            event.get("event_type"),
            event.get("action"),
            event.get("user_email"),
            event.get("actor"),
            event.get("target"),
            event.get("target_email"),
            event.get("workspace_id"),
            event.get("severity"),
            event.get("sev"),
        ]
        return " ".join(str(part).lower() for part in parts if part is not None)

    def _parse_timestamp(value: object) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    @router.get("/admin/summary")
    async def admin_summary(request: Request):
        _, users = require_admin(request)
        history = _scoped_history(request)
        user_msgs = [i for i in history if i.get("role") == "user"]
        asst_msgs = [i for i in history if i.get("role") == "assistant"]
        return {
            "total_users": len(users),
            "active_users": sum(1 for u in users.values() if not u.get("disabled")),
            "admin_users": sum(1 for e in users if get_user_role(e, users) == "admin"),
            "total_messages": len(history),
            "user_messages": len(user_msgs),
            "assistant_messages": len(asst_msgs),
            "last_message_at": history[-1].get("timestamp") if history else None,
        }

    @router.get("/admin/health-summary")
    async def admin_health_summary(request: Request):
        """One-line admin health for the calm console header (layout rebuild).

        Aggregates existing admin surfaces — no new persistence. ``status`` is
        ``attention`` when any issue is present, else ``ok``.
        """
        _, users = require_admin(request)
        issues: List[Dict[str, Any]] = []

        disabled = sum(1 for user in users.values() if user.get("disabled"))
        if disabled:
            issues.append({
                "area": "users",
                "severity": "warning",
                "message": f"{disabled} disabled user(s)",
            })

        try:
            report = build_sensitivity_report(_scoped_history(request)) or {}
            severity = ((report.get("summary") or {}).get("severity_counts") or {})
            high = int(severity.get("high") or 0)
            if high:
                issues.append({
                    "area": "security",
                    "severity": "high",
                    "message": f"{high} high-risk event(s)",
                })
        except Exception as exc:
            logging.warning("admin health-summary sensitivity failed: %s", exc)

        if enable_graph:
            try:
                stats = get_graph_stats() or {}
                if isinstance(stats, dict) and stats.get("error"):
                    issues.append({
                        "area": "brain_ops",
                        "severity": "warning",
                        "message": "Knowledge graph unavailable",
                    })
            except Exception as exc:
                issues.append({
                    "area": "brain_ops",
                    "severity": "warning",
                    "message": str(exc)[:160],
                })

        if product_hardening_status is not None:
            try:
                hardening = product_hardening_status() or {}
                startup = hardening.get("startup") or {}
                if startup.get("network_exposed"):
                    issues.append({
                        "area": "runtime_trust",
                        "severity": "warning",
                        "message": "Server is network-exposed",
                    })
            except Exception as exc:
                logging.warning("admin health-summary hardening failed: %s", exc)

        return {
            "status": "attention" if issues else "ok",
            "issue_count": len(issues),
            "issues": issues,
        }

    @router.get("/admin/stats")
    async def admin_stats(request: Request):
        require_admin(request)
        history = _scoped_history(request)
        daily: dict = defaultdict(lambda: {"user": 0, "assistant": 0})
        for item in history:
            ts = item.get("timestamp", "")
            day = ts[:10] if ts else "unknown"
            role = item.get("role", "")
            if role in ("user", "assistant"):
                daily[day][role] += 1
        sorted_days = sorted(daily.keys())[-14:]
        return {"daily": [{"date": d, "user": daily[d]["user"], "assistant": daily[d]["assistant"]} for d in sorted_days]}

    @router.get("/admin/users")
    async def admin_users(request: Request):
        _, users = require_admin(request)
        return [public_user(email, user, users) for email, user in users.items()]

    @router.get("/admin/sensitivity")
    async def admin_sensitivity(request: Request):
        require_admin(request)
        return build_sensitivity_report(_scoped_history(request))

    @router.get("/admin/audit")
    async def admin_audit(
        request: Request,
        q: Optional[str] = Query(None),
        actor: Optional[str] = Query(None),
        action: Optional[str] = Query(None),
        severity: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=250),
    ):
        _, users = require_admin(request)
        scoped_events = _scoped_audit_log(request)
        filtered_events = _filter_audit_log(
            scoped_events,
            q=q,
            actor=actor,
            action=action,
            severity=severity,
            limit=limit,
        )
        report = build_admin_audit_report(users, filtered_events)
        report["filters"] = {
            "q": q or "",
            "actor": actor or "",
            "action": action or "",
            "severity": severity or "",
            "limit": limit,
            "matched_events": len(filtered_events),
            "scoped_events": len(scoped_events),
        }
        try:
            report["graph"] = get_graph_stats() if enable_graph else {"disabled": True}
        except Exception as e:
            logging.warning("knowledge graph stats for audit failed: %s", e)
            report["graph"] = {"error": str(e)}
        return report

    @router.get("/admin/roles")
    async def admin_roles(request: Request):
        _, users = require_admin(request)
        counts: Dict[str, int] = defaultdict(int)
        for email, user in users.items():  # noqa: B007 — the key is the payload; the loop var documents the shape
            role = (get_user_role(email, users) or "user").lower()
            counts[role] += 1
        matrix: List[Dict[str, Any]] = policy_matrix() if policy_matrix else [
            {"role": "admin", "caps": ["all"]},
            {"role": "user", "caps": ["chat", "search"]},
        ]
        policy_caps = {
            str(item.get("role") or "user"): list(item.get("caps") or [])
            for item in matrix
            if isinstance(item, dict)
        }
        for role in policy_caps:
            counts.setdefault(role, 0)
        order = {"owner": 0, "admin": 1, "member": 2, "user": 3, "viewer": 4}
        roles = [
            {"role": role, "members": counts.get(role, 0), "caps": policy_caps.get(role, [])}
            for role in sorted(counts, key=lambda r: (order.get(r, 99), r))
        ]
        return {"roles": roles}

    @router.get("/admin/policies")
    async def admin_policies(request: Request):
        require_admin(request)
        # The real, enforced governance posture of a local-first deployment.
        return {
            "policies": [
                {"id": "local_file_access", "label": "Local file access",
                 "value": "Approval-token gated (per path/user/action)", "enforced": True},
                {"id": "package_install", "label": "Package install",
                 "value": "Admin-only with audit trail", "enforced": True},
                {"id": "data_residency", "label": "Data residency",
                 "value": "Single-tenant local storage (~/.ltcai)", "enforced": True},
                {"id": "model_egress", "label": "Model egress",
                 "value": "Local-only by default (no external inference in local mode)", "enforced": True},
                {"id": "invite_gate", "label": "Invite gate",
                 "value": "Signed access gate" if invite_gate_enabled else "Disabled",
                 "enforced": bool(invite_gate_enabled)},
                {"id": "log_retention", "label": "Log retention",
                 "value": "90 day local audit window with manual export before pruning", "enforced": True},
            ]
        }

    @router.get("/admin/log-retention")
    async def admin_log_retention(request: Request):
        require_admin(request)
        events = _scoped_audit_log(request)
        retention_days = 90
        cutoff = datetime.now() - timedelta(days=retention_days)
        retained = 0
        prune_candidates = 0
        for event in events:
            ts = _parse_timestamp(event.get("timestamp") or event.get("ts"))
            if ts and ts < cutoff:
                prune_candidates += 1
            else:
                retained += 1
        return {
            "mode": "local-first",
            "retention_days": retention_days,
            "total_events": len(events),
            "retained_events": retained,
            "prune_candidates": prune_candidates,
            "export_before_prune": True,
            "editable": False,
            "reason": "Retention is reported in Community mode; destructive pruning requires an explicit export workflow.",
        }

    @router.get("/admin/product-hardening")
    async def admin_product_hardening(request: Request):
        require_admin(request)
        if product_hardening_status is None:
            return {
                "available": False,
                "reason": "Product hardening status provider is not configured.",
            }
        return product_hardening_status()

    @router.get("/vpc/status")
    async def vpc_status(request: Request):
        require_user(request)
        return load_vpc_config()

    @router.patch("/admin/vpc")
    async def admin_update_vpc(req: VpcConfigUpdate, request: Request):
        require_admin(request)
        config = load_vpc_config()
        update = req.dict(exclude_unset=True)
        if "private_subnets" in update and update["private_subnets"] is not None:
            update["private_subnets"] = [s.strip() for s in update["private_subnets"] if s.strip()]
        config.update(update)
        save_vpc_config(config)
        return config

    @router.patch("/admin/users/{email:path}")
    async def admin_update_user(email: str, req: AdminUserUpdate, request: Request):
        admin_email, users = require_admin(request)
        if email not in users:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        before = public_user(email, users[email], users)
        if req.role is not None:
            if req.role not in {"admin", "user"}:
                raise HTTPException(status_code=400, detail="role은 admin 또는 user만 가능합니다.")
            users[email]["role"] = req.role
        if req.disabled is not None:
            if email == admin_email and req.disabled:
                raise HTTPException(status_code=400, detail="자기 자신은 비활성화할 수 없습니다.")
            users[email]["disabled"] = req.disabled
        save_users(users)
        after = public_user(email, users[email], users)
        append_audit_event("user_update", user_email=admin_email, target_email=email, before=before, after=after)
        return after

    @router.delete("/admin/users/{email:path}")
    async def admin_delete_user(email: str, request: Request):
        admin_email, users = require_admin(request)
        if email == admin_email:
            raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")
        if email not in users:
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        deleted = public_user(email, users[email], users)
        append_audit_event("user_delete", user_email=admin_email, target_email=email, deleted_user=deleted)
        del users[email]
        save_users(users)
        return {"status": "ok", "deleted": deleted}

    @router.get("/admin/invite-link")
    async def admin_invite_link(request: Request):
        require_admin(request)
        host = request.headers.get("host", f"localhost:{default_port}")
        scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
        url = f"{scheme}://{host}/?code={invite_code}" if invite_gate_enabled else f"{scheme}://{host}/"
        return {"invite_url": url, "invite_code": invite_code, "gate_enabled": invite_gate_enabled}

    @router.get("/admin/sso")
    async def admin_sso(request: Request):
        require_admin(request)
        return public_sso_config()

    @router.patch("/admin/sso")
    async def admin_update_sso(req: SsoConfigUpdate, request: Request):
        admin_email, _ = require_admin(request)
        update = req.dict(exclude_unset=True)
        saved = save_sso_config(update)
        append_audit_event(
            "sso_config_update",
            user_email=admin_email,
            provider_name=saved.get("provider_name"),
            discovery_url=saved.get("discovery_url"),
            enabled=bool(saved.get("enabled")),
        )
        return public_sso_config(saved)

    @router.get("/admin/enterprise")
    async def admin_enterprise_overview(request: Request):
        """Enterprise PoC surface: edition matrix, admin policies, audit export,
        SIEM stub, and org-governance capabilities. Community reports every
        Enterprise capability as disabled and never gates Community features."""
        require_admin(request)
        from latticeai.core.enterprise_admin import poc_overview
        return poc_overview()

    @router.get("/admin/enterprise/siem-export")
    async def admin_enterprise_siem_export(request: Request):
        """Preview the SIEM export envelope. In Community this is a stub
        (``streamed=false``) — no events are pushed to an external SIEM."""
        require_admin(request)
        from latticeai.core.enterprise_admin import siem_export_stub
        return siem_export_stub()

    return router
