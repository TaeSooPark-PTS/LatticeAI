"""P-Reinforce garden routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel


class GardenRequest(BaseModel):
    raw_data: str
    category: Optional[str] = None


def create_garden_router(*, gardener, require_user) -> APIRouter:
    api_router = APIRouter()

    # ── P-Reinforce Knowledge Gardener ────────────────────────────────────────────
    
    @api_router.post("/garden")
    async def garden(req: GardenRequest, request: Request):
        """Raw 데이터를 P-Reinforce 구조로 자동 분류·저장"""
        require_user(request)
        result = await gardener.process(req.raw_data, req.category)
        return result
    
    
    @api_router.get("/garden/tree")
    async def garden_tree(request: Request):
        """지식 정원 파일트리 반환"""
        require_user(request)
        return gardener.get_tree()
    return api_router
