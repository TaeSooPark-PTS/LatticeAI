"""Stable request contracts shared by the chat HTTP submodules."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    client_url: Optional[str] = None
    model: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.2
    stream: bool = True
    context: Optional[str] = None
    source: Optional[str] = None
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    image_data: Optional[str] = None
    allow_file_context: bool = False


class AgentRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    source: Optional[str] = None
    max_steps: int = 25
    temperature: float = 0.1
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    workspace_id: Optional[str] = None
    planning_model: Optional[str] = None
    executing_model: Optional[str] = None
    reviewing_model: Optional[str] = None
    human_in_loop: bool = False


class AgentResumeRequest(BaseModel):
    # Legacy human-in-loop pause (kept working): context_id + approved.
    context_id: Optional[str] = None
    approved: bool = True
    modified_plan: Optional[dict] = None
    executing_model: Optional[str] = None
    reviewing_model: Optional[str] = None
    # awaiting_approval flow (v9.10): run_id + short-TTL approval token.
    run_id: Optional[str] = None
    approval_token: Optional[str] = None
    approve: Optional[bool] = None
    edited_plan: Optional[dict] = None


class AgentEvalRequest(BaseModel):
    skill: str
    case_id: Optional[str] = None


__all__ = ["AgentEvalRequest", "AgentRequest", "AgentResumeRequest", "ChatRequest"]
