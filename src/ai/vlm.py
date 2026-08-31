"""Local vision-model client — the Qwen2.5-VL plug point (PROJECT_PLAN §13.1).

The owner builds and serves the vision model (e.g. Qwen2.5-VL via vLLM,
OpenAI-compatible) on Forge's 4080 Super. This client talks to it over HTTP
and provides the two integration points:

1. Reference analysis  — describe uploaded reference images so the text-only
   GLM-5.3 analyst can ground its ObjectSpec in them.
2. Visual gate         — advisory comparison of the studio renders against
   the reference images; the verdict lands in the run manifest.

Everything is config-gated (config/ai.yaml → vision.vlm.base_url) and fails
soft: an absent or unreachable VLM leaves the pipeline fully functional.
"""

from __future__ import annotations

import base64
import json
import re
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


def _image_content_part(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower().lstrip(".") or "png"
    mime = _IMAGE_MIME.get(ext, "png")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}}


class LocalVLMClient:
    """OpenAI-compatible chat with image support against the local VLM."""

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
            import os

            self.api_key = os.environ.get(vlm_cfg.get("api_key_env") or "THREED_VLM_API_KEY") or "sk-local"
        self._available: bool | None = None

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
        """One vision chat round. Raises on HTTP errors; callers fail soft."""
        if not self.is_available():
            raise RuntimeError("local VLM not available")
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for p in image_paths:
            content.append(_image_content_part(Path(p)))
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
        return self.chat_vision(self.DESCRIBE_PROMPT, image_paths, max_tokens=1600)

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
            return {"available": False, "reason": "local VLM not available"}
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
            raw = self.chat_vision(text, all_paths, max_tokens=1200)
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


def get_local_vlm() -> LocalVLMClient | None:
    """The configured local VLM, or None when vision.vlm is not set up.
    Never raises — an unreachable VLM is reported via is_available()."""
    try:
        client = LocalVLMClient()
        return client if client.is_configured() else None
    except Exception:
        return None
