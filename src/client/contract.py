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


def required_filenames(job_code: str) -> list[str]:
    return [job_code + d.suffix for d in REQUIRED_DELIVERABLES]
