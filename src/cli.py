"""3D Builder Typer CLI application."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .materials.pbr import list_material_presets
from .pipeline import ThreeDBuilderPipeline
from .run_store import RunStore

app = typer.Typer(
    name="3dbuilder",
    help="3D Builder — AI 3D Model Generation from Reference Images + Measurements",
    add_completion=False,
)
runs_app = typer.Typer(help="Manage and inspect generation runs")
app.add_typer(runs_app, name="runs")

console = Console()


@app.command()
def build(
    prompt: Optional[str] = typer.Option(None, "--prompt", "-p", help="Description of the 3D model to create"),
    measurements: Optional[str] = typer.Option(None, "--measurements", "-m", help="Physical dimensions (e.g. 'width 1.2m, height 0.8m')"),
    spec: Optional[str] = typer.Option(None, "--spec", "-s", help="Path to ObjectSpec JSON file"),
    image: Optional[list[str]] = typer.Option(None, "--image", "-i", help="Reference image path(s) analyzed by the AI analyst"),
    material: Optional[str] = typer.Option(None, "--material", "-mat", help="PBR Material preset (e.g. 'oak_wood', 'brushed_steel')"),
    name: str = typer.Option("build", "--name", "-n", help="Run name prefix"),
):
    """Build a 3D model using AI agent or deterministic CAD spec."""
    if not prompt and not spec:
        console.print("[bold red]Error:[/] You must provide either --prompt or --spec.", style="red")
        raise typer.Exit(1)

    image_paths: list[str] = []
    for img in image or []:
        p = Path(img)
        if not p.exists():
            console.print(f"[bold red]Error: Image not found:[/] {img}")
            raise typer.Exit(1)
        image_paths.append(str(p.resolve()))

    pipeline = ThreeDBuilderPipeline()

    if not pipeline.runner.is_available:
        console.print(
            Panel(
                "[bold yellow]Warning: Blender 3.3+ was not found.[/]\n"
                "Please configure THREED_BLENDER / BLENDER_PATH or run `powershell scripts/setup-blender.ps1`.",
                title="Blender Not Found",
                border_style="yellow",
            )
        )
        raise typer.Exit(1)

    with console.status("[bold green]Generating 3D model and enforcing quality gates...[/]"):
        if spec:
            console.print(f"[bold cyan]Building from spec:[/] {spec}")
            result = pipeline.generate_from_spec(spec, run_name=name)
        else:
            console.print(f"[bold cyan]Building from prompt:[/] '{prompt}'")
            if image_paths:
                console.print(f"[bold cyan]Reference images:[/] {len(image_paths)}")
            if measurements:
                console.print(f"[bold cyan]Measurements:[/] '{measurements}'")
            result = pipeline.generate_from_prompt(
                prompt=prompt,
                measurements=measurements or "",
                material_preset=material,
                images=image_paths,
                run_name=name,
            )

    if result.success:
        console.print(
            Panel(
                f"[bold green]✓ 3D Model Generation Succeeded![/]\n\n"
                f"[bold]Model Name:[/] {result.spec.name}\n"
                f"[bold]Final Asset:[/] [cyan]{result.final_glb_path}[/]\n"
                f"[bold]Run Directory:[/] [cyan]{result.run_dir}[/]\n"
                f"[bold]Iterations:[/] {result.iterations}\n"
                f"[bold]Triangles:[/] {result.verification.mesh_gate.faces_count if result.verification else 'N/A'}\n"
                f"[bold]Dimensions (m):[/] {result.verification.mesh_gate.bounding_box_m if result.verification else 'N/A'}",
                title="Generation Complete",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                f"[bold red]✗ Build Completed with Warnings/Failures[/]\n\n"
                f"[bold]Error/Feedback:[/] {result.error}\n"
                f"[bold]Artifacts saved at:[/] {result.run_dir}",
                title="Build Warnings",
                border_style="yellow",
            )
        )


@app.command()
def measure(
    file_path: str = typer.Argument(..., help="Path to 3D model file (GLB, OBJ, FBX)"),
):
    """Measure the precise metric dimensions of any 3D asset."""
    p = Path(file_path)
    if not p.exists():
        console.print(f"[bold red]Error: File not found:[/] {file_path}")
        raise typer.Exit(1)

    pipeline = ThreeDBuilderPipeline()
    with console.status("[bold cyan]Measuring geometry in Blender...[/]"):
        res = pipeline.measure_file(p)

    if not res.get("success"):
        console.print(f"[bold red]Measurement failed:[/] {res.get('error')}")
        raise typer.Exit(1)

    overall = res.get("overall", {})
    dims = overall.get("dimensions", [0, 0, 0])
    center = overall.get("center", [0, 0, 0])

    table = Table(title=f"Measurements for {p.name}")
    table.add_column("Property", style="cyan", justify="left")
    table.add_column("Value (Meters)", style="green", justify="right")
    table.add_column("Value (cm)", style="yellow", justify="right")
    table.add_column("Value (mm)", style="magenta", justify="right")

    labels = ["Width (X)", "Depth (Y)", "Height (Z)"]
    for i, label in enumerate(labels):
        val_m = dims[i] if i < len(dims) else 0.0
        table.add_row(label, f"{val_m:.4f} m", f"{val_m*100:.2f} cm", f"{val_m*1000:.1f} mm")

    console.print(table)
    console.print(f"[dim]Total Vertices: {res.get('total_vertices')}, Total Faces: {res.get('total_faces')}[/]")


@app.command()
def render(
    file_path: str = typer.Argument(..., help="Path to 3D model file"),
    output_dir: Optional[str] = typer.Option(None, "--out", "-o", help="Output directory for render PNGs"),
    views: str = typer.Option("front,side,top,iso", "--views", "-v", help="Comma-separated view angles"),
    resolution: int = typer.Option(1024, "--res", "-r", help="Resolution width & height in pixels"),
):
    """Render neutral studio preview images of a 3D model."""
    p = Path(file_path)
    if not p.exists():
        console.print(f"[bold red]Error: File not found:[/] {file_path}")
        raise typer.Exit(1)

    view_list = [v.strip() for v in views.split(",") if v.strip()]
    pipeline = ThreeDBuilderPipeline()
    with console.status("[bold cyan]Rendering studio camera views...[/]"):
        res = pipeline.render_file(
            file_path=p,
            output_dir=output_dir,
            views=view_list,
            resolution=[resolution, resolution],
        )

    if res.get("success"):
        console.print("[bold green]✓ Renders generated successfully:[/]")
        for v, file_out in res.get("views", {}).items():
            console.print(f" - [bold cyan]{v}:[/] {file_out}")
    else:
        console.print(f"[bold red]Render failed:[/] {res.get('error')}")


@app.command()
def presets():
    """List all available PBR material presets."""
    mat_presets = list_material_presets()
    table = Table(title="3D Builder PBR Material Presets")
    table.add_column("Preset Name", style="cyan")
    table.add_column("Category", style="yellow")
    table.add_column("Roughness", justify="right")
    table.add_column("Metallic", justify="right")
    table.add_column("Description", style="dim")

    for p in mat_presets:
        table.add_row(
            p["name"],
            p["category"],
            f"{p['roughness']:.2f}",
            f"{p['metallic']:.2f}",
            p["description"],
        )
    console.print(table)


@app.command()
def img3d(
    image_path: str = typer.Argument(..., help="Path to the reference image (PNG/JPG)"),
    target: str = typer.Option(
        None, "--target", "-t",
        help="Target size in meters as X,Y,Z (e.g. 0.4,0.3,0.15). The mesh is scaled to these exact bounds.",
    ),
    output_dir: Optional[str] = typer.Option(None, "--out", "-o", help="Output directory for the GLB"),
):
    """Generate a 3D mesh (GLB) from a single reference image via the local neural img3d service."""
    from .img3d import get_img3d_provider

    p = Path(image_path)
    if not p.exists():
        console.print(f"[bold red]Error: Image not found:[/] {image_path}")
        raise typer.Exit(1)

    target_size = None
    if target:
        try:
            target_size = [float(v) for v in target.split(",")]
            if len(target_size) != 3 or min(target_size) <= 0:
                raise ValueError
        except ValueError:
            console.print("[bold red]Error: --target must be three positive numbers, e.g. 0.4,0.3,0.15[/]")
            raise typer.Exit(1)

    provider = get_img3d_provider()
    if provider is None:
        console.print("[bold red]img3d is disabled.[/] Set img3d.enabled: true in config/hardware.yaml")
        raise typer.Exit(1)
    if not provider.is_available():
        console.print(f"[bold red]img3d service unreachable at {provider.base_url}[/]")
        console.print("[dim]Start it with: scripts/start-img3d.ps1 (or scripts/start-img3d.ps1 tripo_sr for the GPU backend)[/]")
        raise typer.Exit(1)

    out_dir = Path(output_dir) if output_dir else p.parent / "img3d_output"
    with console.status("[bold cyan]Generating mesh from image (neural service)...[/]"):
        result = provider.generate_mesh_from_image(p, out_dir, target_size)

    if result.success and result.output_glb_path:
        console.print("[bold green]✓ Neural mesh generated:[/]")
        console.print(f" - [bold cyan]GLB:[/] {result.output_glb_path}")
        console.print(f" - [bold cyan]Triangles:[/] {result.tri_count}")
        console.print(f" - [bold cyan]Duration:[/] {result.duration_sec:.1f}s")
    else:
        console.print(f"[bold red]img3d generation failed:[/] {result.error}")
        raise typer.Exit(1)


@app.command()
def health():
    """Check AI provider endpoint and Blender installation status."""
    pipeline = ThreeDBuilderPipeline()
    ai_health = pipeline.provider.health()
    blender_ok = pipeline.runner.is_available

    console.print(Panel(
        f"[bold]Blender Status:[/] {'[green]Installed & Ready[/]' if blender_ok else '[red]Not Found[/]'}\n"
        f"[bold]Blender Path:[/] {pipeline.runner.install.executable if blender_ok and pipeline.runner.install else 'None'}\n\n"
        f"[bold]AI Provider:[/] {ai_health.provider} ({ai_health.endpoint})\n"
        f"[bold]Model ID:[/] {ai_health.model}\n"
        f"[bold]Endpoint Reachable:[/] {'[green]Yes[/]' if ai_health.healthy else '[red]No[/]'}\n"
        f"[bold]Tool Calling:[/] {'[green]Supported[/]' if ai_health.tools_supported else '[red]No[/]'}\n"
        f"[bold]Vision Supported:[/] {'[green]Yes[/]' if ai_health.vision_supported else '[yellow]Text/Code Model (Vision routing active)[/]'}",
        title="3D Builder System Health",
        border_style="cyan" if (blender_ok and ai_health.healthy) else "yellow",
    ))


@app.command()
def validate(
    package_dir: str = typer.Argument(..., help="Package directory to validate, e.g. output/packages/<JOB>"),
    job: str = typer.Option(..., "--job", "-j", help="Path to the job card (job.yaml)"),
    json_out: bool = typer.Option(False, "--json", help="Print gate results as JSON instead of a table"),
):
    """Reproduce the client's validator panel against a package directory."""
    from .client.gates import MeshFacts, run_all_gates
    from .client.job import load_job

    try:
        job_card = load_job(Path(job))
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1)

    pkg = Path(package_dir)
    if not pkg.is_dir():
        console.print(f"[bold red]Error:[/] Package directory not found: {package_dir}")
        raise typer.Exit(1)

    # Mesh facts: one fresh Blender process pointed at the packaged FBX
    # (repo rule 1 — no shared scene state). Fail closed: without facts the
    # mesh gates report "could not verify" and the command exits non-zero;
    # we never learn about a failure from the client's validator.
    facts: MeshFacts | None = None
    fbx = pkg / f"{job_card.job_code}.fbx"
    if fbx.is_file():
        from .blender.runner import BlenderRunner

        runner = BlenderRunner()
        if runner.is_available:
            try:
                with console.status("[bold cyan]Measuring packaged FBX in Blender...[/]"):
                    report = runner.execute_op("topology_report", {"model_path": str(fbx)})
                facts = MeshFacts.from_topology_report(report)
            except Exception as e:  # noqa: BLE001 — report and fail closed
                console.print(f"[bold yellow]Warning:[/] topology measurement failed: {e}")
        else:
            console.print("[bold yellow]Warning:[/] Blender not found — mesh gates will fail as 'could not verify'.")
    else:
        console.print(f"[bold yellow]Warning:[/] {fbx.name} not found — mesh gates will fail as 'could not verify'.")

    results = run_all_gates(pkg, job_card, facts)

    if json_out:
        console.print_json(json.dumps({
            "job": job_card.job_code,
            "package": str(pkg),
            "gates": [r.to_dict() for r in results],
            "all_passed": all(r.passed for r in results),
        }))
    else:
        table = Table(title=f"Client Validator (local mirror) — {job_card.job_code}")
        table.add_column("Gate", style="cyan")
        table.add_column("Result")
        table.add_column("Expected", style="dim")
        table.add_column("Received", style="dim")
        for r in results:
            table.add_row(r.gate, "[green]PASS[/]" if r.passed else "[red]FAIL[/]",
                          r.expected, r.received)
        console.print(table)
        for r in results:
            if not r.passed:
                console.print(f"  [red]✗ {r.gate}:[/] {r.message}")
        failed = sum(1 for r in results if not r.passed)
        console.print(Panel(
            "[bold green]ALL GATES PASSED[/]" if not failed else f"[bold red]{failed} GATE(S) FAILED[/]",
            title="Validation Result",
            border_style="green" if not failed else "red",
        ))

    if any(not r.passed for r in results):
        raise typer.Exit(1)


@runs_app.command("list")
def runs_list():
    """List recent generation runs and manifests."""
    store = RunStore()
    runs = store.list_runs()
    if not runs:
        console.print("[dim]No past runs found in output/runs/[/]")
        return

    table = Table(title="Past Generation Runs")
    table.add_column("Run ID", style="cyan")
    table.add_column("Model Name", style="green")
    table.add_column("Status", style="magenta")
    table.add_column("Dim Gate", style="yellow")
    table.add_column("Mesh Gate", style="blue")

    for r in runs:
        table.add_row(
            r.get("run_id", "N/A"),
            r.get("model_name", "Untitled"),
            r.get("status", "unknown"),
            "✓ Pass" if r.get("dimension_gate_passed") else "✗ Fail",
            "✓ Pass" if r.get("mesh_gate_passed") else "✗ Fail",
        )
    console.print(table)


@app.command()
def mcp():
    """Launch the MCP stdio server for ZCode / IDE integration."""
    from .mcp_server import start_mcp_server
    start_mcp_server()


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address for the web UI server"),
    port: int = typer.Option(8137, "--port", help="Port for the web UI server"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the UI in the default browser"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload server on code changes (dev)"),
):
    """Launch the 3D Builder web UI (build, watch progress, inspect output)."""
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}"
    console.print(Panel(f"[bold green]3D Builder Web UI[/]\n[link={url}]{url}[/link]\n\n"
                        f"Serving [cyan]web/[/] frontend + REST API + WebSocket progress.",
                        title="Starting...", border_style="green"))
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("src.webapp.server:app", host=host, port=port, reload=reload, log_level="warning")


def main():
    app()


if __name__ == "__main__":
    main()
