"""Unit layer — metres ↔ client units, converted ONLY at the I/O boundary.

Repo rule 8: every internal length is metres, always. The client contract
works in inches (their validator panel compares L/W/H in inches). Conversion
happens exactly once, where job-card data enters the pipeline or validator-
panel output leaves it. Nothing else in src/client may hard-code a factor.
"""

from __future__ import annotations

# Exact by international definition: 1 inch = 25.4 mm.
M_PER_INCH = 0.0254

# canonical unit -> metres
_TO_METRES: dict[str, float] = {
    "m": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "in": M_PER_INCH,
    "ft": 0.3048,
}

# accepted spellings -> canonical unit (case-insensitive). The client's own
# job card writes "IN"; be forgiving about spelling, never about absence.
_ALIASES: dict[str, str] = {
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm",
    "centimetre": "cm", "centimetres": "cm",
    "mm": "mm", "millimeter": "mm", "millimeters": "mm",
    "millimetre": "mm", "millimetres": "mm",
    "in": "in", "inch": "in", "inches": "in", '"': "in",
    "ft": "ft", "foot": "ft", "feet": "ft", "'": "ft",
}

SUPPORTED_UNITS = sorted(_TO_METRES)


def canonical_unit(unit: str) -> str:
    """Normalise a unit spelling to its canonical form. Raises loudly on an
    unknown unit — an unrecognised unit must never be guessed around."""
    key = str(unit).strip().lower()
    if key not in _ALIASES:
        raise ValueError(
            f"Unknown unit {unit!r}. Supported units: {SUPPORTED_UNITS}. "
            "The job card must declare dimensions with an explicit unit."
        )
    return _ALIASES[key]


def to_metres(value: float, unit: str) -> float:
    return float(value) * _TO_METRES[canonical_unit(unit)]


def from_metres(value: float, unit: str) -> float:
    return float(value) / _TO_METRES[canonical_unit(unit)]


def convert(value: float, from_unit: str, to_unit: str) -> float:
    return from_metres(to_metres(value, from_unit), to_unit)
