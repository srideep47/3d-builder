"""Build-route router (GLM_PROMPT_NEURAL_INTAKE.md §4.0.5).

One contract, three mesh sources, chosen per job — cheapest first:

  1. template     a `templates/*.yaml` matches the job's product_class
  2. parametric   no template, shape expressible in the 12 ShapeType
                  primitives (the brain authors an ObjectSpec)
  3. neural       neither fits — organic / sculpted / freeform, or the
                  spec route failed its gates (escalation is SURFACED to
                  the owner, never a silent second run)

Measured motivation (same machine, 2026-09-03): for a product a template
covers, the template wins on every axis the client validator checks —
exact dimensions vs square, quad-clean vs triangulated, 374–15,420 tri vs
~48,000, correct fabric metallic vs 34%, real label crop vs smeared,
seconds vs 5–10 minutes. Sending it to TRELLIS is a downgrade.

Three hard requirements (work order, verbatim intent):

  - the decision and its reason are recorded in the run manifest, EVERY
    time — a disputed asset must show which path built it and why
  - the choice is exposed in the UI (Auto default, owner can force)
  - a forced path that cannot run REFUSES with a named reason
    (RouteError); a silent downgrade is exactly the defect class this
    project refuses to ship

Shape-class vocabulary: the organic keyword list below is ROUTING
vocabulary (shape-class words that map to `ShapeType.ORGANIC`), not
product knowledge — rule 11 confines product knowledge to templates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..spec.template import load_template
from .view_diversity import ViewDiversity, measure_view_diversity

ROUTES = ("auto", "template", "parametric", "neural")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Words in the prompt that describe a shape the parametric primitives
# cannot express — the router's proxy for "expressible in the 12 ShapeType
# primitives" at INTAKE time, before the brain authors anything.
ORGANIC_KEYWORDS = (
    "organic",
    "sculpted",
    "sculpture",
    "freeform",
    "free-form",
    "irregular",
    "amorphous",
    "plush",
    "stuffed",
    "figurine",
    "statue",
    "mascot",
    "character",
)


class RouteError(Exception):
    """A forced route that cannot run — REFUSE, never silently fall back."""


@dataclass
class RouteDecision:
    route: str  # "template" | "parametric" | "neural"
    reason: str  # one line; shown in the UI before the run, recorded in the manifest
    forced: bool = False
    template_file: str | None = None
    diversity: dict | None = None  # §3.1 score, when views were supplied

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "reason": self.reason,
            "forced": self.forced,
            "template_file": self.template_file,
            "diversity": self.diversity,
        }


def _default_templates_dir() -> Path:
    return PROJECT_ROOT / "templates"


def find_template(product_class: str | None, templates_dir: Path) -> Path | None:
    """The template whose declared product_class (or file stem) matches —
    case-insensitive. None when the class has no template."""
    if not product_class:
        return None
    wanted = product_class.strip().lower()
    if not wanted:
        return None
    for p in sorted(templates_dir.glob("*.yaml")):
        if p.stem.lower() == wanted:
            return p
        try:
            tpl = load_template(p)
        except Exception:
            continue
        if (tpl.product_class or "").strip().lower() == wanted:
            return p
    return None


def _organic_signal(prompt: str) -> str | None:
    text = (prompt or "").lower()
    for word in ORGANIC_KEYWORDS:
        if word in text:
            return word
    return None


def _diversity_dict(views: list[str | Path] | None) -> dict | None:
    if not views:
        return None
    result = measure_view_diversity([Path(v) for v in views])
    return {
        "score": result.score,
        "max_pairwise": result.max_pairwise,
        "min_pairwise": result.min_pairwise,
        "image_count": result.image_count,
        "warned": result.warned,
        "reason": result.reason,
    }


def decide_route(
    *,
    prompt: str = "",
    product_class: str | None = None,
    views: list[str | Path] | None = None,
    forced: str = "auto",
    templates_dir: str | Path | None = None,
) -> RouteDecision:
    """Decide the build route for one job. Pure and deterministic: no
    network, no GPU, no service calls — the caller owns availability
    probes and re-checks them where they matter.

    forced: "auto" (default) or an explicit route. A forced route that
    cannot run raises RouteError with a named reason — NEVER falls back.
    """
    forced = (forced or "auto").strip().lower()
    if forced not in ROUTES:
        raise RouteError(
            f"unknown route {forced!r} — expected one of {list(ROUTES)}"
        )

    tdir = Path(templates_dir) if templates_dir else _default_templates_dir()
    template = find_template(product_class, tdir)
    diversity = _diversity_dict(views)
    div_note = ""
    if diversity and diversity.get("score") is not None:
        div_note = f", view diversity {diversity['score']:.3f}"

    # ── forced routes: refuse what cannot run, never fall back ───────────
    if forced == "template":
        if template is None:
            raise RouteError(
                f"Template route refused: no templates/*.yaml for product_class "
                f"{product_class or '(none supplied)'!r} in {tdir}"
            )
        return RouteDecision(
            route="template",
            reason=f"forced — {template.name} matches product_class "
            f"{(product_class or template.stem)!r}",
            forced=True,
            template_file=template.name,
            diversity=diversity,
        )
    if forced == "parametric":
        if not prompt.strip():
            raise RouteError(
                "Parametric route refused: the brain authors the ObjectSpec "
                "from the prompt, and the prompt is empty"
            )
        return RouteDecision(
            route="parametric",
            reason="forced — brain-authored ObjectSpec from the prompt",
            forced=True,
            diversity=diversity,
        )
    if forced == "neural":
        if not views:
            raise RouteError(
                "Neural route refused: TRELLIS 2 generates from labelled "
                "reference views and none were supplied"
            )
        reason = "forced — TRELLIS 2 from the labelled reference views"
        if diversity and diversity.get("warned"):
            reason += (
                f" (LOW VIEW DIVERSITY {diversity.get('score'):.3f} — "
                "neural proportions are at risk; §3.1)"
            )
        return RouteDecision(route="neural", reason=reason, forced=True, diversity=diversity)

    # ── auto: cheapest first ──────────────────────────────────────────────
    if template is not None:
        return RouteDecision(
            route="template",
            reason=f"{template.name} matches product_class "
            f"{(product_class or template.stem)!r}",
            template_file=template.name,
            diversity=diversity,
        )

    organic = _organic_signal(prompt)
    if organic is not None:
        reason = (
            f"no template for {(product_class or 'this product')!r}, shape described as "
            f"{organic!r} (not expressible in primitives){div_note}"
        )
        if diversity and diversity.get("warned"):
            reason += (
                f" — LOW VIEW DIVERSITY {diversity['score']:.3f}: neural proportions "
                "are at risk (§3.1)"
            )
        return RouteDecision(route="neural", reason=reason, diversity=diversity)

    reason = (
        f"no template for {(product_class or 'this product')!r}, shape expressible in "
        f"primitives — brain-authored spec"
    )
    if diversity and diversity.get("warned"):
        reason += (
            f" (low view diversity {diversity['score']:.3f} prefers this route; §3.1)"
        )
    return RouteDecision(route="parametric", reason=reason, diversity=diversity)
