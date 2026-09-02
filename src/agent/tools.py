"""Agent tool definitions and execution registry.

Used by native tool-calling loops. Every Blender op runs in its own process, so
stateful tools (measure/render) track the last built GLB and pass its path —
there is no shared scene between ops.

The brain never writes raw Blender Python: `execute_blender_script` was
REMOVED (master work order Phase 3.0) because arbitrary-script access
destroys the validated-spec boundary — *3DCodeBench* (arXiv 2606.01057)
measured API-mismatch and floating-geometry failures as the two dominant
modes when models author procedural code. `run_script` remains a HARNESS op
(test fixtures and developer tooling), not an agent tool; the removal is
pinned by tests/test_agent_surface.py.

Delivery tools (master work order Phase 3.1) wrap the client layer —
finish_delivery / package_delivery — and return MEASURED FACTS ONLY: gate
results with their values, counts, bounds, texel densities, timings; never
prose. The placeholder-dimension refusal (rule 9: dimensions are never
inferred) is a TOOL RESULT, not an exception crossing the brain boundary —
the brain must see the refusal and its reason. Vision verdicts are advisory
only (never gate a release), cached by image hash (docs/VISION_CONFIG.md §6),
and escalated once on disagreement with the measured gates (§3) — the loop's
visual gate and the review tool share one escalation helper so the policy
cannot drift between them.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from ..blender.runner import BlenderRunner
from ..spec.resolver import resolve_spec_to_build_params
from ..spec.schema import ObjectSpec
from ..spec.validation import evaluate_dimension_gate


AGENT_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "build_spec",
            "description": "Construct the 3D model in Blender from a structured ObjectSpec definition.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": "The complete ObjectSpec dictionary containing parts, materials, and measurements.",
                    }
                },
                "required": ["spec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "measure_model",
            "description": "Measure the last built model (or an explicit model_path): overall and per-part bounding dimensions in meters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "model_path": {
                        "type": "string",
                        "description": "Optional path to a 3D file; defaults to the last model built in this session.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_model",
            "description": "Render multi-view studio preview images (front, side, top, iso) of the last built model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "views": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of views to render (e.g. ['front', 'side', 'top', 'iso'])",
                    },
                    "model_path": {
                        "type": "string",
                        "description": "Optional path to a 3D file; defaults to the last model built in this session.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect",
            "description": (
                "Full measured inspection of the last built model (or an explicit model_path): "
                "per-part dimensions/bounds/closed-solid, overall polycount, n-gon count, "
                "UV/texel-density diagnostics, and the gate results WITH their values "
                "(dimension deltas in mm, polycount vs budget, ground contact)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "model_path": {
                        "type": "string",
                        "description": "Optional path to a 3D file; defaults to the last model built in this session.",
                    },
                    "spec": {
                        "type": "object",
                        "description": "Optional ObjectSpec dict for the dimension/polycount gates; defaults to the last spec built in this session.",
                    },
                    "resolution": {
                        "type": "integer",
                        "description": "Texture resolution used for texel-density measurement (default 1024).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review",
            "description": (
                "Render the review view set (front/side/top/iso plus spec close-ups) and, when "
                "reference images are supplied and a vision provider is configured, take the "
                "advisory render-vs-reference verdict. Returns render file paths and the verdict "
                "JSON; verdicts are cached by image hash."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "model_path": {
                        "type": "string",
                        "description": "Optional path to a 3D file; defaults to the last model built in this session.",
                    },
                    "reference_images": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Reference image paths to compare the renders against.",
                    },
                    "views": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Views to render (default ['front', 'side', 'top', 'iso']).",
                    },
                    "closeups": {
                        "type": "array",
                        "description": "Close-up definitions [{name, part, direction, pad, frame}]; defaults to the spec's review_closeups.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Run the full delivery finishing chain (finish_delivery) for a job card and the "
                "current spec: quad-clean scene, per-island UV atlas, 5-map bake, LP decimation, "
                "FBX/USDZ export, gates. Returns measured facts (tri counts, bake device and "
                "step timings, UV/texel diagnostics, gate results). Refuses with "
                "success=false, refused=true when the job card has placeholder dimensions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job": {
                        "type": "string",
                        "description": "Path to the job card YAML (owner-supplied dimensions + client constraints).",
                    },
                    "spec": {
                        "type": "object",
                        "description": "Optional ObjectSpec dict; defaults to the last spec built in this session.",
                    },
                    "resolution": {
                        "type": "integer",
                        "description": "Bake texture resolution (default 1024; 4K bakes need bake_timeout_sec ~3600).",
                    },
                    "bake_timeout_sec": {
                        "type": "number",
                        "description": "Bake subprocess timeout in seconds (default 300).",
                    },
                    "bake_device": {
                        "type": "string",
                        "description": "Cycles device: auto | cpu | optix | cuda (default auto).",
                    },
                    "review_renders": {
                        "type": "boolean",
                        "description": "Render the review view set (default true).",
                    },
                    "out_root": {
                        "type": "string",
                        "description": "Package root directory (default output/packages).",
                    },
                },
                "required": ["job"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "package",
            "description": (
                "Assemble the client delivery package (package_delivery) from a source GLB and a "
                "job card: FBX/USDZ export, placeholder texture set, all six client gates. Returns "
                "the deliverable manifest (files, sizes, hashes) and gate results with values. "
                "Refuses with success=false, refused=true on placeholder dimensions (rule 9)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job": {
                        "type": "string",
                        "description": "Path to the job card YAML (owner-supplied dimensions + client constraints).",
                    },
                    "source_glb": {
                        "type": "string",
                        "description": "Source GLB path; defaults to the last model built in this session.",
                    },
                    "out_root": {
                        "type": "string",
                        "description": "Package root directory (default output/packages).",
                    },
                },
                "required": ["job"],
            },
        },
    },
]


def advisory_visual_verdict(
    vlm: Any,
    rendered_views: dict[str, str],
    refs: list[str],
    model_summary: str = "",
) -> dict[str, Any]:
    """Advisory render-vs-reference verdict with the shared one-step
    escalation policy (docs/VISION_CONFIG.md §3).

    Callers run this ONLY after the measured gates are green, so
    matches_reference=False IS the disagreement with the gates: exactly ONE
    escalated verdict is taken with the provider's configured escalation
    model, and both verdicts are recorded (escalated_from). Used by the agent
    loop's visual gate and the review tool so the policy cannot drift.
    """
    verdict = vlm.visual_verdict(rendered_views, refs, model_summary=model_summary)
    if (
        verdict.get("parsed")
        and not verdict.get("matches_reference")
        and getattr(vlm, "escalation_model", None)
    ):
        escalated = vlm.visual_verdict(
            rendered_views, refs, model_summary=model_summary, escalate=True
        )
        escalated["escalated_from"] = verdict
        verdict = escalated
    return verdict


class AgentToolExecutor:
    def __init__(self, runner: BlenderRunner, workdir: str | Path | None = None):
        self.runner = runner
        self.workdir = Path(workdir) if workdir else Path("output") / "agent_tool"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.last_built_glb: str | None = None
        self.last_spec: ObjectSpec | None = None
        # fallback polycount budget when the spec carries no tri_budget
        self.default_tri_budget = 50000
        # advisory vision verdicts, keyed by image-hash cache key (§6)
        self._verdict_cache: dict[str, dict[str, Any]] = {}
        self._vlm = None
        self._vlm_checked = False

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get_vlm(self):
        if not self._vlm_checked:
            self._vlm_checked = True
            try:
                from ..ai.vlm import get_vision_provider

                self._vlm = get_vision_provider()
            except Exception:
                self._vlm = None
        return self._vlm

    def _spec_from_args(
        self, args: dict[str, Any]
    ) -> tuple[ObjectSpec | None, str | None]:
        """Explicit spec arg wins (validated loudly); otherwise the last spec
        built in this session. An INVALID explicit spec is an error, never a
        silent fallback to the stale last_spec."""
        raw = args.get("spec")
        if raw is None:
            return self.last_spec, None
        try:
            return ObjectSpec.model_validate(raw), None
        except Exception as e:
            return None, f"Invalid ObjectSpec schema: {e}"

    @staticmethod
    def _file_hash(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def _verdict_cache_key(
        self, rendered_views: dict[str, str], refs: list[str], vlm: Any
    ) -> str:
        def h(p: str) -> str:
            try:
                return self._file_hash(p)
            except OSError:
                return f"missing:{p}"

        parts = [h(p) for p in sorted(rendered_views.values())]
        parts += [h(p) for p in sorted(refs)]
        parts.append(str(getattr(vlm, "model", "") or ""))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    @staticmethod
    def _finish_facts(report: dict[str, Any]) -> dict[str, Any]:
        """Measured-facts extraction from a finish_delivery report."""
        fin = report.get("finish", {}) or {}
        uv = fin.get("uv_diagnostics", {}) or {}
        return {
            "success": True,
            "package_dir": report.get("package_dir"),
            "all_gates_passed": report.get("all_passed"),
            "gates": report.get("gates"),
            "lp_tri_equivalent": fin.get("lp_tri_equivalent"),
            "hp_tri_equivalent": fin.get("hp_tri_equivalent"),
            "lp_budget": fin.get("lp_budget"),
            "lp_decimated": fin.get("lp_decimated"),
            "texture_resolution": fin.get("texture_resolution"),
            "bake_device_resolved": fin.get("bake_device_resolved"),
            "step_timings_sec": fin.get("step_timings_sec"),
            "bake_maps": fin.get("bake"),
            "uv_atlas": fin.get("uv_atlas"),
            "uv_diagnostics": uv,
            "texel_density_per_object": uv.get("texel_density_per_object"),
            "review_renders": fin.get("review_renders"),
        }

    def _load_job_arg(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        from ..client.job import load_job

        job_path = args.get("job")
        if not job_path:
            return None, "'job' is required: path to the job card YAML"
        try:
            return load_job(job_path), None
        except Exception as e:
            return None, f"job card failed to load: {e}"

    # ── read-only inspection ──────────────────────────────────────────────────

    def _inspect_model(
        self, model_path: str, spec_obj: ObjectSpec | None, resolution: int
    ) -> dict[str, Any]:
        topo = self.runner.execute_op("topology_report", {"model_path": model_path})
        if not topo.get("success"):
            return topo
        measure = self.runner.execute_op("measure", {"model_path": model_path})
        uv = self.runner.execute_op("uv_report", {"model_path": model_path, "resolution": resolution})

        # merge per-part measured facts: dimensions from measure, topology
        # flags from objects_detail (same import path, same names)
        parts: dict[str, Any] = {}
        for pname, pdata in (measure.get("parts") or {}).items():
            parts[pname] = {
                "dimensions_m": pdata.get("dimensions"),
                "bounds_min_m": pdata.get("min"),
                "bounds_max_m": pdata.get("max"),
                "bottom_z_m": pdata.get("bottom_z"),
                "top_z_m": pdata.get("top_z"),
            }
        for detail in topo.get("objects_detail") or []:
            entry = parts.setdefault(detail.get("name", "?"), {})
            for key in (
                "vertices", "faces_total", "triangles", "quads", "ngons",
                "triangle_equivalent", "loose_vertices", "loose_edges",
                "boundary_edges", "nonmanifold_edges", "closed_solid",
            ):
                entry[key] = detail.get(key)

        tri_eq = int(topo.get("triangle_equivalent", 0) or 0)
        ngons = int(topo.get("ngons", 0) or 0)
        budget = int(getattr(spec_obj, "tri_budget", 0) or 0) or self.default_tri_budget
        open_parts = [n for n, e in parts.items() if e.get("closed_solid") is False]

        gates: dict[str, Any] = {
            "polycount": {
                "passed": tri_eq <= budget,
                "triangle_equivalent": tri_eq,
                "budget": budget,
            },
            "ngons": {"passed": ngons == 0, "count": ngons},
            "closed_solids": {"passed": not open_parts, "open_parts": open_parts},
        }
        if spec_obj is not None and measure.get("success"):
            dim = evaluate_dimension_gate(spec_obj, measure)
            gates["dimensions"] = {
                "passed": dim.passed,
                "measurements_checked": dim.measurements_checked,
                "passed_count": dim.passed_count,
                "failed_count": dim.failed_count,
                "max_delta_m": dim.max_delta_m,
                "details": dim.details,
                "ground_contact_passed": dim.ground_contact_passed,
                "ground_contact_min_z": dim.ground_contact_min_z,
                "ground_contact_failures": dim.ground_contact_failures,
            }

        uv_diag = uv.get("uv") if uv.get("success") else None
        if uv_diag is None:
            uv_section: dict[str, Any] = {
                "available": False,
                "reason": uv.get("error", "uv_report failed"),
            }
        elif not uv_diag.get("islands_total"):
            uv_section = {
                "available": False,
                "reason": uv_diag.get("reason", "no UV islands"),
            }
        else:
            uv_section = {"available": True, **uv_diag}

        return {
            "success": True,
            "model_path": model_path,
            "units": "meters",
            "bounds_m": topo.get("bounds"),
            "polycount": {
                "vertices": topo.get("vertices"),
                "faces_total": topo.get("faces_total"),
                "triangles": topo.get("triangles"),
                "quads": topo.get("quads"),
                "ngons": ngons,
                "triangle_equivalent": tri_eq,
            },
            "parts": parts,
            "gates": gates,
            "all_gates_passed": all(g["passed"] for g in gates.values()),
            "uv": uv_section,
        }

    def _review_verdict(
        self, rendered_views: dict[str, str], refs: list[str]
    ) -> dict[str, Any]:
        vlm = self._get_vlm()
        if vlm is None or not vlm.is_available():
            return {"available": False, "reason": "no vision provider configured or reachable"}
        summary = ""
        if self.last_spec is not None:
            summary = f"{self.last_spec.name}: {len(self.last_spec.parts)} parts"
        key = self._verdict_cache_key(rendered_views, refs, vlm)
        cached = self._verdict_cache.get(key)
        if cached is not None:
            return {**cached, "cached": True}
        try:
            verdict = advisory_visual_verdict(vlm, rendered_views, refs, summary)
        except Exception as e:
            return {"available": True, "parsed": False, "error": str(e)[:400]}
        self._verdict_cache[key] = verdict
        return verdict

    # ── tool dispatch ─────────────────────────────────────────────────────────

    def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        output_glb: str | None = None,
        renders_dir: str | None = None,
    ) -> dict[str, Any]:
        if tool_name == "build_spec":
            raw_spec = args.get("spec", {})
            try:
                spec_obj = ObjectSpec.model_validate(raw_spec)
            except Exception as e:
                return {"success": False, "error": f"Invalid ObjectSpec schema: {e}"}

            if output_glb is None:
                output_glb = str(self.workdir / f"build_{int(time.time())}.glb")
            params = resolve_spec_to_build_params(spec_obj, output_glb_path=str(Path(output_glb).resolve()))
            result = self.runner.execute_op("build_from_spec", params)
            if result.get("success"):
                self.last_built_glb = str(Path(output_glb).resolve())
                self.last_spec = spec_obj
            return result

        if tool_name == "measure_model":
            model_path = args.get("model_path") or self.last_built_glb
            if not model_path:
                return {"success": False, "error": "No model built yet and no model_path given"}
            return self.runner.execute_op("measure", {"model_path": str(Path(model_path).resolve())})

        if tool_name == "render_model":
            model_path = args.get("model_path") or self.last_built_glb
            if not model_path:
                return {"success": False, "error": "No model built yet and no model_path given"}
            out_dir = renders_dir or str(self.workdir / "renders")
            params = {
                "model_path": str(Path(model_path).resolve()),
                "views": args.get("views", ["front", "side", "top", "iso"]),
                "output_dir": str(Path(out_dir).resolve()),
                "prefix": "view",
            }
            return self.runner.execute_op("render_views", params)

        if tool_name == "inspect":
            model_path = args.get("model_path") or self.last_built_glb
            if not model_path:
                return {"success": False, "error": "No model built yet and no model_path given"}
            model_path = str(Path(model_path).resolve())
            if not Path(model_path).is_file():
                return {"success": False, "error": f"model not found: {model_path}"}
            spec_obj, spec_err = self._spec_from_args(args)
            if spec_err:
                return {"success": False, "error": spec_err}
            return self._inspect_model(model_path, spec_obj, int(args.get("resolution", 1024)))

        if tool_name == "review":
            model_path = args.get("model_path") or self.last_built_glb
            if not model_path:
                return {"success": False, "error": "No model built yet and no model_path given"}
            model_path = str(Path(model_path).resolve())
            refs = []
            for r in args.get("reference_images") or []:
                p = Path(r)
                if p.is_file():
                    refs.append(str(p.resolve()))
            closeups = args.get("closeups")
            if closeups is None:
                closeups = [
                    {
                        "name": c.name,
                        "part": c.part,
                        "direction": c.direction,
                        "pad": c.pad,
                        "frame": c.frame,
                    }
                    for c in (getattr(self.last_spec, "review_closeups", None) or [])
                ]
            out_dir = renders_dir or str(self.workdir / "review")
            params: dict[str, Any] = {
                "model_path": model_path,
                "views": args.get("views", ["front", "side", "top", "iso"]),
                "output_dir": str(Path(out_dir).resolve()),
                "prefix": "review",
            }
            if closeups:
                params["closeups"] = closeups
            rv = self.runner.execute_op("render_views", params)
            if not rv.get("success"):
                return rv
            result: dict[str, Any] = {
                "success": True,
                "model_path": model_path,
                "renders": rv.get("views", {}),
                "closeup_skips": rv.get("closeup_skips", []),
                "vision_verdict": None,
            }
            if refs:
                result["vision_verdict"] = self._review_verdict(rv.get("views", {}), refs)
            return result

        if tool_name == "finish":
            job, job_err = self._load_job_arg(args)
            if job_err:
                return {"success": False, "error": job_err}
            spec_obj, spec_err = self._spec_from_args(args)
            if spec_err:
                return {"success": False, "error": spec_err}
            if spec_obj is None:
                return {"success": False, "error": "No spec: pass 'spec' or build one first"}
            from ..client.package import PlaceholderDimensionsError, finish_delivery

            try:
                report = finish_delivery(
                    job,
                    spec_obj,
                    out_root=Path(args.get("out_root", "output/packages")),
                    runner=self.runner,
                    resolution=int(args.get("resolution", 1024)),
                    review_renders=bool(args.get("review_renders", True)),
                    bake_timeout_sec=float(args.get("bake_timeout_sec", 300.0)),
                    bake_device=str(args.get("bake_device", "auto")),
                )
            except PlaceholderDimensionsError as e:
                # rule 9 refusal — a result the brain can read, never a crash
                return {
                    "success": False,
                    "refused": True,
                    "reason": "dims_placeholder",
                    "error": str(e),
                }
            except Exception as e:  # noqa: BLE001 — the brain sees errors as results
                return {"success": False, "error": str(e)[:1000]}
            return self._finish_facts(report)

        if tool_name == "package":
            job, job_err = self._load_job_arg(args)
            if job_err:
                return {"success": False, "error": job_err}
            source = args.get("source_glb") or self.last_built_glb
            if not source and not job.dims_placeholder:
                return {
                    "success": False,
                    "error": "No source GLB: pass 'source_glb' or build a model first",
                }
            # a placeholder-dims job refuses BEFORE the source is touched, so
            # a missing source must never mask the rule-9 refusal
            source_path = Path(source) if source else Path("output") / "unbuilt.glb"
            from ..client.package import PlaceholderDimensionsError, package_delivery

            try:
                report = package_delivery(
                    job,
                    source_path,
                    out_root=Path(args.get("out_root", "output/packages")),
                    runner=self.runner,
                )
            except PlaceholderDimensionsError as e:
                return {
                    "success": False,
                    "refused": True,
                    "reason": "dims_placeholder",
                    "error": str(e),
                }
            except Exception as e:  # noqa: BLE001 — the brain sees errors as results
                return {"success": False, "error": str(e)[:1000]}
            return {
                "success": True,
                "package_dir": report.get("package_dir"),
                "all_gates_passed": report.get("all_passed"),
                "gates": report.get("gates"),
                "files": report.get("files"),
                "placeholders": report.get("placeholders", {}),
                "axis_convention": report.get("axis_convention"),
            }

        # execute_blender_script is INTENTIONALLY not callable: the brain
        # never writes raw Blender Python (validated-spec boundary; the
        # removal is pinned by tests/test_agent_surface.py).
        return {"success": False, "error": f"Unknown tool '{tool_name}'"}
