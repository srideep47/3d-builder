"""View-diversity measurement for multi-view neural intake (§3.1).

Measured finding (GLM_PROMPT_NEURAL_INTAKE.md §3.1): TRELLIS dimensional
accuracy tracks INPUT VIEW SPREAD, not model quality. The cup — two
genuinely opposite side views — came out exact to three decimals
(1 : 0.778 : 0.556); the mattress — four variations of the same
front/three-quarter view — came out square (1 : 0.999 vs truth
1 : 0.750). Nothing in the model tells it the bed is longer than it is
wide, so a square mattress must never be a surprise AFTERWARDS.

This module measures how different the uploaded viewpoints actually
are, so the intake UI can WARN — loudly and visibly — when they are
near-duplicates. It must NOT refuse: the owner's explicit call is that
the run still goes ahead (§5: low view diversity is not a stop).

Metric choice is CALIBRATED, not guessed (measured 2026-09-03 on the
four sets in `Test Images/`, 64-bit dHash, mean pairwise normalised
Hamming distance):

    set        images   mean    max    min    generated vs real ratio
    cup        4        0.299   0.422  0.156  exact (1:0.778:0.556)
    desk       3        0.323   0.422  0.141  close (0.589 vs 0.625)
    doormat    4        0.344   0.453  0.203  close (0.733 vs 0.600)
    mattress   4        0.135   0.203  0.062  SQUARE (0.999 vs 0.750)

Downscaled-greyscale correlation was REJECTED by the same calibration:
mattress mean 1−cos = 0.389 ≥ cup 0.377 — it tracks exposure and
framing differences between shots of the same view, not viewpoint
structure. dHash (horizontal gradient structure) is invariant to those
and separates the calibration pair 2.2×.

WARN_THRESHOLD = 0.20 is a round number inside the measured gap
[mattress 0.135, cup 0.299], biased toward the mattress end so only
genuinely near-duplicate sets warn. Per-pair distances, max and min
ride into the result so a bad generation can always be traced back to
its inputs (§3.1: record the score in the run manifest).

Deterministic, pure PIL+numpy, no network, no torch — safe at intake
time in the light main environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import numpy as np
from PIL import Image

WARN_THRESHOLD = 0.20


@dataclass
class ViewDiversity:
    """The measured diversity of one uploaded view set."""

    score: float | None  # mean pairwise dHash distance in [0, 1]; None if < 2 images
    max_pairwise: float | None
    min_pairwise: float | None
    pairwise: list[dict[str, object]] = field(default_factory=list)
    image_count: int = 0
    warned: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.warned

    def describe(self) -> str:
        if self.score is None:
            return self.reason
        return (f"view diversity {self.score:.3f} "
                f"(max pair {self.max_pairwise:.3f}, {self.image_count} views)")


def _load_grey(path: Path, width: int, height: int) -> np.ndarray:
    """Greyscale thumbnail; alpha composited on mid-grey so transparent
    regions do not read as black structure."""
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "PA"):
        bg = Image.new("RGB", img.size, (128, 128, 128))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    return np.asarray(img.convert("L").resize((width, height), Image.LANCZOS),
                      dtype=np.float64)


def _dhash(path: Path, size: int = 8) -> np.ndarray:
    """64-bit difference hash: horizontal gradient comparisons of a
    (size+1)×size greyscale thumbnail. Invariant to global exposure.
    The resize is (size+1)×size exactly — WARN_THRESHOLD was calibrated
    on this pipeline; do not change the sampling geometry without
    re-measuring on the four Test Images/ sets."""
    g = _load_grey(path, size + 1, size)
    return (g[:, 1:] > g[:, :-1]).ravel()


def _hamming(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.count_nonzero(a != b)) / float(a.size)


def measure_view_diversity(image_paths: list[str | Path]) -> ViewDiversity:
    """Measure pairwise view diversity of an uploaded set.

    Returns a ViewDiversity with the mean pairwise dHash distance as the
    headline score. Fewer than 2 usable images → score None and a warning
    (a single view is the degenerate near-duplicate case). Missing files
    are skipped and named in the reason — intake keeps going on what
    exists; the neural route's own four-slot requirement is enforced
    elsewhere.
    """
    paths = [Path(p) for p in image_paths]
    usable = [p for p in paths if p.is_file()]
    missing = [p.name for p in paths if not p.is_file()]
    notes = []
    if missing:
        notes.append(f"skipped missing: {', '.join(missing)}")

    if len(usable) < 2:
        reason = "; ".join(
            notes + ["fewer than 2 usable images — diversity cannot be measured"]
        )
        return ViewDiversity(score=None, max_pairwise=None, min_pairwise=None,
                             pairwise=[], image_count=len(usable),
                             warned=True, reason=reason)

    hashes = [_dhash(p) for p in usable]
    pairwise = []
    for (i, a), (j, b) in combinations(enumerate(usable), 2):
        d = _hamming(hashes[i], hashes[j])
        pairwise.append({"a": a.name, "b": b.name, "distance": round(d, 4)})
    dists = [p["distance"] for p in pairwise]  # type: ignore[index]
    mean = float(np.mean(dists))
    result = ViewDiversity(
        score=round(mean, 4),
        max_pairwise=round(float(max(dists)), 4),  # type: ignore[type-var]
        min_pairwise=round(float(min(dists)), 4),  # type: ignore[type-var]
        pairwise=pairwise,
        image_count=len(usable),
    )
    if mean < WARN_THRESHOLD:
        result.warned = True
        result.reason = (
            f"view diversity {mean:.3f} is BELOW the {WARN_THRESHOLD} calibration "
            f"floor — the uploaded views are near-duplicates of one another, so "
            f"neural generation cannot recover the real proportions (measured "
            f"failure: a square mattress from four same-angle photos). The run "
            f"still goes ahead — this is the owner's call — but do not be "
            f"surprised by wrong aspect ratios."
        )
    else:
        result.reason = (
            f"view diversity {mean:.3f} at or above the {WARN_THRESHOLD} "
            f"calibration floor"
        )
    if notes:
        result.reason = "; ".join(notes) + "; " + result.reason
    return result
