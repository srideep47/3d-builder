"""Aptos GLM-5.3 AI Provider — integrates with OpenAI-compatible vLLM endpoint."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any
import httpx
import yaml
from openai import OpenAI

from .inference_log import InferenceLog
from .provider import AIProvider
from .schemas import AIHealth, ChatMessage, InferenceResult, ToolCall
from .vision_probe import probe_vision_support

DEFAULT_AI_CONFIG = Path(__file__).resolve().parents[2] / "config" / "ai.yaml"

_IMAGE_MIME = {
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
    "webp": "webp",
    "gif": "gif",
    "bmp": "bmp",
}


def vision_user_content(text: str, image_paths: list[str | Path]) -> list[dict[str, Any]]:
    """Build an OpenAI-style multimodal user content block: text + image parts."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for p in image_paths:
        path = Path(p)
        ext = path.suffix.lower().lstrip(".") or "png"
        mime = _IMAGE_MIME.get(ext, "png")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}})
    return parts


def load_ai_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_AI_CONFIG
    if p.exists():
        try:
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    return {
        "provider": "aptos",
        "base_url": "https://host0.inference.aptoslabs.com/v1",
        "model_id": "zai-org/GLM-5.3",
        "api_key_env": "THREED_API_KEY",
        "timeout_sec": 120,
        "temperature": 0.1,
    }


def extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Robust extractor for JSON objects from LLM response text."""
    clean = text.strip()
    if "```" in clean:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    try:
        return json.loads(clean)
    except Exception:
        pass
    # Find widest outer curly braces
    first_brace = clean.find("{")
    last_brace = clean.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(clean[first_brace : last_brace + 1])
        except Exception:
            pass
    return None


class AptosGLMProvider(AIProvider):
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        log: InferenceLog | None = None,
        api_key: str | None = None,
    ):
        self.config = config or load_ai_config()
        self.base_url = os.environ.get("APTOS_BASE_URL") or self.config.get("base_url") or "https://host0.inference.aptoslabs.com/v1"
        self.base_url = self.base_url.rstrip("/")

        env_key_name = self.config.get("api_key_env", "THREED_API_KEY")
        self.api_key = api_key or os.environ.get(env_key_name) or os.environ.get("APTOS_API_KEY") or "test"

        self.model_id = os.environ.get("APTOS_MODEL_ID") or self.config.get("model_id") or "zai-org/GLM-5.3"
        self.timeout = float(self.config.get("timeout_sec") or 120.0)
        # SDK-level retry for transient failures (429/5xx/connection): the
        # batch phase runs several agent loops against one endpoint
        # concurrently; the client's own exponential backoff absorbs bursts.
        self.max_retries = int(self.config.get("max_retries") or 4)
        self.default_temp = float(self.config.get("temperature") or 0.1)
        self.log = log or InferenceLog()

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        self._vision_cached: bool | None = None

    def supports_vision(self) -> bool:
        """Resolve vision capability once, honouring the config mode:
        auto = probe the endpoint, on = assume yes, off = assume no.
        (`vision` may be a plain string or a dict with a `mode` key — the
        dict form also carries the local-VLM config, see ai/vlm.py.)"""
        if self._vision_cached is not None:
            return self._vision_cached
        vision_cfg = self.config.get("vision", "auto")
        mode = str(vision_cfg.get("mode", "auto")).lower() if isinstance(vision_cfg, dict) else str(vision_cfg).lower()
        if mode == "on":
            self._vision_cached = True
        elif mode == "off":
            self._vision_cached = False
        else:
            self._vision_cached = probe_vision_support(self.base_url, self.model_id, self.api_key)
        return self._vision_cached

    def health(self) -> AIHealth:
        try:
            resp = httpx.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=10.0)
            if resp.status_code != 200:
                return AIHealth(
                    healthy=False,
                    model=self.model_id,
                    provider="aptos",
                    endpoint=self.base_url,
                    vision_supported=False,
                    tools_supported=False,
                    error=f"HTTP {resp.status_code}: {resp.text}",
                )

            data = resp.json()
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            if self._vision_cached is None:
                self._vision_cached = probe_vision_support(self.base_url, self.model_id, self.api_key)

            return AIHealth(
                healthy=True,
                model=self.model_id,
                provider="aptos",
                endpoint=self.base_url,
                vision_supported=self._vision_cached,
                tools_supported=True,
            )
        except Exception as e:
            return AIHealth(
                healthy=False,
                model=self.model_id,
                provider="aptos",
                endpoint=self.base_url,
                vision_supported=False,
                tools_supported=False,
                error=str(e),
            )

    def _resolve_role_params(
        self, role: str, temperature: float | None, max_tokens: int | None
    ) -> tuple[float, int, str | None]:
        """Explicit args win, then the per-role config, then the global config,
        then hardcoded defaults. GLM-5.3 is a reasoning model: budgets that are
        too small are exhausted by reasoning before any content is emitted, and
        `reasoning_effort` (e.g. "low") trades reasoning depth for ~30x lower
        latency — the verification loop catches quality drift either way."""
        role_cfg = dict((self.config.get("roles") or {}).get(role) or {})
        eff_temp = (
            temperature
            if temperature is not None
            else float(role_cfg.get("temperature", self.config.get("temperature", 0.1)))
        )
        eff_tokens = int(
            max_tokens
            if max_tokens is not None
            else role_cfg.get("max_tokens", self.config.get("max_tokens", 16384))
        )
        eff_effort = role_cfg.get("reasoning_effort", self.config.get("reasoning_effort"))
        return eff_temp, eff_tokens, eff_effort

    def chat(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        role: str = "general",
    ) -> InferenceResult:
        temperature, max_tokens, reasoning_effort = self._resolve_role_params(role, temperature, max_tokens)
        # Convert ChatMessage objects to OpenAI dicts
        formatted_messages = []
        for m in messages:
            if isinstance(m, ChatMessage):
                d: dict[str, Any] = {"role": m.role}
                if m.content is not None:
                    d["content"] = m.content
                if m.tool_calls:
                    d["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ]
                if m.tool_call_id:
                    d["tool_call_id"] = m.tool_call_id
                if m.name:
                    d["name"] = m.name
                formatted_messages.append(d)
            else:
                formatted_messages.append(m)

        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if reasoning_effort:
            kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        start_t = time.perf_counter()
        resp = self.client.chat.completions.create(**kwargs)
        latency_ms = (time.perf_counter() - start_t) * 1000.0

        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""

        parsed_tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                raw_args = tc.function.arguments
                try:
                    args_dict = json.loads(raw_args)
                except Exception:
                    args_dict = extract_json_from_text(raw_args) or {}
                parsed_tool_calls.append(ToolCall(id=tc.id, name=fn_name, arguments=args_dict))

        # Check for JSON-protocol fallback tool calls in text (whenever the
        # endpoint did not return native tool calls).
        if not parsed_tool_calls and content:
            candidate = extract_json_from_text(content)
            if isinstance(candidate, dict) and "tool" in candidate and "args" in candidate:
                parsed_tool_calls.append(
                    ToolCall(
                        id=f"call_{int(time.time()*1000)}",
                        name=candidate["tool"],
                        arguments=candidate.get("args", {}),
                    )
                )

        p_tokens = resp.usage.prompt_tokens if resp.usage else 0
        c_tokens = resp.usage.completion_tokens if resp.usage else 0

        # Log inference record
        self.log.log(
            role=role,
            messages=formatted_messages,
            response={"content": content, "tool_calls": [tc.model_dump() for tc in parsed_tool_calls]},
            latency_ms=latency_ms,
            model=self.model_id,
            meta={"prompt_tokens": p_tokens, "completion_tokens": c_tokens},
        )

        return InferenceResult(
            content=content,
            tool_calls=parsed_tool_calls,
            raw_response=resp.model_dump(),
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            latency_ms=latency_ms,
            finish_reason=choice.finish_reason,
            max_tokens=max_tokens,
        )

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        role: str = "analyst",
    ) -> tuple[str, dict[str, Any] | None]:
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_prompt),
        ]
        res = self.chat(messages=messages, temperature=temperature, max_tokens=max_tokens, role=role)
        parsed = extract_json_from_text(res.content)
        return res.content, parsed
