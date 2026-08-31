"""ObjectSpec specification module."""

from .schema import (
    BevelModifier,
    BooleanModifier,
    ConstraintSpec,
    GenerationMethod,
    MeasurementSpec,
    MirrorModifier,
    Modifiers,
    ObjectSpec,
    PartSpec,
    PBRMaterial,
    RadialArrayModifier,
    ShapeType,
    SubdivisionModifier,
    Unit,
)
from .validation import DimensionGateResult, evaluate_dimension_gate, validate_spec_structure
from .resolver import resolve_spec_to_build_params

__all__ = [
    "ObjectSpec",
    "PartSpec",
    "MeasurementSpec",
    "ConstraintSpec",
    "PBRMaterial",
    "ShapeType",
    "Unit",
    "GenerationMethod",
    "Modifiers",
    "BevelModifier",
    "SubdivisionModifier",
    "RadialArrayModifier",
    "MirrorModifier",
    "BooleanModifier",
    "DimensionGateResult",
    "evaluate_dimension_gate",
    "validate_spec_structure",
    "resolve_spec_to_build_params",
]
