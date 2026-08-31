"""MCP server for 3D Builder — exposes tool calling over stdio to ZCode and MCP clients."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .materials.pbr import list_material_presets
from .pipeline import ThreeDBuilderPipeline
from .run_store import RunStore
from .spec.schema import ObjectSpec

mcp = _Server("3D-Builder")
_pipeline = ThreeDBuilderPipeline()
_store = RunStore()


@mcp.tool()
def generate_3d_model(
    prompt: str,
    measurements: str = "",
    material_preset: str = "",
    image_paths: list[str] | None = None,
) -> str:
    """Generate an AI-driven, measurement-accurate 3D model (GLB) from natural
    language, dimensions, and optional reference images."""
    valid_images = [str(Path(p).resolve()) for p in (image_paths or []) if Path(p).exists()]
    res = _pipeline.generate_from_prompt(
        prompt=prompt,
        measurements=measurements,
        material_preset=material_preset or None,
        images=valid_images,
    )
    return json.dumps({
        "success": res.success,
        "model_name": res.spec.name,
        "final_glb_path": str(res.final_glb_path) if res.final_glb_path else None,
        "run_dir": str(res.run_dir),
        "iterations": res.iterations,
        "renders": res.renders,
        "error": res.error,
    }, indent=2)


@mcp.tool()
def build_from_spec_json(spec_json: str) -> str:
    """Deterministically construct and verify a 3D model in Blender from an ObjectSpec JSON string."""
    try:
        spec_dict = json.loads(spec_json)
        spec_obj = ObjectSpec.model_validate(spec_dict)
    except Exception as e:
        return json.dumps({"success": False, "error": f"Invalid ObjectSpec JSON: {e}"})

    res = _pipeline.generate_from_spec(spec_obj)
    return json.dumps({
        "success": res.success,
        "model_name": res.spec.name,
        "final_glb_path": str(res.final_glb_path) if res.final_glb_path else None,
        "run_dir": str(res.run_dir),
        "renders": res.renders,
        "error": res.error,
    }, indent=2)


@mcp.tool()
def measure_3d_file(file_path: str) -> str:
    """Measure the precise real-world metric bounding dimensions (X, Y, Z) and part sizes of any 3D asset."""
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"success": False, "error": f"File not found: {file_path}"})
    res = _pipeline.measure_file(p)
    return json.dumps(res, indent=2)


@mcp.tool()
def render_3d_views(file_path: str, views: list[str] | None = None) -> str:
    """Render studio camera views (front, side, top, iso) of any 3D model."""
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"success": False, "error": f"File not found: {file_path}"})
    res = _pipeline.render_file(p, views=views)
    return json.dumps(res, indent=2)


@mcp.tool()
def list_pbr_material_presets() -> str:
    """List all available realistic procedural and PBR material presets (wood, metal, leather, plastic, glass, etc.)."""
    return json.dumps(list_material_presets(), indent=2)


@mcp.tool()
def list_recent_runs() -> str:
    """List recent generation runs, verification outcomes, and output paths."""
    return json.dumps(_store.list_runs(), indent=2)


@mcp.tool()
def image_to_3d(
    image_path: str,
    target_size: list[float] | None = None,
    output_dir: str | None = None,
) -> str:
    """Generate a 3D mesh (GLB) from a single reference image via the local
    neural img3d service (TripoSR/TRELLIS class models). target_size is
    [x, y, z] in meters — the mesh is scaled to exactly those bounds."""
    from .img3d import get_img3d_provider

    p = Path(image_path)
    if not p.exists():
        return json.dumps({"success": False, "error": f"Image not found: {image_path}"})

    provider = get_img3d_provider()
    if provider is None:
        return json.dumps({"success": False, "error": "img3d disabled in config/hardware.yaml"})
    if not provider.is_available():
        return json.dumps({
            "success": False,
            "error": f"img3d service unreachable at {provider.base_url} — start it with scripts/start-img3d.ps1",
        })

    out_dir = Path(output_dir) if output_dir else p.parent / "img3d_output"
    result = provider.generate_mesh_from_image(p, out_dir, target_size)
    return json.dumps({
        "success": result.success,
        "glb_path": str(result.output_glb_path) if result.output_glb_path else None,
        "tri_count": result.tri_count,
        "duration_sec": round(result.duration_sec, 2),
        "error": result.error,
    }, indent=2)


@mcp.tool()
def check_system_health() -> str:
    """Check Blender installation, Aptos GLM-5.3 AI provider connectivity, and the img3d neural service."""
    ai_h = _pipeline.provider.health()
    blender_ok = _pipeline.runner.is_available
    img3d_status = "disabled"
    try:
        from .img3d import get_img3d_provider

        provider = get_img3d_provider()
        if provider is not None:
            img3d_status = "available" if provider.is_available() else "unreachable"
    except Exception:
        img3d_status = "error"
    return json.dumps({
        "blender_available": blender_ok,
        "blender_path": _pipeline.runner.install.executable if blender_ok and _pipeline.runner.install else None,
        "ai_healthy": ai_h.healthy,
        "ai_model": ai_h.model,
        "ai_endpoint": ai_h.endpoint,
        "tool_calling_supported": ai_h.tools_supported,
        "img3d_service": img3d_status,
    }, indent=2)


def start_mcp_server():
    """Start the MCP stdio server loop."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    start_mcp_server()
