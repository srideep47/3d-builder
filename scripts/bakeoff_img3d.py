"""img3d backend bake-off (PLAN.md §9, M4).

Scores every available img3d backend against a set of reference images:
generation time, triangle count, watertightness, normal consistency, and
target-scale accuracy. Run on the inference host (Forge) with the service up:

  .venv/Scripts/python scripts/bakeoff_img3d.py --images input/bakeoff
  .venv/Scripts/python scripts/bakeoff_img3d.py --images input/bakeoff --backend tripo_sr

Results print as a table and land in output/bakeoff_<timestamp>.json.
Scale accuracy is expected to be exact (the service enforces target bounds);
watertightness and time are the real differentiators.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "services" / "img3d_service"))

import httpx  # noqa: E402
import trimesh  # noqa: E402

SERVICE_URL = "http://127.0.0.1:8501"
TARGETS = [
    [0.30, 0.20, 0.15],
    [0.50, 0.50, 0.40],
    [0.12, 0.12, 0.25],
]


def service_backends(base_url: str) -> dict:
    r = httpx.get(f"{base_url}/models", timeout=15)
    r.raise_for_status()
    return r.json()["backends"]


def generate(base_url: str, image: Path, target: list[float], backend: str) -> dict:
    with open(image, "rb") as f:
        r = httpx.post(
            f"{base_url}/generate",
            files={"file": (image.name, f, "image/png")},
            data={
                "target_x": target[0], "target_y": target[1], "target_z": target[2],
                "max_tris": "50000",
            },
            timeout=30.0,
        )
    r.raise_for_status()
    job_id = r.json()["job_id"]

    deadline = time.monotonic() + 600
    while True:
        res = httpx.get(f"{base_url}/result/{job_id}", timeout=15).json()
        if res["status"] == "failed":
            return {"error": res.get("error")}
        if res["status"] == "completed":
            break
        if time.monotonic() > deadline:
            return {"error": "timeout"}
        time.sleep(1.0)

    dl = httpx.get(f"{base_url}/download/{job_id}", timeout=60)
    dl.raise_for_status()
    out = Path("output") / "bakeoff" / f"{image.stem}_{backend}.glb"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(dl.content)

    mesh = trimesh.load(out, force="mesh", process=True)
    scale_err = max(abs(mesh.extents[i] - target[i]) for i in range(3))
    return {
        "glb": str(out),
        "tris": int(len(mesh.faces)),
        "watertight": bool(mesh.is_watertight),
        "normal_consistency": round(float(mesh.volume > 0), 2),  # outward normals
        "scale_err_m": round(scale_err, 6),
        "duration_s": res.get("duration_sec"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", default="input/bakeoff", help="Directory of reference images")
    parser.add_argument("--backend", default=None, help="Score only this backend (default: all available)")
    parser.add_argument("--service", default=SERVICE_URL)
    args = parser.parse_args()

    image_dir = Path(args.images)
    images = sorted(
        p for p in image_dir.iterdir()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    ) if image_dir.is_dir() else []
    if not images:
        print(f"No reference images in {image_dir} — add PNG/JPG files there first.")
        return 1

    backends = service_backends(args.service)
    names = [args.backend] if args.backend else [
        n for n, info in backends.items() if info["available"]
    ]
    print(f"Backends to score: {', '.join(names)}  ·  images: {len(images)}  ·  targets: {len(TARGETS)}")

    results: dict[str, dict] = {}
    for name in names:
        print(f"\n=== {name} ===")
        runs = []
        for img in images:
            for i, target in enumerate(TARGETS):
                print(f"  {img.name} → {target} ...", end=" ", flush=True)
                run = generate(args.service, img, target, name)
                print(run.get("error") or f"{run['tris']} tris, {run['duration_s']}s, wt={run['watertight']}")
                runs.append({"image": img.name, "target_m": target, **run})
        ok = [r for r in runs if "error" not in r]
        results[name] = {
            "runs": runs,
            "summary": {
                "completed": len(ok),
                "failed": len(runs) - len(ok),
                "avg_duration_s": round(sum(r["duration_s"] for r in ok) / len(ok), 2) if ok else None,
                "avg_tris": int(sum(r["tris"] for r in ok) / len(ok)) if ok else None,
                "watertight_rate": round(sum(1 for r in ok if r["watertight"]) / len(ok), 2) if ok else None,
                "max_scale_err_m": max((r["scale_err_m"] for r in ok), default=None),
            },
        }

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = Path("output") / f"bakeoff_{stamp}.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"{'backend':<12} {'done':>5} {'avg s':>7} {'avg tris':>9} {'watertight':>11} {'scale err':>10}")
    for name, data in results.items():
        s = data["summary"]
        print(
            f"{name:<12} {s['completed']:>5} {str(s['avg_duration_s']):>7} "
            f"{str(s['avg_tris']):>9} {str(s['watertight_rate']):>11} {str(s['max_scale_err_m']):>10}"
        )
    print(f"\nDetailed results: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
