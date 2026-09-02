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

Two model tiers (``docs/VISION_CONFIG.md`` §3): the default ``model``
serves every iteration; ``escalation_model`` (optional, same config block)
serves the ONE escalated verdict before packaging and whenever the default
disagrees with the measured gates. Both ids come from config — never
hardcoded.

429 handling is the §7 branch, implemented reactively in every chat POST:
``RATE_LIMIT_EXCEEDED`` → exponential backoff with jitter (2 s → 60 s,
bounded); ``QUOTA_EXCEEDED`` / ``RESOURCE_EXHAUSTED`` → stop retrying and
take the verdict from the local Qwen fallback (``vision.local_fallback``)
when one is configured. Oversized overview renders and reference photos are
downscaled to 768×768 before sending; close-ups are never downscaled.

Everything fails soft: an absent, misconfigured, or unreachable provider
leaves the pipeline fully functional.
"""

from __future__ import annotations

import abc
import base64
import io
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from PIL import Image

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

# Phase 5 image-size policy (master order / VISION_CONFIG): overview views
# and reference photos are downscaled to at most 768×768 (aspect preserved);
# close-up renders are NEVER downscaled — fine detail is their entire
# purpose, and blanket downscaling once hid a label defect completely.
OVERVIEW_MAX_DIM = 768
OVERVIEW_KEYS = ("front", "side", "top", "iso")

# VISION_CONFIG §7 — the 429 branch, applied reactively (there is no
# pre-flight quota endpoint):
#   RATE_LIMIT_EXCEEDED        → exponential backoff with jitter, 2 s → 60 s,
#                                bounded retries (never loop forever);
#   QUOTA_EXCEEDED /
#   RESOURCE_EXHAUSTED         → stop retrying; the verdict is taken from
#                                the local Qwen fallback when one is
#                                configured (vision.local_fallback).
RATE_LIMIT_INITIAL_S = 2.0
RATE_LIMIT_CAP_S = 60.0
RATE_LIMIT_MAX_RETRIES = 5

_sleep = time.sleep  # tests swap this to observe backoff delays


class QuotaExhaustedError(RuntimeError):
    """429 QUOTA_EXCEEDED / RESOURCE_EXHAUSTED — §7 branch 2: do NOT retry;
    callers may take the verdict from the local Qwen fallback."""


class RateLimitExhaustedError(RuntimeError):
    """429 RATE_LIMIT_EXCEEDED persisted past the bounded backoff — give up
    honestly (fail soft). NOT the quota branch, so no local fallback."""


def _reject_floating_alias(model: str, role: str) -> None:
    """Gemini models must be PINNED versions — ``-latest`` aliases are
    rejected at construction so a silent model drift cannot pass review."""
    if str(model).endswith("-latest"):
        raise ValueError(
            f"gemini {role} model {model!r} is a floating alias — pin a "
            "specific version (e.g. gemini-3.6-flash) so results are "
            "reproducible"
        )


def _image_b64(path: Path, max_dim: int | None = None) -> tuple[str, str]:
    """(mime_subtype, base64 payload) for an image file.

    With ``max_dim``, images LARGER than max_dim are downscaled (aspect
    preserved, LANCZOS) before encoding — the Phase 5 image-size policy.
    Images at or below max_dim, and anything PIL cannot read, pass through
    untouched."""
    ext = path.suffix.lower().lstrip(".") or "png"
    mime = _IMAGE_MIME.get(ext, "png")
    data = path.read_bytes()
    if max_dim is not None:
        try:
            img = Image.open(io.BytesIO(data))
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                buf = io.BytesIO()
                if mime == "jpeg":
                    img.convert("RGB").save(buf, format="JPEG", quality=90)
                else:
                    img.save(buf, format="PNG")
                data = buf.getvalue()
        except Exception:
            pass  # not a PIL-readable image — send the raw bytes
    b64 = base64.b64encode(data).decode("ascii")
    return mime, b64


def _classify_429(resp: httpx.Response) -> str:
    """'quota' | 'rate_limit' from the reason code in ``error.details``
    (§7). Priority: the specific ``RATE_LIMIT_EXCEEDED`` reason wins — real
    Gemini rate-limit bodies also carry ``status: "RESOURCE_EXHAUSTED"``,
    which alone (or with ``QUOTA_EXCEEDED``) is the quota branch. A bare 429
    with no recognizable reason is treated as a rate limit — the
    conservative retry branch."""
    try:
        body = resp.text[:2000]
    except Exception:
        body = ""
    if "RATE_LIMIT_EXCEEDED" in body:
        return "rate_limit"
    if "QUOTA_EXCEEDED" in body or "RESOURCE_EXHAUSTED" in body:
        return "quota"
    return "rate_limit"


def _post_with_429_policy(post):
    """Run ``post()`` (an httpx POST) under the §7 429 branch.

    RATE_LIMIT_EXCEEDED → sleep (exponential 2 s → 60 s, +jitter) and retry,
    at most RATE_LIMIT_MAX_RETRIES times. Quota reasons → raise
    QuotaExhaustedError immediately (no retry). Any other status is returned
    untouched for the caller's raise_for_status."""
    resp = post()
    attempt = 0
    while resp.status_code == 429:
        if _classify_429(resp) == "quota":
            raise QuotaExhaustedError(
                f"vision quota exhausted (429): {resp.text[:200]}"
            )
        if attempt >= RATE_LIMIT_MAX_RETRIES:
            raise RateLimitExhaustedError(
                f"vision rate limit persisted after {RATE_LIMIT_MAX_RETRIES} "
                f"backoff retries (429): {resp.text[:200]}"
            )
        delay = min(RATE_LIMIT_CAP_S, RATE_LIMIT_INITIAL_S * (2 ** attempt))
        _sleep(delay + random.uniform(0.0, 1.0))  # jitter
        attempt += 1
        resp = post()
    return resp


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
        model: str | None = None,
        image_max_dims: list[int | None] | None = None,
    ) -> str:
        """One vision chat round. ``model`` overrides the provider's default
        (the escalation tier); None means the default. ``image_max_dims``
        (parallel to image_paths) downscales oversized images before sending
        — the Phase 5 image-size policy (None entries = never downscale).
        Raises on HTTP errors; callers fail soft."""

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
        # Reference photos go to the VLM at most 768px (Phase 5 policy).
        return self.chat_vision(
            self.DESCRIBE_PROMPT, image_paths, max_tokens=4096,
            image_max_dims=[OVERVIEW_MAX_DIM] * len(image_paths),
        )

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
        escalate: bool = False,
    ) -> dict[str, Any]:
        """Compare renders against references. Returns a verdict dict:
        {available, matches_reference, score, issues, summary, model,
        escalated} — plus ``quota_fallback``/``quota_exhausted`` (§7 branch 2)
        and ``image_policy`` (which images were downscaled).

        ``escalate=True`` routes the ONE escalated call to the configured
        escalation model (``docs/VISION_CONFIG.md`` §3: before packaging,
        and whenever the default model disagrees with the measured gates).
        With no escalation model configured the default serves the call and
        ``escalated`` records False — an honest no-op, never a crash.

        Image-size policy (Phase 5): overview keys (front/side/top/iso) and
        reference photos are sent at most 768×768; every other render key is
        a close-up and is sent at NATIVE resolution — never downscaled."""
        if not self.is_available():
            return {"available": False, "reason": "vision provider not available"}
        try:
            images: list[tuple[str, Path]] = [
                ("reference", Path(p)) for p in reference_paths
            ]
            for k in OVERVIEW_KEYS:
                if render_paths.get(k):
                    images.append((k, Path(render_paths[k])))
            for k, p in render_paths.items():
                if k not in OVERVIEW_KEYS and p:
                    images.append((k, Path(p)))  # close-up: never downscaled
            all_paths = [p for _, p in images]
            max_dims = [
                None if k not in OVERVIEW_KEYS and k != "reference" else OVERVIEW_MAX_DIM
                for k, _ in images
            ]
            text = (
                self.VERDICT_PROMPT
                + "\n\nThe FIRST image(s) are the reference; the REMAINING "
                "images are the generated model's renders."
            )
            if model_summary:
                text += f"\nGenerated model summary: {model_summary}"
            model = (self.escalation_model if escalate else None) or None
            raw, fallback_model = self._chat_vision_quota_fallback(
                text, all_paths, max_tokens=3072, model=model,
                image_max_dims=max_dims,
            )
            parsed = extract_json_from_text(raw) or self._loose_verdict(raw)
            if not parsed:
                return {
                    "available": True, "parsed": False, "raw": raw[:800],
                    "model": fallback_model or model or self.model,
                    "escalated": False,
                }
            verdict = {
                "available": True,
                "parsed": True,
                "matches_reference": bool(parsed.get("matches_reference")),
                "score": parsed.get("score"),
                "issues": parsed.get("issues") or [],
                "summary": parsed.get("summary"),
                "model": fallback_model or model or self.model,
                "escalated": bool(model and model != self.model),
                "image_policy": {
                    "overview_and_reference_max_dim": OVERVIEW_MAX_DIM,
                    "closeups_untouched": True,
                },
            }
            if fallback_model:
                verdict["quota_fallback"] = True
            return verdict
        except QuotaExhaustedError as e:
            return {
                "available": True, "parsed": False,
                "error": str(e)[:400], "quota_exhausted": True,
            }
        except Exception as e:
            return {"available": True, "parsed": False, "error": str(e)[:400]}

    def _chat_vision_quota_fallback(
        self,
        text: str,
        image_paths: list[str | Path],
        *,
        max_tokens: int,
        model: str | None = None,
        image_max_dims: list[int | None] | None = None,
    ) -> tuple[str, str | None]:
        """chat_vision with the §7 quota branch: a quota-exhausted primary
        (QUOTA_EXCEEDED / RESOURCE_EXHAUSTED) is NOT retried — the local
        Qwen fallback serves the call when one is configured, and its model
        id is returned for honest recording (``quota_fallback`` in the
        verdict). Rate-limit errors are already handled inside chat_vision's
        bounded backoff; anything else propagates."""
        try:
            return (
                self.chat_vision(
                    text, image_paths, max_tokens=max_tokens, model=model,
                    image_max_dims=image_max_dims,
                ),
                None,
            )
        except QuotaExhaustedError:
            fb = get_quota_fallback_provider(self)
            if fb is None or not fb.is_available():
                raise
            return (
                fb.chat_vision(
                    text, image_paths, max_tokens=max_tokens,
                    image_max_dims=image_max_dims,
                ),
                fb.model,
            )

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
        escalation_model: str | None = None,
    ):
        cfg = self._load_vision_config()
        vlm_cfg = cfg.get("vlm", {}) or {}
        self.base_url = (base_url or vlm_cfg.get("base_url") or "").rstrip("/")
        self.model = model or vlm_cfg.get("model") or ""
        self.escalation_model = (
            escalation_model or vlm_cfg.get("escalation_model") or None
        )
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
        model: str | None = None,
        image_max_dims: list[int | None] | None = None,
    ) -> str:
        if not self.is_available():
            raise RuntimeError("local VLM not available")
        dims = list(image_max_dims or [None] * len(image_paths))
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for p, md in zip(image_paths, dims):
            mime, b64 = _image_b64(Path(p), md)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/{mime};base64,{b64}"},
            })
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        def post():
            return httpx.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": model or self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=self.timeout_sec,
            )

        resp = _post_with_429_policy(post)
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
        escalation_model: str | None = None,
    ):
        cfg = self._load_vision_config()
        vlm_cfg = cfg.get("vlm", {}) or {}
        self.model = model or vlm_cfg.get("model") or ""
        _reject_floating_alias(self.model, "default")
        # Escalation tier (VISION_CONFIG §3): one call before packaging and
        # whenever the default model disagrees with the measured gates.
        self.escalation_model = (
            escalation_model or vlm_cfg.get("escalation_model") or None
        )
        if self.escalation_model:
            _reject_floating_alias(self.escalation_model, "escalation")
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
        model: str | None = None,
        image_max_dims: list[int | None] | None = None,
    ) -> str:
        if not self.is_available():
            raise RuntimeError("gemini vision provider not available")
        dims = list(image_max_dims or [None] * len(image_paths))
        parts: list[dict[str, Any]] = [{"text": text}]
        for p, md in zip(image_paths, dims):
            mime, b64 = _image_b64(Path(p), md)
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

        def post():
            return httpx.post(
                f"{self.base_url}/v1beta/models/{model or self.model}:generateContent",
                headers=self._headers(),
                json=body,
                timeout=self.timeout_sec,
            )

        resp = _post_with_429_policy(post)
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


def get_quota_fallback_provider(
    primary: VisionProvider,
) -> VisionProvider | None:
    """§7 branch 2: when the primary (Gemini) is quota-exhausted, the
    verdict comes from the local Qwen provider instead — configured under
    ``vision.local_fallback`` (base_url + model of the OpenAI-compatible
    vLLM server). None when not configured or when the primary already IS
    the local tier (no second fallback). Loading/unloading the local model
    and returning the GPU to Blender is a server-side ops action
    (VISION_CONFIG §7); this code only routes the call and records the
    fallback honestly. Never raises."""
    if isinstance(primary, OpenAICompatibleVisionProvider):
        return None
    try:
        cfg = VisionProvider._load_vision_config().get("local_fallback") or {}
        if not (cfg.get("base_url") and cfg.get("model")):
            return None
        provider = OpenAICompatibleVisionProvider(
            base_url=str(cfg["base_url"]), model=str(cfg["model"])
        )
        return provider if provider.is_configured() else None
    except Exception:
        return None


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
