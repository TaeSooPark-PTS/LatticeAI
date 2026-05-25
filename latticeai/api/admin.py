"""Admin API router: dashboard, users, VPC, SSO, audit, permissions."""

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


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
) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/summary")
    async def admin_summary(request: Request):
        _, users = require_admin(request)
        history = get_history()
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

    @router.get("/admin/stats")
    async def admin_stats(request: Request):
        require_admin(request)
        history = get_history()
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
        return build_sensitivity_report(get_history())

    @router.get("/admin/audit")
    async def admin_audit(request: Request):
        _, users = require_admin(request)
        report = build_admin_audit_report(users)
        try:
            report["graph"] = get_graph_stats() if enable_graph else {"disabled": True}
        except Exception as e:
            logging.warning("knowledge graph stats for audit failed: %s", e)
            report["graph"] = {"error": str(e)}
        return report

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

    return router
