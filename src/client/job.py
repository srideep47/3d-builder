"""Job intake — one client job card as data (GLM_BRIEF §7 T1) + Phase 4
prompt intake.

Rule 9 (absolute): dimensions are owner-supplied with an EXPLICIT unit for
every job. A job card without `dims`, or dims without `unit`, fails loud
validation — a unit is never defaulted and a dimension never inferred. The
job card in the client dashboard (12 × 12 × 65 IN) and a hallucinated
60 × 80 × 10 in are both irrelevant: only what the owner writes in job.yaml
counts.

Phase 4 (prompt → JobCard): every constraint the owner can state in a
prompt is a card field, and every consumer reads the EFFECTIVE value
through the `effective_*` helpers — never a module constant — so a per-job
override cannot drift from what is enforced. Fields left None fall back to
the client-contract defaults in contract.py (with their OPEN_QUESTIONS
provenance). `intake_from_prompt` is the deterministic front door: regex
extraction of EXPLICITLY stated constraints only, loud IntakeError on
ambiguity or silence; it never guesses a number, a unit, or which file a
cap applies to.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .contract import (
    MB,
    OPTIONAL_DELIVERABLES,
    REQUIRED_DELIVERABLES,
    TIER_TRI_CEILINGS,
)
from .units import canonical_unit, to_metres

_AXES = ("x", "y", "z")

# Axis mapping (owner amendment 2, T1 review): which model axis carries each
# declared dimension. The DEFAULT is L→X, W→Y, H→Z (Blender Z-up, the repo's
# internal convention — origin bottom-centre, height vertical). It is a field
# on the job card, not an implicit convention, because the client's validator
# derives "Base aspect ratio (Long/Short)" and "Height aspect ratio
# (Long/Height)" from the L/W/H assignment: a swapped L/W fails their gate
# while every individual number still looks correct. If the client's
# convention is ever observed to differ, override per job (or change the
# default here once, with evidence) — never guess per build.
DEFAULT_AXIS_MAP = {"length": "x", "width": "y", "height": "z"}

# Client dimension tolerance (owner amendment 3): ±0.01 in by default.
# Rationale: the client's validator panel displays dimensions to TWO decimal
# places in inches (observed: L=8.64, W=31.00, H=20.50). Anything inside
# ±0.01 in renders as the same value their reviewer compares against, so a
# tighter local tolerance buys nothing and a looser one risks a visible
# mismatch. NOTE this is the CLIENT gate tolerance, expressed in the job's
# declared unit, and is SEPARATE from the internal build tolerance
# (spec `tolerance_m`, default ±1 mm = ±0.039 in): the client figure is
# ~4× tighter than the internal default, so an internally-green build is NOT
# automatically client-green. In practice builds converge to ~0 mm delta,
# well inside both.
DEFAULT_DIM_TOLERANCE = 0.01

# Every deliverable suffix this pipeline can emit (contract.py is the
# definition). `required_formats` / `file_size_caps` keys are validated
# against this set at card-construction time: a format we cannot produce
# must fail intake LOUDLY (naming the format), not surface later as a
# naming-gate failure the owner reads as a broken build.
KNOWN_DELIVERABLE_SUFFIXES: frozenset[str] = frozenset(
    d.suffix for d in REQUIRED_DELIVERABLES
) | frozenset(d.suffix for d in OPTIONAL_DELIVERABLES)


class SizeCap(BaseModel):
    """One deliverable's file-size cap exactly as stated — value AND basis.

    The basis changes the byte count (MB = 1,000,000 decimal; MiB = 2^20
    binary ≈ 4.9% larger): the client's own basis is unobserved (open
    question 'mb-basis', contract.py), so an owner-stated basis must be
    carried verbatim, never assumed. The contract's default caps are
    decimal MB — the stricter reading, so a local pass can never overshoot
    a same-number binary cap."""

    value: float = Field(gt=0)
    basis: Literal["MB", "MiB"] = "MB"

    @property
    def max_bytes(self) -> int:
        unit = 1_000_000 if self.basis == "MB" else 1 << 20
        return int(self.value * unit)

    def describe(self) -> str:
        return f"{self.value:g}{self.basis}"


class JobDims(BaseModel):
    """Owner-supplied dimensions with an explicit unit. All three extents are
    required and positive; `unit` has no default."""

    length: float = Field(gt=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    unit: str

    @field_validator("unit")
    @classmethod
    def _unit_must_be_known(cls, v: str) -> str:
        canonical_unit(v)  # raises loudly on unknown units
        return v


class AxisMap(BaseModel):
    length: Literal["x", "y", "z"] = "x"
    width: Literal["x", "y", "z"] = "y"
    height: Literal["x", "y", "z"] = "z"

    @model_validator(mode="after")
    def _must_be_permutation(self) -> "AxisMap":
        if sorted((self.length, self.width, self.height)) != sorted(_AXES):
            raise ValueError(
                f"axis_map must assign x, y, z to length/width/height exactly "
                f"once each; got length={self.length}, width={self.width}, "
                f"height={self.height}"
            )
        return self


class JobCard(BaseModel):
    job_code: str
    dims: JobDims
    complexity: Literal["simple", "medium", "complex"]
    orientation: Literal["floor", "wall", "ceiling", "tabletop"]
    product_class: str
    part_scope: str = ""
    reference_dir: Path
    axis_map: AxisMap = Field(default_factory=AxisMap)
    # Client-gate tolerance in the job's declared unit (see DEFAULT_DIM_TOLERANCE).
    dim_tolerance: float | None = Field(default=None, gt=0)
    # PLACEHOLDER dimensions (owner's overnight order, T4): the owner has NOT
    # supplied real dimensions yet — the values in `dims` are stand-ins for
    # exercising the pipeline only. With this flag set the pipeline still runs
    # (structural review renders are valid output) but package emission is
    # REFUSED, loudly (rule 9: dimensions are never inferred; a standard
    # queen size must never be guessed). Recorded in qa_report.json.
    dims_placeholder: bool = False

    # ── Phase 4: dynamic constraints from the owner's prompt ────────────────
    # All optional; None = "use the client-contract default" (contract.py).
    # Gates and packaging read these through the effective_* helpers below,
    # so an override cannot drift from what is actually enforced. The full
    # card (including these) lands in qa_report.json's job_card section.
    polycount_ceiling: int | None = Field(
        default=None, gt=0,
        description="Owner-stated polycount ceiling. Overrides the tier table "
                    "(contract.TIER_TRI_CEILINGS) and UNBLOCKS the 'complex' "
                    "tier, whose ceiling is unknown and otherwise fails closed.")
    polycount_semantics: Literal["triangles", "faces", "triangle_equivalent"] | None = Field(
        default=None,
        description="What the owner's 'polycount' counts. None = the contract "
                    "default triangle_equivalent (conservative: >= face count; "
                    "open question 'polycount-semantics').")
    file_size_caps: dict[str, SizeCap] | None = Field(
        default=None,
        description="Per-deliverable size caps keyed by suffix (e.g. '.fbx'), "
                    "each with its own value AND basis (MB/MiB). Overrides the "
                    "contract's observed decimal-MB caps for those keys only.")
    required_formats: list[str] | None = Field(
        default=None,
        description="The deliverable suffixes this job requires — defines "
                    "'complete package' for the naming/file-size gates. None = "
                    "the full contract set. NOTE the finishing chain still "
                    "emits the standard superset (partial chains degrade the "
                    "FBX: its materials come from the bake); qa_report marks "
                    "each emitted file required: true/false per this list.")
    texture_resolution: int | None = Field(
        default=None, gt=0,
        description="Bake/atlas resolution in px. Used when the caller does "
                    "not pass an explicit resolution (finish_delivery "
                    "resolution=None).")
    fbx_axis_up: Literal["X", "Y", "Z"] | None = Field(
        default=None,
        description="FBX export up-axis requested from the exporter. Must be "
                    "set together with fbx_axis_forward. None = the "
                    "FBX-standard Y-up default (open question "
                    "'fbx-axis-convention').")
    fbx_axis_forward: Literal["X", "Y", "Z", "-X", "-Y", "-Z"] | None = Field(
        default=None,
        description="FBX export forward-axis. Must be set together with "
                    "fbx_axis_up and must not be parallel to it.")
    # Provenance for prompt-sourced cards: which quoted prompt fragment set
    # which constraint. Set only by intake_from_prompt; rides along into
    # qa_report.json so a disputed delivery shows its own evidence.
    intake_evidence: dict[str, str] | None = None

    @field_validator("job_code")
    @classmethod
    def _safe_job_code(cls, v: str) -> str:
        v = v.strip()
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            # job_code becomes file names — no path separators, ever.
            raise ValueError(f"job_code {v!r} must be non-empty alphanumerics/-/_")
        return v

    @field_validator("required_formats")
    @classmethod
    def _formats_must_be_producible(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("required_formats must be non-empty when set — "
                             "an empty delivery is not a delivery")
        unknown = [s for s in v if s not in KNOWN_DELIVERABLE_SUFFIXES]
        if unknown:
            raise ValueError(
                f"required_formats names deliverables this pipeline cannot produce: "
                f"{unknown}. Known suffixes: {sorted(KNOWN_DELIVERABLE_SUFFIXES)}. "
                "Fail intake loudly rather than fail a gate later (rule 9)."
            )
        return v

    @field_validator("file_size_caps")
    @classmethod
    def _caps_must_target_known_deliverables(cls, v: dict[str, SizeCap] | None):
        if v is None:
            return v
        unknown = [k for k in v if k not in KNOWN_DELIVERABLE_SUFFIXES]
        if unknown:
            raise ValueError(
                f"file_size_caps keys must be known deliverable suffixes; unknown: "
                f"{unknown}. Known: {sorted(KNOWN_DELIVERABLE_SUFFIXES)}"
            )
        return v

    @model_validator(mode="after")
    def _fbx_axes_must_be_a_pair(self) -> "JobCard":
        if (self.fbx_axis_up is None) != (self.fbx_axis_forward is None):
            raise ValueError(
                "fbx_axis_up and fbx_axis_forward must be set TOGETHER — a "
                "half-specified axis convention is a guess (rule 9)"
            )
        if self.fbx_axis_up is not None and self.fbx_axis_forward is not None:
            if self.fbx_axis_up == self.fbx_axis_forward.lstrip("-"):
                raise ValueError(
                    f"fbx forward axis {self.fbx_axis_forward!r} is parallel to "
                    f"up axis {self.fbx_axis_up!r} — not a valid convention"
                )
        return self

    # ── resolved helpers ─────────────────────────────────────────────────────

    @property
    def canonical_unit(self) -> str:
        return canonical_unit(self.dims.unit)

    def expected_bounds_m(self) -> dict[str, float]:
        """{axis: metres} the packaged model must hit, per the axis map."""
        d = self.dims
        return {
            self.axis_map.length: to_metres(d.length, d.unit),
            self.axis_map.width: to_metres(d.width, d.unit),
            self.axis_map.height: to_metres(d.height, d.unit),
        }

    def dim_tolerance_in_job_units(self) -> float:
        return self.dim_tolerance if self.dim_tolerance is not None else DEFAULT_DIM_TOLERANCE

    def dim_tolerance_m(self) -> float:
        return to_metres(self.dim_tolerance_in_job_units(), self.dims.unit)

    # ── effective constraint values (Phase 4) ────────────────────────────────
    # Single point of resolution: card override > contract default. Gates and
    # packaging call THESE, never the contract tables directly, so a card
    # override and the enforced number cannot drift apart.

    def effective_polycount_ceiling(self) -> int | None:
        """Card override > tier table. An explicit owner ceiling also unblocks
        the 'complex' tier (unknown ceiling → otherwise fail closed) and
        replaces the provisional 'simple' 50k."""
        if self.polycount_ceiling is not None:
            return self.polycount_ceiling
        return TIER_TRI_CEILINGS.get(self.complexity)

    def effective_polycount_semantics(self) -> str:
        return self.polycount_semantics or "triangle_equivalent"

    def effective_required_suffixes(self) -> list[str]:
        """The suffixes a COMPLETE package must contain for this job."""
        if self.required_formats is None:
            return [d.suffix for d in REQUIRED_DELIVERABLES]
        out: list[str] = []
        for s in self.required_formats:
            if s not in out:
                out.append(s)
        return out

    def effective_size_cap(self, suffix: str) -> SizeCap | None:
        """Cap for one deliverable suffix: card override (own basis) >
        contract's observed decimal-MB cap > None (no known cap)."""
        if self.file_size_caps and suffix in self.file_size_caps:
            return self.file_size_caps[suffix]
        for d in REQUIRED_DELIVERABLES:
            if d.suffix == suffix:
                if d.max_bytes is None:
                    return None
                return SizeCap(value=d.max_bytes / MB, basis="MB")
        return None

    def effective_texture_resolution(self, default: int = 1024) -> int:
        return self.texture_resolution if self.texture_resolution is not None else default


def load_job(path: str | Path) -> JobCard:
    """Load and validate a job.yaml. Fails loudly (never defaults a unit)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Job card not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Job card {p} must be a YAML mapping")
    # Fast, explicit pre-checks so the error names the missing field —
    # pydantic's message is good but this makes rule 9 unmissable.
    if "dims" not in raw or not isinstance(raw["dims"], dict):
        raise ValueError(
            f"Job card {p}: 'dims' (length, width, height, unit) is REQUIRED — "
            "dimensions are owner-supplied, never inferred (rule 9)"
        )
    if not str(raw["dims"].get("unit", "")).strip():
        raise ValueError(
            f"Job card {p}: dims.unit is REQUIRED and must be explicit — "
            "units are never defaulted (rule 9)"
        )
    if raw.get("dims_placeholder"):
        # Loud, unavoidable: these dims are NOT real and nothing deliverable
        # may be built from them (refusal happens in package emission).
        print(
            "*" * 78
            + f"\n* PLACEHOLDER DIMENSIONS: job card {p} sets dims_placeholder: true.\n"
            "* The dims below are stand-ins for pipeline exercise ONLY — the owner\n"
            "* has not supplied real dimensions. NO deliverable package will be\n"
            "* emitted for this job until real dimensions replace them (rule 9).\n"
            + "*" * 78,
            file=sys.stderr,
        )
    try:
        return JobCard.model_validate(raw)
    except Exception as e:
        raise ValueError(f"Job card {p} failed validation:\n{e}") from e


def dump_job_yaml(card: JobCard) -> str:
    """Serialize a JobCard back to job.yaml form (round-trips load_job)."""
    return yaml.safe_dump(card.model_dump(mode="json"), sort_keys=False,
                          allow_unicode=True)


# ════════════════════════════════════════════════════════════════════════════
# Phase 4: prompt intake — deterministic, loud, never a guess
# ════════════════════════════════════════════════════════════════════════════


class IntakeError(ValueError):
    """An owner prompt is ambiguous, contradictory, or silent on a required
    constraint. Intake never guesses — not dimensions (rule 9), not units,
    not which deliverable a cap applies to, not which of two ceilings was
    meant. Fix the prompt or write job.yaml directly."""


# Patterns are deliberately NARROW: each requires the constraint's own
# vocabulary (a ceiling word for polycount, a format token for caps, a
# texture word for resolution). A statement outside these patterns is left
# as None → the contract default — never guessed. Richer prompts go through
# a hand-written job.yaml; intake is the fast, auditable path, not a limit.

_UNIT_WORD = r"(?:inches|inch|in|feet|foot|ft|mm|cm|meters|metres|meter|metre|m)"
_INTAKE_DIMS = re.compile(
    rf"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*({_UNIT_WORD})\b",
    re.IGNORECASE,
)
_INTAKE_BARE_DIMS = re.compile(
    r"\b\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\b"
)

_CEILING_WORD = (r"(?:max(?:imum)?|ceiling|budget|cap(?:ped)?(?:\s+at)?|under|"
                 r"up\s+to|limit(?:ed)?(?:\s+to)?|no\s+more\s+than)")
_POLY_NOUN = r"(?:polycount|polys?|tris?|triangles?|faces?)"
# "max 80,000 triangles", "under 50k tris", "no more than 8000 faces"
_INTAKE_POLY_NUM_FIRST = re.compile(
    rf"\b{_CEILING_WORD}\s+(?:of\s+)?(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*({_POLY_NOUN})\b",
    re.IGNORECASE,
)
# "polycount ceiling 200,000", "tris budget of 60k", "faces limited to 8000"
_INTAKE_POLY_NOUN_FIRST = re.compile(
    rf"\b({_POLY_NOUN})\s+(?:{_CEILING_WORD}|of)\s*(?:of\s+)?(\d[\d,]*(?:\.\d+)?)\s*(k)?\b",
    re.IGNORECASE,
)

# "FBX under 10 MB", "LP max 15MB", "HP file size 50 MiB"
_INTAKE_CAP_FMT_FIRST = re.compile(
    r"\b(fbx|usdz|spp|lp|hp)\b[^0-9\n]{0,30}?(\d+(?:\.\d+)?)\s*(mib|mb)\b",
    re.IGNORECASE,
)
# "12 MiB for the FBX", "10 MB LP"
_INTAKE_CAP_NUM_FIRST = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mib|mb)\s+(?:for\s+(?:the\s+)?|max\s+)?(fbx|usdz|spp|lp|hp)\b",
    re.IGNORECASE,
)
_CAP_TOKEN_TO_SUFFIX = {
    "fbx": ".fbx", "usdz": "_LP.usdz", "spp": ".spp",
    "lp": "_LP.glb", "hp": "_HP.glb",
}
_ANY_SIZE_STATEMENT = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mib|mb)\b", re.IGNORECASE)

# "2048px", "1024 x 1024 px"
_INTAKE_RES_PX = re.compile(
    r"\b(\d{3,5})(?:\s*[x×]\s*\d{3,5})?\s*(?:px|pixels?)\b", re.IGNORECASE)
# "2K textures" / "textures at 4k" — the k form REQUIRES a texture word:
# a bare "8k" is more likely prose (e.g. a polycount "8k tris") than a
# resolution statement, and a wrong guess here silently changes every bake.
_INTAKE_RES_K = re.compile(
    r"\b([1248])k\b\s*(?:textures?|maps?|bakes?|res(?:olution)?s?)", re.IGNORECASE)
_INTAKE_RES_K_PRE = re.compile(
    r"\b(?:textures?|maps?|bakes?)\s*(?:at\s+|of\s+)?([1248])k\b", re.IGNORECASE)

# A LABELED clause only ("Formats: FBX, GLB and USDZ") — format tokens in
# free prose are mentions, not requirements. No '.' in the capture class:
# the clause ends at the first sentence period.
_INTAKE_FORMATS_CLAUSE = re.compile(
    r"\bformats?\s*[:\-]?\s*([a-z0-9,+ ]+)", re.IGNORECASE)
_FORMAT_TOKEN_SUFFIXES: dict[str, list[str]] = {
    "fbx": [".fbx"],
    "glb": ["_LP.glb", "_HP.glb"],
    "gltf": ["_LP.glb", "_HP.glb"],
    "usdz": ["_LP.usdz"],
    "png": ["_BaseColor.png", "_Normal.png", "_Roughness.png",
            "_Metallic.png", "_AO.png"],
    "spp": [".spp"],
}

_AXIS_WORD = r"(?:maps?\s*(?:to|onto)|along|on|to|→|->)"
_INTAKE_AXIS: list[tuple[str, re.Pattern]] = [
    ("length", re.compile(rf"\b(?:length|l)\s*{_AXIS_WORD}\s*([xyz])\b", re.IGNORECASE)),
    ("width", re.compile(rf"\b(?:width|w)\s*{_AXIS_WORD}\s*([xyz])\b", re.IGNORECASE)),
    ("height", re.compile(rf"\b(?:height|h)\s*{_AXIS_WORD}\s*([xyz])\b", re.IGNORECASE)),
]

_INTAKE_COMPLEXITY = re.compile(
    r"\bcomplexity\s*[:\-]?\s*(simple|medium|complex)\b", re.IGNORECASE)
_INTAKE_ORIENTATION: list[tuple[str, re.Pattern]] = [
    ("floor", re.compile(r"\bfloor[- ]standing\b|\b(?:rests|sits)\s+on\s+the\s+(?:floor|ground)\b", re.IGNORECASE)),
    ("tabletop", re.compile(r"\btable[- ]?top\b", re.IGNORECASE)),
    ("wall", re.compile(r"\bwall[- ]mounted\b", re.IGNORECASE)),
    ("ceiling", re.compile(r"\bceiling[- ]mounted\b|\bhangs?\s+from\s+the\s+ceiling\b", re.IGNORECASE)),
]

# "FBX axis convention: up Y, forward -Z" or "FBX: Y-up, -Z-forward" — the
# pair, or nothing: a half-specified convention is a guess and fails loud
# below.
_INTAKE_FBX_AXES = re.compile(
    r"\bfbx\b[^.\n]{0,40}?up\s*[:=]?\s*([xyz])\b[^.\n]{0,40}?forward\s*[:=]?\s*([+-]?[xyz])\b",
    re.IGNORECASE)
_INTAKE_FBX_AXES_ALT = re.compile(
    r"\bfbx\b[^.\n]{0,40}?([xyz])\s*-\s*up\b[^.\n]{0,40}?([+-]?[xyz])\s*-\s*forward\b",
    re.IGNORECASE)
_INTAKE_FBX_UP_ONLY = re.compile(
    r"\bfbx\b[^.\n]{0,40}?(?:\bup\s*[:=]?\s*[xyz]\b|[xyz]\s*-\s*up\b)",
    re.IGNORECASE)


def _single(values: list, what: str):
    """None when absent, the value when every statement agrees, an
    IntakeError quoting the disagreement when they do not."""
    uniq: list = []
    for v in values:
        if v not in uniq:
            uniq.append(v)
    if not uniq:
        return None
    if len(uniq) == 1:
        return uniq[0]
    raise IntakeError(
        f"the prompt states {what} more than once with DIFFERENT values "
        f"({uniq!r}) — intake never picks one; disambiguate the prompt or "
        f"write job.yaml directly (rule 9)"
    )


def _intake_number(num: str, k: str | None) -> float:
    value = float(num.replace(",", ""))
    return value * 1000.0 if k else value


def _semantics_from_noun(noun: str) -> str | None:
    n = noun.lower()
    if n.startswith("tri"):
        return "triangles"
    if n.startswith("face"):
        return "faces"
    return None  # "polycount"/"polys" — semantics unstated


def intake_from_prompt(
    prompt: str,
    *,
    job_code: str,
    product_class: str,
    reference_dir: Path,
    complexity: Literal["simple", "medium", "complex"] | None = None,
    orientation: Literal["floor", "wall", "ceiling", "tabletop"] | None = None,
    part_scope: str = "",
    placeholder_dims: tuple[float, float, float] | None = None,
    placeholder_unit: str | None = None,
    explicit_dims: "JobDims | None" = None,
) -> JobCard:
    """Build a JobCard from an owner prompt + the structural facts the caller
    supplies (job code, product class, reference dir).

    DETERMINISTIC (regex, no LLM). Extracts only EXPLICITLY stated
    constraints; every extraction is recorded in `intake_evidence` with its
    quoted prompt fragment so the card carries its own provenance into
    qa_report.json. Anything ambiguous, contradictory, or silent fails with
    IntakeError — never a guess:

    - dimensions: 'L x W x H <unit>' (unit REQUIRED, rule 9). Absent dims
      accept `explicit_dims` (the intake FORM's real owner-supplied values —
      recorded as form evidence, NOT placeholders, delivery NOT refused) or
      require explicit `placeholder_dims` + `placeholder_unit` (delivery
      stays refused via dims_placeholder); bare dims without a unit are an
      error, not a default.
    - polycount: a ceiling word must be present ("max 80,000 triangles",
      "polycount ceiling 200k"). The noun sets tri-vs-face semantics;
      "polycount"/"polys" leaves semantics at the contract default.
    - file-size caps: must NAME the deliverable ("FBX under 10 MB",
      "12 MiB for the LP"); value AND basis (MB vs MiB) are kept verbatim.
      A cap with no named target is an error — intake never guesses which
      file it applies to.
    - texture resolution: "2048px" or "2K textures" (the k form requires a
      texture word).
    - formats: a labeled clause ("Formats: FBX, GLB").
    - axis convention: all three of length/width/height or none; FBX export
      axes as an up+forward pair.
    - complexity / orientation: explicit argument wins over a prompt
      statement; neither → error (the polycount tier and grounding are
      never guessed).

    Returns the validated JobCard. Structural args (job_code,
    product_class, reference_dir) are the caller's dispatch facts — they
    are not scraped out of free text.
    """
    evidence: dict[str, str] = {}

    # ── dimensions (rule 9: never inferred, unit never defaulted) ────────────
    dim_matches = list(_INTAKE_DIMS.finditer(prompt))
    if dim_matches:
        triples = [(float(m[1]), float(m[2]), float(m[3]), m[4].lower())
                   for m in dim_matches]
        triple = _single(triples, "dimensions")
        dims = JobDims(length=triple[0], width=triple[1], height=triple[2],
                       unit=triple[3])
        dims_placeholder = False
        evidence["dims"] = (f"{triple[0]:g} x {triple[1]:g} x {triple[2]:g} "
                            f"{triple[3]} — quoted from the prompt")
        if explicit_dims is not None:
            # both sources stated: they must AGREE (converted to metres) —
            # a silent pick between contradictory owner statements is a guess
            a = [to_metres(dims.length, dims.unit), to_metres(dims.width, dims.unit),
                 to_metres(dims.height, dims.unit)]
            b = [to_metres(explicit_dims.length, explicit_dims.unit),
                 to_metres(explicit_dims.width, explicit_dims.unit),
                 to_metres(explicit_dims.height, explicit_dims.unit)]
            if any(abs(x - y) > 1e-6 for x, y in zip(a, b)):
                raise IntakeError(
                    f"the prompt states {a[0]:g} x {a[1]:g} x {a[2]:g} m but the "
                    f"form supplies {b[0]:g} x {b[1]:g} x {b[2]:g} m — "
                    "contradictory owner dimensions are never silently resolved"
                )
            evidence["dims"] += " (agrees with the intake form)"
    elif explicit_dims is not None:
        dims = explicit_dims
        dims_placeholder = False
        evidence["dims"] = (f"{dims.length:g} x {dims.width:g} x {dims.height:g} "
                            f"{dims.unit} — owner-supplied via the intake form "
                            "(explicit, not placeholders; rule 9 satisfied)")
    elif placeholder_dims is not None:
        if not placeholder_unit:
            raise IntakeError("placeholder_dims given without placeholder_unit "
                              "— a unit is never defaulted (rule 9)")
        dims = JobDims(length=placeholder_dims[0], width=placeholder_dims[1],
                       height=placeholder_dims[2], unit=placeholder_unit)
        dims_placeholder = True
        evidence["dims"] = ("PLACEHOLDER stand-ins (caller-supplied), NOT from "
                            "the prompt — delivery stays refused (rule 9)")
    else:
        bare = _INTAKE_BARE_DIMS.search(prompt)
        if bare:
            raise IntakeError(
                f"the prompt states dimensions {bare[0]!r} with NO unit — "
                "units are never defaulted (rule 9). State the unit or pass "
                "placeholder_dims + placeholder_unit."
            )
        raise IntakeError(
            "the prompt states no dimensions (pattern: 'L x W x H <unit>'). "
            "Dimensions are never inferred (rule 9). Supply real dims, or "
            "pass placeholder_dims + placeholder_unit for a pipeline-exercise "
            "card (delivery stays refused)."
        )

    # ── polycount ceiling + semantics ────────────────────────────────────────
    poly_hits: list[tuple[float, str | None, str]] = []
    for m in _INTAKE_POLY_NUM_FIRST.finditer(prompt):
        poly_hits.append((_intake_number(m[1], m[2]), _semantics_from_noun(m[3]), m[0]))
    for m in _INTAKE_POLY_NOUN_FIRST.finditer(prompt):
        poly_hits.append((_intake_number(m[2], m[3]), _semantics_from_noun(m[1]), m[0]))
    polycount_ceiling: int | None = None
    polycount_semantics: str | None = None
    if poly_hits:
        key = _single([(v, s) for v, s, _ in poly_hits], "polycount")
        if key is not None:
            value, semantics = key
            if value != int(value) or value <= 0:
                raise IntakeError(
                    f"polycount ceiling {value!r} is not a positive integer — "
                    "intake does not round a constraint"
                )
            polycount_ceiling = int(value)
            polycount_semantics = semantics
            spans = " / ".join(f"'{s}'" for _, _, s in poly_hits)
            evidence["polycount_ceiling"] = (
                f"{polycount_ceiling:,} from {spans}"
                + (f" (semantics: {semantics})" if semantics
                   else " (semantics unstated → triangle_equivalent default)"))

    # ── file-size caps (must name the deliverable) ───────────────────────────
    cap_hits: list[tuple[str, SizeCap, str, tuple[int, int]]] = []
    for m in _INTAKE_CAP_FMT_FIRST.finditer(prompt):
        cap_hits.append((_CAP_TOKEN_TO_SUFFIX[m[1].lower()],
                         SizeCap(value=float(m[2]), basis="MiB" if m[3].upper() == "MIB" else "MB"),
                         m[0], m.span()))
    for m in _INTAKE_CAP_NUM_FIRST.finditer(prompt):
        cap_hits.append((_CAP_TOKEN_TO_SUFFIX[m[3].lower()],
                         SizeCap(value=float(m[1]), basis="MiB" if m[2].upper() == "MIB" else "MB"),
                         m[0], m.span()))
    file_size_caps: dict[str, SizeCap] | None = None
    if cap_hits:
        by_suffix: dict[str, list[SizeCap]] = {}
        for suffix, cap, _, _ in cap_hits:
            by_suffix.setdefault(suffix, []).append(cap)
        file_size_caps = {
            suffix: _single(caps, f"the size cap for {suffix}")
            for suffix, caps in by_suffix.items()
        }
        evidence["file_size_caps"] = "; ".join(
            f"{s}: {c.describe()}" for s, c in sorted(file_size_caps.items()))
    covered = [span for _, _, _, span in cap_hits]
    orphan = [m[0] for m in _ANY_SIZE_STATEMENT.finditer(prompt)
              if not any(s <= m.start() < e for s, e in covered)]
    if orphan:
        raise IntakeError(
            f"the prompt states a file-size cap without naming which "
            f"deliverable it applies to ({orphan!r}). Intake never guesses "
            "the target — name the format ('FBX under 10 MB', '12 MiB for "
            "the LP') or remove the statement."
        )

    # ── texture resolution ───────────────────────────────────────────────────
    res_hits: list[tuple[int, str]] = []
    for m in _INTAKE_RES_PX.finditer(prompt):
        res_hits.append((int(m[1]), m[0]))
    for m in _INTAKE_RES_K.finditer(prompt):
        res_hits.append((int(m[1]) * 1024, m[0]))
    for m in _INTAKE_RES_K_PRE.finditer(prompt):
        res_hits.append((int(m[1]) * 1024, m[0]))
    texture_resolution = _single([v for v, _ in res_hits], "texture resolution")
    if texture_resolution is not None:
        evidence["texture_resolution"] = (
            f"{texture_resolution} from "
            + " / ".join(f"'{s}'" for _, s in res_hits))

    # ── required formats (labeled clause only) ───────────────────────────────
    required_formats: list[str] | None = None
    fmt_m = _INTAKE_FORMATS_CLAUSE.search(prompt)
    if fmt_m:
        tokens = [t.strip(" .;") for t in re.split(r"[,\s]+", fmt_m[1])
                  if t.strip(" .;")]
        suffixes: list[str] = []
        for tok in tokens:
            key = tok.lower()
            if key in {"and", "&", "+"}:
                continue
            if key not in _FORMAT_TOKEN_SUFFIXES:
                raise IntakeError(
                    f"the prompt's formats clause lists {tok!r}, which this "
                    "pipeline does not produce. Known format tokens: "
                    f"{sorted(_FORMAT_TOKEN_SUFFIXES)} — write job.yaml "
                    "directly if the job needs something else."
                )
            for s in _FORMAT_TOKEN_SUFFIXES[key]:
                if s not in suffixes:
                    suffixes.append(s)
        if suffixes:
            required_formats = suffixes
            evidence["required_formats"] = (
                f"{fmt_m[0].strip()} → {suffixes}")

    # ── axis map (all three or none) ─────────────────────────────────────────
    axis_map = AxisMap()
    axis_hits: dict[str, list[str]] = {"length": [], "width": [], "height": []}
    for dim_name, pattern in _INTAKE_AXIS:
        axis_hits[dim_name].extend(m[1].lower() for m in pattern.finditer(prompt))
    stated = {k: v for k, v in axis_hits.items() if v}
    if stated:
        if len(stated) != 3:
            raise IntakeError(
                f"the prompt states axes for only {sorted(stated)} of "
                "length/width/height — a partial axis map cannot be completed "
                "without guessing (rule 9). State all three ('length along Y, "
                "width along X, height along Z') or none."
            )
        try:
            axis_map = AxisMap(
                length=_single(stated["length"], "the length axis"),
                width=_single(stated["width"], "the width axis"),
                height=_single(stated["height"], "the height axis"),
            )
        except ValueError as e:
            raise IntakeError(f"axis statements are not a valid map: {e}") from e
        evidence["axis_map"] = (
            f"L→{axis_map.length.upper()}, W→{axis_map.width.upper()}, "
            f"H→{axis_map.height.upper()} — quoted from the prompt")

    # ── FBX export axes (up+forward pair or nothing) ─────────────────────────
    fbx_axis_up = fbx_axis_forward = None
    ax_m = _INTAKE_FBX_AXES.search(prompt) or _INTAKE_FBX_AXES_ALT.search(prompt)
    if ax_m:
        fbx_axis_up = ax_m[1].upper()
        fbx_axis_forward = ax_m[2].upper()
        evidence["fbx_axes"] = (f"up {fbx_axis_up}, forward {fbx_axis_forward} "
                                f"from '{ax_m[0]}'")
    elif _INTAKE_FBX_UP_ONLY.search(prompt):
        raise IntakeError(
            "the prompt states an FBX up axis without a forward axis — a "
            "half-specified export convention is a guess (rule 9). State the "
            "pair ('FBX axes: up Y, forward -Z') or leave the default."
        )

    # ── complexity / orientation (explicit argument wins) ────────────────────
    if complexity is None:
        c_hits = [(m[1].lower(), m[0]) for m in _INTAKE_COMPLEXITY.finditer(prompt)]
        complexity = _single([v for v, _ in c_hits], "complexity")
        if complexity is not None:
            evidence["complexity"] = f"from '{c_hits[0][1]}'"
    if complexity is None:
        raise IntakeError(
            "no complexity given: pass complexity= or state "
            "'complexity: <simple|medium|complex>' in the prompt — the "
            "polycount tier is never guessed"
        )
    if orientation is None:
        o_hits: list[tuple[str, str]] = []
        for orient, pattern in _INTAKE_ORIENTATION:
            o_hits.extend((orient, m[0]) for m in pattern.finditer(prompt))
        orientation = _single([o for o, _ in o_hits], "orientation")
        if orientation is not None:
            evidence["orientation"] = f"from '{o_hits[0][1]}'"
    if orientation is None:
        raise IntakeError(
            "no orientation given: pass orientation= or state it in the "
            "prompt (e.g. 'floor-standing', 'wall-mounted') — the grounding "
            "convention is never guessed"
        )

    return JobCard(
        job_code=job_code,
        dims=dims,
        complexity=complexity,
        orientation=orientation,
        product_class=product_class,
        part_scope=part_scope,
        reference_dir=reference_dir,
        axis_map=axis_map,
        dims_placeholder=dims_placeholder,
        polycount_ceiling=polycount_ceiling,
        polycount_semantics=polycount_semantics,
        file_size_caps=file_size_caps,
        required_formats=required_formats,
        texture_resolution=texture_resolution,
        fbx_axis_up=fbx_axis_up,
        fbx_axis_forward=fbx_axis_forward,
        intake_evidence=evidence or None,
    )
