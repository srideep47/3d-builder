"""Phase 6 intake driver — turn each PHASE6 prompt into a job card.

Deterministic front door (src/client/job.py:intake_from_prompt): the prompt
document is the single source of truth for both the job card and the analyst
build. Structural dispatch facts (job code, product class, reference dir) are
supplied here, never scraped from free text. Writes input/jobs/<CODE>.yaml.

Usage: uv run python scripts/phase6_intake.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.client.job import dump_job_yaml, intake_from_prompt

SUBJECTS = [
    ("COATSTAND0001", "coat_stand", "PHASE6-A-COATSTAND"),
    ("MAILBOX0001", "wall_mailbox", "PHASE6-B-MAILBOX"),
    ("WATERCAN0001", "watering_can", "PHASE6-C-WATERINGCAN"),
]


def main() -> int:
    dry = "--dry-run" in sys.argv
    root = Path(__file__).resolve().parents[1]
    for code, product_class, ref_dir_name in SUBJECTS:
        ref_dir = root / "input" / "references" / ref_dir_name
        prompt = (ref_dir / "PROMPT.md").read_text(encoding="utf-8")
        card = intake_from_prompt(
            prompt,
            job_code=code,
            product_class=product_class,
            reference_dir=ref_dir,
        )
        print(f"== {code} ({product_class}) ==")
        print(f"  dims: {card.dims.length:g} x {card.dims.width:g} x "
              f"{card.dims.height:g} {card.dims.unit}  "
              f"placeholder={card.dims_placeholder}")
        print(f"  complexity: {card.complexity}  orientation: {card.orientation}")
        print(f"  polycount_ceiling: {card.polycount_ceiling} "
              f"(semantics: {card.polycount_semantics})")
        for k, v in (card.intake_evidence or {}).items():
            print(f"  evidence[{k}]: {v}")
        if not dry:
            dest = root / "input" / "jobs" / f"{code}.yaml"
            dest.write_text(dump_job_yaml(card), encoding="utf-8")
            print(f"  wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
