"""Plugin SDK API router (v2).

Surfaces the :class:`latticeai.core.plugins.PluginRegistry` over HTTP using the
same router-factory convention as the rest of ``latticeai.api`` (server_app
constructs the dependencies and passes them in; this module never imports the
app). New paths are namespaced under ``/plugins/registry`` and friends so they
do not collide with the pre-existing ``/plugins/directory`` marketplace routes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from latticeai.api.ui_redirects import app_redirect


class PluginActionRequest(BaseModel):
    plugin_id: str
    enabled: Optional[bool] = None
    version: Optional[str] = None


class PluginValidateRequest(BaseModel):
    manifest: Dict[str, Any] = {}


class PluginExecuteRequest(BaseModel):
    plugin_id: str
    action: str
    args: Dict[str, Any] = {}


def create_plugins_router(
    *,
    registry,
    require_user: Callable[[Request], str],
    require_admin: Callable[[Request], Any],
    append_audit_event: Callable[..., None],
    gate_write: Optional[Callable[[Request], Optional[str]]] = None,
    register_skill: Optional[Callable[[str, str], Any]] = None,
    plugin_runners_factory: Optional[
        Callable[[str, Optional[str]], Dict[str, Callable[..., Any]]]
    ] = None,
    ui_file_response: Optional[Callable[[Path], Any]] = None,
    static_dir: Optional[Path] = None,
) -> APIRouter:
    from latticeai.core.plugins import validate_manifest

    router = APIRouter()

    @router.get("/plugins/sdk")
    async def plugins_sdk_page(request: Request):
        require_user(request)
        return app_redirect("marketplace", request)

    @router.get("/plugins/registry")
    async def plugins_registry(request: Request):
        require_user(request)
        return registry.catalog()

    @router.get("/plugins/registry/{plugin_id}")
    async def plugin_detail(plugin_id: str, request: Request):
        require_user(request)
        manifest = registry.get_manifest(plugin_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
        state = registry.store.list_plugin_registry().get(plugin_id, {}) if registry.store else {}
        return {"plugin": manifest.public(), "registry": state}

    @router.post("/plugins/validate")
    async def plugin_validate(req: PluginValidateRequest, request: Request):
        require_user(request)
        manifest, errors = validate_manifest(req.manifest)
        return {"ok": not errors, "errors": errors, "manifest": manifest.public() if manifest else None}

    @router.post("/plugins/install")
    async def plugin_install(req: PluginActionRequest, request: Request):
        admin_email, _ = require_admin(request)
        try:
            result = registry.install(req.plugin_id, register_skill=register_skill)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("plugin_install", user_email=admin_email, plugin=req.plugin_id)
        return result

    @router.post("/plugins/uninstall")
    async def plugin_uninstall(req: PluginActionRequest, request: Request):
        admin_email, _ = require_admin(request)
        result = registry.uninstall(req.plugin_id)
        append_audit_event("plugin_uninstall", user_email=admin_email, plugin=req.plugin_id)
        return result

    @router.post("/plugins/enable")
    async def plugin_enable(req: PluginActionRequest, request: Request):
        admin_email, _ = require_admin(request)
        plugin = registry.set_enabled(req.plugin_id, True)
        append_audit_event("plugin_enable", user_email=admin_email, plugin=req.plugin_id)
        return {"plugin": plugin}

    @router.post("/plugins/disable")
    async def plugin_disable(req: PluginActionRequest, request: Request):
        admin_email, _ = require_admin(request)
        plugin = registry.set_enabled(req.plugin_id, False)
        append_audit_event("plugin_disable", user_email=admin_email, plugin=req.plugin_id)
        return {"plugin": plugin}

    @router.post("/plugins/execute")
    async def plugin_execute(req: PluginExecuteRequest, request: Request):
        current_user = require_user(request)
        scope = gate_write(request) if gate_write is not None else None
        runners = plugin_runners_factory(current_user, scope) if plugin_runners_factory else {}
        result = registry.execute_action(
            req.plugin_id,
            req.action,
            req.args,
            runners=runners,
            workspace_id=scope,
        )
        append_audit_event("plugin_execute", user_email=current_user, plugin=req.plugin_id, action=req.action, status=result.status)
        return result.as_dict()

    return router
