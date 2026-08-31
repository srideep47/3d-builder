"""Agent tool definitions and execution registry.

Used by native tool-calling loops. Every Blender op runs in its own process, so
stateful tools (measure/render) track the last built GLB and pass its path —
there is no shared scene between ops.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..blender.runner import BlenderRunner
from ..spec.resolver import resolve_spec_to_build_params
from ..spec.schema import ObjectSpec


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
            "name": "execute_blender_script",
            "description": "Execute arbitrary custom Python code inside Blender for specialized geometric modifications. Set the variable RESULT to a JSON-serializable value to return data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code using the 'bpy' API.",
                    }
                },
                "required": ["code"],
            },
        },
    },
]


class AgentToolExecutor:
    def __init__(self, runner: BlenderRunner, workdir: str | Path | None = None):
        self.runner = runner
        self.workdir = Path(workdir) if workdir else Path("output") / "agent_tool"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.last_built_glb: str | None = None

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

        if tool_name == "execute_blender_script":
            code = args.get("code", "")
            params: dict[str, Any] = {"code": code}
            if self.last_built_glb:
                params["input"] = self.last_built_glb
            return self.runner.execute_op("run_script", params)

        return {"success": False, "error": f"Unknown tool '{tool_name}'"}
