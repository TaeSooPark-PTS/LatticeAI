"""Brain Network API — device identity, peer pairing, knowledge exchange.

The /network/receive endpoint authenticates PEERS (signed device requests),
not user sessions; everything else requires a logged-in user, and pairing /
pushing are deliberate owner actions.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class PeerPairRequest(BaseModel):
    name: str
    base_url: str
    public_key: str


class PeerPushRequest(BaseModel):
    workspace_id: Optional[str] = None


def create_network_router(*, network, identity, require_user, require_admin) -> APIRouter:
    router = APIRouter()

    @router.get("/network/identity")
    async def network_identity(request: Request):
        require_user(request)
        return identity.describe()

    @router.get("/network/peers")
    async def network_peers(request: Request):
        require_admin(request)
        return {"peers": network.list_peers()}

    @router.post("/network/peers")
    async def network_pair(req: PeerPairRequest, request: Request):
        require_admin(request)
        try:
            return {"status": "paired", "peer": network.add_peer(
                name=req.name, base_url=req.base_url, public_key=req.public_key,
            )}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/network/peers/{peer_id}")
    async def network_unpair(peer_id: str, request: Request):
        require_admin(request)
        try:
            return network.remove_peer(peer_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown peer: {exc}") from exc

    @router.post("/network/push/{peer_id}")
    async def network_push(peer_id: str, req: PeerPushRequest, request: Request):
        require_admin(request)
        try:
            return network.push_to_peer(peer_id, workspace_id=req.workspace_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown peer: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Push failed: {exc}") from exc

    @router.post("/network/receive")
    async def network_receive(request: Request):
        # Peer-authenticated: a paired device's signature replaces the session.
        body = await request.body()
        try:
            return network.receive(dict(request.headers), body)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


__all__ = ["create_network_router"]
