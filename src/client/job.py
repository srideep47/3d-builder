"""Job intake — one client job card as data (GLM_BRIEF §7 T1).

Rule 9 (absolute): dimensions are owner-supplied with an EXPLICIT unit for
every job. A job card without `dims`, or dims without `unit`, fails loud
validation — a unit is never defaulted and a dimension never inferred. The
job card in the client dashboard (12 × 12 × 65 IN) and a hallucinated
60 × 80 × 10 in are both irrelevant: only what the owner writes in job.yaml
counts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

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

    @field_validator("job_code")
    @classmethod
    def _safe_job_code(cls, v: str) -> str:
        v = v.strip()
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            # job_code becomes file names — no path separators, ever.
            raise ValueError(f"job_code {v!r} must be non-empty alphanumerics/-/_")
        return v

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
