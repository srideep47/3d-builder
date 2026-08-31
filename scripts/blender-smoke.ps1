# scripts/blender-smoke.ps1 — Smoke test for Blender automation harness
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir

Write-Host "==> Running Blender Harness Smoke Test..." -ForegroundColor Cyan
Set-Location $RootDir

uv run python -c "
from src.blender.runner import BlenderRunner
runner = BlenderRunner()
if not runner.is_available:
    print('Blender is not available on this machine yet.')
    exit(0)
res = runner.execute_op('info')
print('Blender Info:', res)

# Test box build & measure
spec = {
    'parts': [
        {'name': 'test_box', 'shape': 'box', 'dimensions': [1.0, 2.0, 0.5], 'position': [0, 0, 0]}
    ]
}
build_res = runner.execute_op('build_from_spec', {'spec': spec, 'output_path': 'output/temp/smoke_box.glb'})
print('Build Result:', build_res)

measure_res = runner.execute_op('measure')
print('Measure Result:', measure_res)
"
Write-Host "==> Smoke test finished." -ForegroundColor Green
