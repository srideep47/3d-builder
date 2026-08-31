"""The client deliverable contract — the SINGLE shared definition of what a
complete package contains (GLM_BRIEF §4.1).

Amendment 1 (owner, T1 review): `check_naming` and `check_file_sizes` must
agree on which files a complete package contains. Both consume
REQUIRED_DELIVERABLES from this module, so the two gates cannot drift apart.
T2's packaging stage will consume the same definition.

Size caps are the caps OBSERVED on the client's validator panel
(FBX 2.10MB / LP GLB 2.77MB / HP GLB 7.62MB against caps 10 / 15 / 50 MB).
Caps use decimal megabytes (1 MB = 1,000,000 bytes): the client's basis is
unobserved, and decimal is the STRICTER reading, so a local pass can never
overshoot their cap (we fail closed, never learn from their validator).
"""

from __future__ import annotations

from dataclasses import dataclass

MB = 1_000_000


@dataclass(frozen=True)
class Deliverable:
    """One required file in a delivery package. `suffix` is appended to the
    job code (e.g. "MAYA00053153" + "_LP.glb"). `max_bytes` None = no known
    cap (presence still required, size not enforced)."""

    suffix: str
    kind: str  # "model" | "texture" | "project"
    max_bytes: int | None


REQUIRED_DELIVERABLES: tuple[Deliverable, ...] = (
    Deliverable(".fbx", "model", 10 * MB),
    Deliverable("_LP.glb", "model", 15 * MB),
    Deliverable("_HP.glb", "model", 50 * MB),
    Deliverable("_LP.usdz", "model", None),  # cap unknown — open question §9
    Deliverable("_BaseColor.png", "texture", None),
    Deliverable("_Normal.png", "texture", None),
    Deliverable("_Roughness.png", "texture", None),
    Deliverable("_Metallic.png", "texture", None),
    Deliverable("_AO.png", "texture", None),
)

# .spp (Substance Painter project) is listed in §4.1 but may not be required
# (open question §9.1). Never required by the naming gate until answered.
OPTIONAL_DELIVERABLES: tuple[Deliverable, ...] = (
    Deliverable(".spp", "project", None),
)

# Polycount ceilings per complexity tier, in triangles.
#   medium : 200,000 — OBSERVED on their validator panel.
#   simple : 50,000  — PROVISIONAL (brief §9.2: "budget ~50k until answered").
#   complex: None    — UNKNOWN. None makes check_polycount fail closed with
#                      "no known ceiling" instead of guessing.
TIER_TRI_CEILINGS: dict[str, int | None] = {
    "simple": 50_000,
    "medium": 200_000,
    "complex": None,
}

# Outstanding client-contract unknowns (brief §9 + T1/T2 empirical findings).
# qa_report.json carries these verbatim so a disputed delivery shows exactly
# which assumptions were in play. These are OPEN QUESTIONS, not settled
# facts — update `handling` when the client answers, never silently.
OPEN_QUESTIONS: tuple[dict[str, str], ...] = (
    {"id": "spp-required",
     "question": "Is .spp (Substance Painter project) a hard requirement, or are baked PNG sets acceptable? (brief §9.1)",
     "handling": "Blender bake path ships first; .spp is optional in every gate."},
    {"id": "simple-polycount-ceiling",
     "question": "What is the Simple tier's polycount ceiling? (Medium observed 200,000.)",
     "handling": "Provisional 50,000 in TIER_TRI_CEILINGS — one number to change."},
    {"id": "complex-polycount-ceiling",
     "question": "What is the Complex tier's polycount ceiling?",
     "handling": "Unknown; check_polycount fails closed rather than guessing."},
    {"id": "usdz-size-cap",
     "question": "What is the USDZ size cap? (brief §4.1: 'limit unknown')",
     "handling": "Presence-only check; no size enforcement."},
    {"id": "fbx-axis-convention",
     "question": "Which FBX axis/unit convention does their validator expect? (brief §9.3)",
     "handling": "Chose the FBX-standard Y-up (Blender default export); verified against "
                 "non-Blender readers — see qa_report.json axis_convention, not a Blender round trip."},
    {"id": "polycount-semantics",
     "question": "Does their validator's 'Polycount' mean triangles, faces, or triangle-equivalent? (T1 finding)",
     "handling": "We count triangle-equivalent (conservative: >= face count)."},
    {"id": "mb-basis",
     "question": "Are their MB caps decimal or binary? (T1 finding)",
     "handling": "Decimal assumed (stricter); a local pass can never overshoot their cap."},
)


def required_filenames(job_code: str) -> list[str]:
    return [job_code + d.suffix for d in REQUIRED_DELIVERABLES]
