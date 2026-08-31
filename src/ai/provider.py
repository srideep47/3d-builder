"""AI Provider abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .schemas import AIHealth, ChatMessage, InferenceResult, ToolCall


class AIProvider(ABC):
    @abstractmethod
    def health(self) -> AIHealth:
        """Check provider endpoint and model availability."""
        ...

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        role: str = "general",
    ) -> InferenceResult:
        """Execute chat completion with optional tool definitions."""
        ...

    @abstractmethod
    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        role: str = "analyst",
    ) -> tuple[str, dict[str, Any] | None]:
        """Convenience method to request structured JSON output."""
        ...
