"""Schemas for AI provider messages, tool calling, and health status."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from pydantic import BaseModel


@dataclass
class AIHealth:
    healthy: bool
    model: str
    provider: str
    endpoint: str
    vision_supported: bool
    tools_supported: bool
    error: str | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class InferenceResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    max_tokens: int | None = None
