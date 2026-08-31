"""Pipeline — master orchestration for 3D generation, measurement, and rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent.loop import AgentLoop, AgentRunResult
from .agent.verifier import Verifier
from .ai.aptos import AptosGLMProvider
from .blender.runner import BlenderRunner
from .run_store import RunStore
from .spec.schema import ObjectSpec


class ThreeDBuilderPipeline:
    def __init__(
        self,
        provider: AptosGLMProvider | None = None,
        runner: BlenderRunner | None = None,
        run_store: RunStore | None = None,
    ):
        self.runner = runner or BlenderRunner()
        self.provider = provider or AptosGLMProvider()
        self.run_store = run_store or RunStore()
        self.verifier = Verifier()
        self.loop = AgentLoop(
            provider=self.provider,
            runner=self.runner,
            verifier=self.verifier,
            run_store=self.run_store,
        )

    def generate_from_prompt(
        self,
        prompt: str,
        measurements: str = "",
        material_preset: str | None = None,
        images: list[str | Path] | None = None,
        run_name: str = "ai_build",
        run_dir: str | Path | None = None,
        progress=None,
        cancel=None,
    ) -> AgentRunResult:
        """Generate a fully verified 3D model from prompt, measurements, and
        optional reference images using GLM-5.3."""
        enhanced_prompt = prompt
        if material_preset:
            enhanced_prompt += f"\nPrimary Material Preset: {material_preset}"

        return self.loop.run(
            prompt=enhanced_prompt,
            measurements_text=measurements,
            images=images,
            run_name=run_name,
            run_dir=run_dir,
            progress=progress,
            cancel=cancel,
        )

    def generate_from_spec(
        self,
        spec_source: str | Path | dict[str, Any] | ObjectSpec,
        run_name: str = "spec_build",
        run_dir: str | Path | None = None,
        progress=None,
        cancel=None,
    ) -> AgentRunResult:
        """Deterministically build and verify a 3D model from an ObjectSpec."""
        if isinstance(spec_source, ObjectSpec):
            spec_obj = spec_source
        elif isinstance(spec_source, dict):
            spec_obj = ObjectSpec.model_validate(spec_source)
        else:
            p = Path(spec_source)
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            spec_obj = ObjectSpec.model_validate(data)

        return self.loop.run(
            prompt=f"Build from spec: {spec_obj.name}",
            spec_override=spec_obj,
            run_name=run_name,
            run_dir=run_dir,
            progress=progress,
            cancel=cancel,
        )

    def measure_file(self, file_path: str | Path) -> dict[str, Any]:
        """Measure precise dimensions of an existing 3D file."""
        p = str(Path(file_path).resolve())
        return self.runner.execute_op("measure", {"model_path": p})

    def render_file(
        self,
        file_path: str | Path,
        output_dir: str | Path | None = None,
        views: list[str] | None = None,
        resolution: list[int] | None = None,
    ) -> dict[str, Any]:
        """Render studio preview views of a 3D file."""
        p = str(Path(file_path).resolve())
        out_d = str(Path(output_dir).resolve()) if output_dir else str(Path(file_path).parent / "renders")
        params = {
            "model_path": p,
            "output_dir": out_d,
            "views": views or ["front", "side", "top", "iso"],
            "resolution": resolution or [1024, 1024],
        }
        return self.runner.execute_op("render_views", params)
