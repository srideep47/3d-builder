"""Vision providers — the analyst eye and the advisory visual gate
(PROJECT_PLAN §13.1).

Two wire protocols live behind one ``VisionProvider`` ABC (T5):

- ``OpenAICompatibleVisionProvider`` (``LocalVLMClient``): a local Qwen2.5-VL
  served via vLLM on Forge's 4080 Super — ``POST /chat/completions`` with
  Bearer auth and ``image_url`` content parts.
- ``GeminiVisionProvider``: Google Generative Language **v1beta** —
  ``POST /v1beta/models/<model>:generateContent`` with the
  ``x-goog-api-key`` header and a ``contents``/``parts`` body. This is NOT
  an OpenAI-shaped API, hence a second provider class rather than a
  modification of the first.

Selection is config-driven (``config/ai.yaml`` → ``vision.vlm.provider``:
``local`` | ``gemini``). The API key NEVER lives in config or code: it is
read from the environment (``api_key_env``, default ``THREED_VLM_API_KEY``;
the Gemini provider also falls back to ``GEMINI_API_KEY``, which holds the
same value). Gemini models must be PINNED versions (``gemini-3.6-flash``),
never ``-latest`` aliases.

Everything fails soft: an absent, misconfigured, or unreachable provider
leaves the pipeline fully functional.
"""

from __future__ import annotations

import abc
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from .aptos import DEFAULT_AI_CONFIG, extract_json_from_text

_IMAGE_MIME = {
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
    "webp": "webp",
}

# Keys are read from the environment only — never config files, never code.
DEFAULT_API_KEY_ENV = "THREED_VLM_API_KEY"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"  # holds the same value (owner setup)
GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


def _image_b64(path: Path) -> tuple[str, str]:
    """(mime_subtype, base64 payload) for an image file."""
    ext = path.suffix.lower().lstrip(".") or "png"
    mime = _IMAGE_MIME.get(ext, "png")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return mime, b64


class VisionProvider(abc.ABC):
    """One vision-chat wire protocol behind the two integration points.

    Subclasses own construction/config and the three abstract wire methods;
    the concrete integration points below (reference description for the
    text-only analyst, advisory render-vs-reference verdict) are shared
    because they only speak ``chat_vision``.
    """

    @staticmethod
    def _load_vision_config() -> dict[str, Any]:
        try:
            if DEFAULT_AI_CONFIG.exists():
                data = yaml.safe_load(DEFAULT_AI_CONFIG.read_text(encoding="utf-8")) or {}
                vision = data.get("vision", {})
                if isinstance(vision, dict):
                    return vision
        except Exception:
            pass
        return {}

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Config is present (endpoint + model [+ key]). No network."""

    @abc.abstractmethod
    def is_available(self, recheck: bool = False) -> bool:
        """Cached liveness probe."""

    @abc.abstractmethod
    def chat_vision(
        self,
        text: str,
        image_paths: list[str | Path],
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> str:
        """One vision chat round. Raises on HTTP errors; callers fail soft."""

    # ── Integration point 1: reference analysis for the analyst ────────────

    DESCRIBE_PROMPT = (
        "You are the eyes of a 3D modeling agent. Describe these reference "
        "image(s) for a CAD modeler in a structured, concrete way:\n"
        "1. Overall silhouette and object type\n"
        "2. Part decomposition — list every visible part with a short name\n"
        "3. Approximate proportions between parts (relative, e.g. 'seat is "
        "about 1.5x wider than deep')\n"
        "4. Materials and colors\n"
        "5. Distinctive features a modeler must not miss\n"
        "No measurements in absolute units — another system owns exact sizes."
    )

    def describe_reference_images(self, image_paths: list[str | Path]) -> str:
        # 4096, not 1600: on a real 12-photo reference set the describe call
        # truncated mid-sentence at 1600 (gemini-3.6-flash is a thinking
        # model — thoughtsTokenCount eats the budget before content, the
        # same lesson as GLM-5.3's reasoning tokens; HANDOFF_GLM §3 run).
        return self.chat_vision(self.DESCRIBE_PROMPT, image_paths, max_tokens=4096)

    # ── Integration point 2: advisory visual gate ──────────────────────────

    VERDICT_PROMPT = (
        "You are a Visual Tester for a 3D model pipeline. IMAGE SET 1 is the "
        "user's reference. IMAGE SET 2 contains studio renders (front/side/"
        "top/iso) of the generated 3D model. Compare them and answer with "
        "ONLY a JSON object:\n"
        '{{"matches_reference": true|false, "score": 0-10, "issues": ["..."], '
        '"summary": "one sentence"}}\n'
        "score: 0 = nothing alike, 10 = faithful. Issues are concrete visual "
        "differences (missing parts, wrong proportions, wrong placement)."
    )

    def visual_verdict(
        self,
        render_paths: dict[str, str],
        reference_paths: list[str | Path],
        model_summary: str = "",
    ) -> dict[str, Any]:
        """Compare renders against references. Returns a verdict dict:
        {available, matches_reference, score, issues, summary}."""
        if not self.is_available():
            return {"available": False, "reason": "vision provider not available"}
        try:
            all_paths = [str(p) for p in reference_paths] + [
                render_paths[k]
                for k in ("front", "side", "top", "iso")
                if render_paths.get(k)
            ]
            text = (
                self.VERDICT_PROMPT
                + "\n\nThe FIRST image(s) are the reference; the REMAINING "
                "images are the generated model's renders."
            )
            if model_summary:
                text += f"\nGenerated model summary: {model_summary}"
            raw = self.chat_vision(text, all_paths, max_tokens=3072)
            parsed = extract_json_from_text(raw) or self._loose_verdict(raw)
            if not parsed:
                return {"available": True, "parsed": False, "raw": raw[:800]}
            return {
                "available": True,
                "parsed": True,
                "matches_reference": bool(parsed.get("matches_reference")),
                "score": parsed.get("score"),
                "issues": parsed.get("issues") or [],
                "summary": parsed.get("summary"),
            }
        except Exception as e:
            return {"available": True, "parsed": False, "error": str(e)[:400]}

    @staticmethod
    def _loose_verdict(raw: str) -> dict | None:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


class OpenAICompatibleVisionProvider(VisionProvider):
    """OpenAI-compatible chat with image support (local vLLM/Qwen2.5-VL)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = self._load_vision_config()
        vlm_cfg = cfg.get("vlm", {}) or {}
        self.base_url = (base_url or vlm_cfg.get("base_url") or "").rstrip("/")
        self.model = model or vlm_cfg.get("model") or ""
        self.api_key = api_key or vlm_cfg.get("_api_key")  # resolved below
        self.timeout_sec = float(timeout_sec or vlm_cfg.get("timeout_sec", 120))
        if not self.api_key:
            self.api_key = (
                os.environ.get(vlm_cfg.get("api_key_env") or DEFAULT_API_KEY_ENV)
                or "sk-local"
            )
        self._available: bool | None = None

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def is_available(self, recheck: bool = False) -> bool:
        """Cached liveness probe (GET /models)."""
        if not self.is_configured():
            return False
        if self._available is not None and not recheck:
            return self._available
        try:
            r = httpx.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=5.0,
            )
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        return self._available

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def chat_vision(
        self,
        text: str,
        image_paths: list[str | Path],
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> str:
        if not self.is_available():
            raise RuntimeError("local VLM not available")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for p in image_paths:
            mime, b64 = _image_b64(Path(p))
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/{mime};base64,{b64}"},
            })
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


# Historical name (agent loop, tests): the local OpenAI-compatible provider.
LocalVLMClient = OpenAICompatibleVisionProvider


class GeminiVisionProvider(VisionProvider):
    """Google Generative Language v1beta — a deliberately SECOND wire
    protocol (``:generateContent``, ``x-goog-api-key``, ``contents``/``parts``),
    not a modification of the OpenAI-shaped one.

    The API key comes from the environment ONLY: ``api_key_env`` (default
    ``THREED_VLM_API_KEY``), falling back to ``GEMINI_API_KEY`` (same value
    in the owner's setup). The model must be a PINNED version (e.g.
    ``gemini-3.6-flash``) — ``-latest`` aliases are rejected at construction
    so a silent model drift can never pass review.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_sec: float | None = None,
    ):
        cfg = self._load_vision_config()
        vlm_cfg = cfg.get("vlm", {}) or {}
        self.model = model or vlm_cfg.get("model") or ""
        if self.model.endswith("-latest"):
            raise ValueError(
                f"gemini model {self.model!r} is a floating alias — pin a "
                "specific version (e.g. gemini-3.6-flash) so results are "
                "reproducible"
            )
        self.base_url = (
            base_url or vlm_cfg.get("base_url") or GEMINI_DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = api_key or vlm_cfg.get("_api_key") or (
            os.environ.get(vlm_cfg.get("api_key_env") or DEFAULT_API_KEY_ENV)
            or os.environ.get(GEMINI_API_KEY_ENV)
        )
        self.timeout_sec = float(timeout_sec or vlm_cfg.get("timeout_sec", 120))
        self._available: bool | None = None

    def is_configured(self) -> bool:
        return bool(self.model and self.api_key)

    def is_available(self, recheck: bool = False) -> bool:
        """Cached liveness probe (GET /v1beta/models with the API key)."""
        if not self.is_configured():
            return False
        if self._available is not None and not recheck:
            return self._available
        try:
            r = httpx.get(f"{self.base_url}/v1beta/models",
                          headers=self._headers(), timeout=10.0)
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        return self._available

    def _headers(self) -> dict[str, str]:
        # v1beta auth is the x-goog-api-key header — NOT a Bearer token.
        return {"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"}

    def chat_vision(
        self,
        text: str,
        image_paths: list[str | Path],
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> str:
        if not self.is_available():
            raise RuntimeError("gemini vision provider not available")
        parts: list[dict[str, Any]] = [{"text": text}]
        for p in image_paths:
            mime, b64 = _image_b64(Path(p))
            parts.append({"inline_data": {"mime_type": f"image/{mime}", "data": b64}})
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        resp = httpx.post(
            f"{self.base_url}/v1beta/models/{self.model}:generateContent",
            headers=self._headers(),
            json=body,
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        # shape: candidates[0].content.parts[*].text (joined across parts)
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"gemini response had no candidates: {str(data)[:300]}")
        content = (candidates[0].get("content") or {})
        return "".join(
            p.get("text") or "" for p in (content.get("parts") or [])
        )


def get_vision_provider() -> VisionProvider | None:
    """The configured vision provider, or None when vision.vlm is not set
    up. Never raises — an unreachable provider is reported via
    is_available(); a broken config is reported to stderr and degrades to
    no vision (fail soft)."""
    try:
        vlm_cfg = VisionProvider._load_vision_config().get("vlm", {}) or {}
        provider = str(vlm_cfg.get("provider") or "local").strip().lower()
        cls = GeminiVisionProvider if provider == "gemini" else OpenAICompatibleVisionProvider
        client = cls()
        return client if client.is_configured() else None
    except Exception as e:
        print(f"[vision] provider construction failed (continuing without "
              f"vision): {e}", file=sys.stderr)
        return None


# Back-compat alias: the agent loop and older call sites used this name.
get_local_vlm = get_vision_provider
