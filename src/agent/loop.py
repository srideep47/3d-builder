"""Agent Loop — autonomous build-measure-verify feedback cycle.

Each iteration: (optional spec correction) -> build in Blender -> measure the
exported GLB -> render views -> run gates -> feed deltas back to the corrector.

Every Blender op runs in its own process (see runner.py), so `measure` and
`render_views` MUST be pointed at the step GLB file — there is no shared scene.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..ai.aptos import AptosGLMProvider, extract_json_from_text, vision_user_content
from ..ai.schemas import ChatMessage
from ..blender.runner import BlenderRunner
from ..run_store import RunManifest, RunStore
from ..spec.resolver import resolve_spec_to_build_params
from ..spec.schema import GenerationMethod, ObjectSpec, ShapeType
from ..spec.validation import validate_spec_structure
from .prompts import ANALYST_SYSTEM_PROMPT, CORRECTOR_SYSTEM_PROMPT
from .verifier import VerificationReport, Verifier


def _safe_filename(name: str) -> str:
    """Windows-safe, collision-free-ish file stem for a part name."""
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in name.strip()]
    stem = "".join(keep).strip("_") or "part"
    return stem[:80]


@dataclass
class AgentRunResult:
    success: bool
    spec: ObjectSpec
    final_glb_path: str | Path | None
    run_dir: Path
    manifest_path: Path
    verification: VerificationReport | None
    iterations: int
    renders: dict[str, str]
    error: str | None = None


class AgentLoop:
    def __init__(
        self,
        provider: AptosGLMProvider | None = None,
        runner: BlenderRunner | None = None,
        verifier: Verifier | None = None,
        run_store: RunStore | None = None,
        max_iterations: int | None = None,
    ):
        self.provider = provider or AptosGLMProvider()
        self.runner = runner or BlenderRunner()
        self.verifier = verifier or Verifier()
        self.run_store = run_store or RunStore()
        agent_cfg = self.provider.config.get("agent", {}) or {}
        self.max_iterations = max_iterations or int(agent_cfg.get("max_iterations", 5))
        self.wall_clock_budget_s = float(agent_cfg.get("wall_clock_budget_s", 900))
        # img3d (neural image-to-3D) provider — resolved lazily on first use so
        # builds without organic parts never touch the service.
        self._img3d_provider = None
        self._img3d_checked = False

    # ── Neural parts (image_to_3d) ───────────────────────────────────────────

    def _get_img3d_provider(self):
        """The configured img3d provider, or None. Cached for the process."""
        if not self._img3d_checked:
            self._img3d_checked = True
            try:
                from ..img3d import get_img3d_provider

                self._img3d_provider = get_img3d_provider()
            except Exception:
                self._img3d_provider = None
        return self._img3d_provider

    def _normalize_spec_methods(self, spec: ObjectSpec) -> None:
        """Deterministic routing fix: 'organic' shapes cannot be built
        parametrically — if the analyst/corrector left method as 'parametric'
        on one, route it to image_to_3d (the harness would otherwise fail with
        'Unknown shape organic')."""
        for p in spec.parts:
            if p.shape == ShapeType.ORGANIC and p.method != GenerationMethod.IMAGE_TO_3D:
                p.method = GenerationMethod.IMAGE_TO_3D

    def _resolve_part_image(self, spec: ObjectSpec, part, image_paths: list[Path]) -> Path | None:
        """Best reference image for an image_to_3d part: its declared crop,
        then the spec's source images, then the run's uploaded images."""
        candidates: list[str] = []
        if part.image_crop:
            candidates.append(str(part.image_crop).split("#")[0])
        candidates.extend(str(s) for s in (spec.source_images or []))
        candidates.extend(str(p) for p in image_paths)
        for c in candidates:
            p = Path(c)
            if p.exists():
                return p
        return None

    def _prepare_neural_parts(
        self,
        spec: ObjectSpec,
        run_dir: Path,
        image_paths: list[Path],
        emit=None,
        cancelled=None,
    ) -> None:
        """Generate meshes for image_to_3d parts via the local neural service
        and attach mesh_path so build_from_spec imports them.

        Cached by part name under run_dir/neural/ — corrector rewrites of the
        spec reuse the files instead of regenerating (and the harness re-scales
        to the part's current target_size on import, so dimension corrections
        still converge without regeneration)."""
        neural_parts = [p for p in spec.parts if p.method == GenerationMethod.IMAGE_TO_3D]
        if not neural_parts:
            return

        def fire(event: str, **data) -> None:
            if emit is not None:
                try:
                    emit(event, **data)
                except Exception:
                    pass

        provider = self._get_img3d_provider()
        if provider is None:
            fire("neural_skipped", reason="img3d disabled in config/hardware.yaml", parts=[p.name for p in neural_parts])
            return
        if not provider.is_available():
            fire("neural_skipped", reason=f"img3d service unreachable at {provider.base_url}", parts=[p.name for p in neural_parts])
            return

        neural_dir = run_dir / "neural"
        for part in neural_parts:
            if cancelled and cancelled():
                break
            if part.mesh_path and Path(part.mesh_path).exists():
                continue
            cache_path = neural_dir / f"{_safe_filename(part.name)}.glb"
            if cache_path.exists():
                part.mesh_path = str(cache_path)
                if not part.target_size:
                    part.target_size = [float(d) for d in part.dimensions]
                continue

            image = self._resolve_part_image(spec, part, image_paths)
            if image is None:
                fire(
                    "neural_part_error",
                    part=part.name,
                    error="no reference image found — set the part's image_crop or pass reference images",
                )
                continue

            target = [float(v) for v in (part.target_size or part.dimensions)]
            fire("neural_part_started", part=part.name, image=str(image), target_size_m=target)
            result = provider.generate_mesh_from_image(image, neural_dir, target)
            if result.success and result.output_glb_path and Path(result.output_glb_path).exists():
                neural_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(result.output_glb_path, cache_path)
                part.mesh_path = str(cache_path)
                if not part.target_size:
                    part.target_size = target
                fire(
                    "neural_part_done",
                    part=part.name,
                    glb=str(cache_path),
                    tri_count=result.tri_count,
                    duration_s=round(result.duration_sec, 2),
                )
            else:
                fire("neural_part_error", part=part.name, error=(result.error or "generation failed")[:500])

    def _reattach_neural_meshes(self, spec: ObjectSpec, run_dir: Path) -> None:
        """Cache-only pass run at the top of every iteration: the corrector
        rewrites the whole spec JSON and may drop mesh_path — repopulate it
        from run_dir/neural/ without hitting the service again."""
        neural_parts = [p for p in spec.parts if p.method == GenerationMethod.IMAGE_TO_3D]
        if not neural_parts:
            return
        neural_dir = run_dir / "neural"
        for part in neural_parts:
            if part.mesh_path and Path(part.mesh_path).exists():
                continue
            cache_path = neural_dir / f"{_safe_filename(part.name)}.glb"
            if cache_path.exists():
                part.mesh_path = str(cache_path)
                if not part.target_size:
                    part.target_size = [float(d) for d in part.dimensions]

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _correct_spec(self, current_spec: ObjectSpec, instruction: str) -> ObjectSpec | None:
        """Ask the corrector for a fixed spec. Returns a validated spec or None."""
        user_prompt = (
            f"{instruction}\n\n"
            f"Current ObjectSpec:\n{current_spec.model_dump_json(indent=2)}\n\n"
            "Return the complete corrected ObjectSpec JSON and nothing else."
        )
        _, parsed = self.provider.complete_json(
            system_prompt=CORRECTOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            role="corrector",
        )
        if not parsed:
            return None
        try:
            return ObjectSpec.model_validate(parsed)
        except Exception:
            return None

    def _analyst_spec(self, prompt: str, measurements_text: str, images: list[Path]) -> tuple[ObjectSpec | None, str]:
        """Ask the analyst for an initial ObjectSpec. Sends reference images as
        vision content when the endpoint supports it."""
        user_text = f"User Request:\n{prompt}\n"
        if measurements_text:
            user_text += f"\nExact Measurements Required:\n{measurements_text}\n"
        if images:
            paths = ", ".join(str(p) for p in images)
            user_text += (
                f"\nReference images (file paths, in order): {paths}\n"
                "Analyze their structure, proportions, and part decomposition.\n"
                "For any image_to_3d part, set image_crop to one of these exact paths.\n"
            )

        use_vision = bool(images) and self.provider.supports_vision()
        if use_vision:
            user_message = ChatMessage(role="user", content=vision_user_content(user_text, images))
        else:
            if images:
                user_text += (
                    "\nNOTE: vision is unavailable — rely on the measurements and the "
                    "user's description; state assumptions in the spec description.\n"
                )
            user_message = ChatMessage(role="user", content=user_text)

        res = self.provider.chat(
            messages=[ChatMessage(role="system", content=ANALYST_SYSTEM_PROMPT), user_message],
            role="analyst",
        )
        parsed = extract_json_from_text(res.content)
        if not parsed:
            if not res.content and res.finish_reason == "length":
                return None, (
                    f"The model exhausted its {res.max_tokens}-token completion budget on reasoning "
                    "before emitting any content (finish_reason=length). Raise roles.analyst.max_tokens "
                    f"in config/ai.yaml. Raw response had {res.completion_tokens} completion tokens.\n"
                    + (res.raw_response.get("choices") or [{}])[0].get("message", {}).get("reasoning", "")[-1500:]
                )
            return None, res.content
        try:
            return ObjectSpec.model_validate(parsed), res.content
        except Exception as e:
            # One corrector round: the model produced JSON that misses the
            # schema — feed the validation errors back before giving up.
            _, fixed = self.provider.complete_json(
                system_prompt=CORRECTOR_SYSTEM_PROMPT,
                user_prompt=(
                    f"The ObjectSpec failed schema validation:\n{e}\n\n"
                    f"Current ObjectSpec JSON:\n{json.dumps(parsed, indent=2)}\n\n"
                    "Fix every invalid field and return the complete corrected ObjectSpec JSON."
                ),
                role="corrector",
            )
            if fixed:
                try:
                    return ObjectSpec.model_validate(fixed), res.content
                except Exception:
                    pass
            return None, f"schema validation failed: {e}\n{res.content}"

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(
        self,
        prompt: str,
        measurements_text: str = "",
        images: list[str | Path] | None = None,
        spec_override: ObjectSpec | None = None,
        run_name: str = "build",
        run_dir: str | Path | None = None,
        progress=None,
        cancel=None,
    ) -> AgentRunResult:
        """Execute the full build-measure-verify loop.

        progress: optional callable(dict) receiving live stage events
          ({"event": "analyst_done", ...}) — used by the web UI.
        cancel: optional callable() -> bool, checked between stages.
        run_dir: reuse an existing run directory instead of creating one."""
        started = time.time()
        if run_dir is not None:
            run_dir = Path(run_dir)
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "renders").mkdir(exist_ok=True)
            (run_dir / "steps").mkdir(exist_ok=True)
        else:
            run_dir = self.run_store.create_run(name_prefix=run_name)
        run_id = run_dir.name

        def emit(event: str, **data) -> None:
            if progress is not None:
                try:
                    progress({"event": event, "run_id": run_id, "ts": time.time(), **data})
                except Exception:
                    pass

        def cancelled() -> bool:
            return bool(cancel) and cancel()

        renders_dir = run_dir / "renders"
        steps_dir = run_dir / "steps"
        final_glb_path = run_dir / "final.glb"
        image_paths = [Path(i) for i in (images or [])]

        emit(
            "run_started",
            mode="spec" if spec_override else "ai",
            run_dir=str(run_dir),
            prompt=prompt,
            measurements=measurements_text,
            images=[str(p) for p in image_paths],
        )

        # Step 1: obtain the initial ObjectSpec.
        if spec_override:
            current_spec = spec_override
            emit("analyst_done", source="user_spec", spec=json.loads(current_spec.model_dump_json()))
        else:
            emit("analyst_started")
            spec_obj, raw = self._analyst_spec(prompt, measurements_text, image_paths)
            if spec_obj is None:
                emit("analyst_error", error=raw[:2000])
                return self._finish(
                    run_dir=run_dir,
                    spec=ObjectSpec(name=prompt[:40]),
                    final_glb_path=None,
                    verification=None,
                    iterations=0,
                    renders={},
                    error=f"Analyst failed to produce a valid ObjectSpec:\n{raw[:800]}",
                    run_name=run_name,
                    started=started,
                    emit=emit,
                    user_cancelled=cancelled(),
                )
            current_spec = spec_obj
            emit(
                "analyst_done",
                source="analyst",
                spec=json.loads(current_spec.model_dump_json()),
            )

        # Neural parts: generate meshes via the local img3d service before the
        # build loop (parametric parts need nothing here — zero overhead).
        self._normalize_spec_methods(current_spec)
        self._prepare_neural_parts(current_spec, run_dir, image_paths, emit, cancelled)

        # Step 2: iterative build-measure-verify loop.
        iteration = 0
        latest_verification: VerificationReport | None = None
        rendered_views: dict[str, str] = {}
        last_error: str | None = None
        budget_exhausted = False

        while iteration < self.max_iterations:
            if cancelled():
                last_error = "Cancelled by user"
                break
            if time.time() - started > self.wall_clock_budget_s:
                budget_exhausted = True
                last_error = f"Wall-clock budget ({self.wall_clock_budget_s}s) exhausted"
                break
            iteration += 1
            step_glb = steps_dir / f"step_{iteration}.glb"
            emit("iteration_started", index=iteration)

            struct_errors = validate_spec_structure(current_spec)
            if struct_errors:
                emit("correction_started", reason="structural validation", feedback="\\n".join(struct_errors)[:1500])
                fixed = self._correct_spec(
                    current_spec,
                    "The ObjectSpec has structural validation errors:\\n- " + "\\n- ".join(struct_errors),
                )
                if fixed:
                    current_spec = fixed
                    emit("correction_done", fixed=True)

            # The corrector rewrites the whole spec; restore neural mesh_path
            # from this run's cache before building.
            self._normalize_spec_methods(current_spec)
            self._reattach_neural_meshes(current_spec, run_dir)

            build_params = resolve_spec_to_build_params(current_spec, output_glb_path=str(step_glb.resolve()))
            emit("build_started", index=iteration, parts=[p.name for p in current_spec.parts])
            try:
                self.runner.execute_op("build_from_spec", build_params)
            except Exception as e:
                last_error = str(e)
                emit("build_error", index=iteration, error=last_error[:2000])
                if cancelled():
                    break
                fixed = self._correct_spec(
                    current_spec,
                    f"Blender build failed with this error:\\n{last_error[:1500]}",
                )
                if fixed:
                    current_spec = fixed
                    emit("correction_done", fixed=True, after="build_error")
                    continue
                break
            emit("build_done", index=iteration, step_glb=str(step_glb))

            if not step_glb.exists():
                last_error = "Build reported success but the step GLB was not written"
                break

            # Measure and render MUST point at the exported file: each op runs
            # in a fresh Blender process with no shared scene state.
            step_path = str(step_glb.resolve())
            try:
                measure_res = self.runner.execute_op("measure", {"model_path": step_path})
            except Exception as e:
                last_error = f"Measure failed: {e}"
                break
            emit(
                "measure_done",
                index=iteration,
                overall=measure_res.get("overall", {}),
                parts=measure_res.get("parts", {}),
            )

            try:
                render_res = self.runner.execute_op(
                    "render_views",
                    {
                        "model_path": step_path,
                        "views": ["front", "side", "top", "iso"],
                        "output_dir": str(renders_dir.resolve()),
                        "prefix": f"step_{iteration}",
                    },
                )
                rendered_views = render_res.get("views", rendered_views)
                emit("render_done", index=iteration, views=rendered_views)
            except Exception as e:
                # Render failure is non-fatal — gates still run on geometry.
                rendered_views = dict(rendered_views)

            latest_verification = self.verifier.verify_run(
                spec=current_spec,
                measurement_data=measure_res,
                glb_path=step_glb,
            )
            emit(
                "verification",
                index=iteration,
                passed=latest_verification.passed,
                dimension_gate=asdict(latest_verification.dimension_gate),
                feedback=latest_verification.feedback_for_agent,
                mesh={
                    "passed": latest_verification.mesh_gate.passed,
                    "is_watertight": latest_verification.mesh_gate.is_watertight,
                    "faces_count": latest_verification.mesh_gate.faces_count,
                    "vertices_count": latest_verification.mesh_gate.vertices_count,
                    "bounding_box_m": latest_verification.mesh_gate.bounding_box_m,
                    "warnings": latest_verification.mesh_gate.warnings,
                },
            )

            if latest_verification.passed:
                shutil.copy2(step_glb, final_glb_path)
                break

            if cancelled():
                last_error = "Cancelled by user"
                break
            emit("correction_started", reason="gate failures", feedback=latest_verification.feedback_for_agent[:1500])
            fixed = self._correct_spec(
                current_spec,
                f"Verification results for step {iteration}:\\n{latest_verification.feedback_for_agent}",
            )
            if fixed:
                current_spec = fixed
                emit("correction_done", fixed=True)
            else:
                last_error = latest_verification.feedback_for_agent
                break

        # Keep the last good artifact even when gates did not pass.
        if not final_glb_path.exists():
            for i in range(iteration, 0, -1):
                candidate = steps_dir / f"step_{i}.glb"
                if candidate.exists():
                    shutil.copy2(candidate, final_glb_path)
                    break

        return self._finish(
            run_dir=run_dir,
            spec=current_spec,
            final_glb_path=final_glb_path if final_glb_path.exists() else None,
            verification=latest_verification,
            iterations=iteration,
            renders=rendered_views,
            error=last_error if (latest_verification is None or not latest_verification.passed) else None,
            run_name=run_name,
            started=started,
            budget_exhausted=budget_exhausted,
            emit=emit,
            user_cancelled=cancelled(),
        )

    def _finish(
        self,
        run_dir: Path,
        spec: ObjectSpec,
        final_glb_path: Path | None,
        verification: VerificationReport | None,
        iterations: int,
        renders: dict[str, str],
        error: str | None,
        run_name: str,
        started: float,
        budget_exhausted: bool = False,
        emit=None,
        user_cancelled: bool = False,
    ) -> AgentRunResult:
        passed = bool(verification and verification.passed)
        self.run_store.save_spec(run_dir, spec)

        status = "completed" if passed else "completed_with_warnings"
        if budget_exhausted and not passed:
            status = "budget_exhausted"
        if user_cancelled and not passed:
            status = "cancelled"

        manifest = RunManifest(
            run_id=run_dir.name,
            created_at=started,
            model_name=spec.name,
            spec_path=str((run_dir / "spec.json").resolve()),
            final_glb_path=str(final_glb_path.resolve()) if final_glb_path else None,
            renders=renders,
            dimension_gate_passed=bool(verification and verification.dimension_gate.passed),
            mesh_gate_passed=bool(verification and verification.mesh_gate.passed),
            tri_count=verification.mesh_gate.faces_count if verification else 0,
            vertex_count=verification.mesh_gate.vertices_count if verification else 0,
            dimensions_m=verification.mesh_gate.bounding_box_m if verification else [0, 0, 0],
            metrics={
                "iterations": iterations,
                "wall_clock_s": round(time.time() - started, 1),
                "dimension_details": verification.dimension_gate.details if verification else [],
                "mesh_warnings": verification.mesh_gate.warnings if verification else [],
                "unresolved_error": error,
            },
            status=status,
        )
        self.run_store.save_manifest(run_dir, manifest)

        if emit is not None:
            try:
                emit(
                    "run_finished",
                    success=passed,
                    status=status,
                    model_name=spec.name,
                    iterations=iterations,
                    final_glb=str(final_glb_path.resolve()) if final_glb_path else None,
                    renders=renders,
                    dimensions_m=manifest.dimensions_m,
                    tri_count=manifest.tri_count,
                    error=error,
                    wall_clock_s=manifest.metrics["wall_clock_s"],
                )
            except Exception:
                pass

        return AgentRunResult(
            success=passed,
            spec=spec,
            final_glb_path=str(final_glb_path.resolve()) if final_glb_path else None,
            run_dir=run_dir,
            manifest_path=run_dir / "manifest.json",
            verification=verification,
            iterations=iterations,
            renders=renders,
            error=error,
        )
