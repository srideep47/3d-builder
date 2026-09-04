# scripts/start-img3d.ps1 — start the local image-to-3D service (PLAN.md §9)
# Usage:
#   scripts/start-img3d.ps1                  # mock backend (main env, no GPU)
#   scripts/start-img3d.ps1 tripo_sr         # GPU backend (service venv)
#   scripts/start-img3d.ps1 trellis          # trellis.cpp server (service venv;
#                                            # run scripts/setup-trellis-cpp.ps1 first)
#   scripts/start-img3d.ps1 comfy_trellis2   # TRELLIS 2 via ComfyUI (service venv;
#                                            # spawns/adopts ComfyUI on 127.0.0.1:8189)
param(
    [string]$Model = "mock",
    [int]$Port = 8501,
    [string]$BindHost = "127.0.0.1"
)
$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ServiceDir = Join-Path $RootDir "services\img3d_service"

$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
if ($Model -ne "mock") {
    $GpuPython = Join-Path $ServiceDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $GpuPython)) {
        Write-Error "GPU backend '$Model' needs the service venv. Run the install steps in services/img3d_service/README.md first."
    }
    $Python = $GpuPython
}

if ($Model -eq "trellis") {
    $TrellisWeights = Join-Path $RootDir "models\trellis\ss_flow.gguf"
    if (-not (Test-Path $TrellisWeights)) {
        Write-Warning "TRELLIS weights not found under models\trellis\ - run scripts/setup-trellis-cpp.ps1 first"
    }
}

$env:IMG3D_MODEL = $Model
Write-Host "==> img3d service: model=$Model on http://${BindHost}:${Port}" -ForegroundColor Cyan
& $Python -m uvicorn app:app --app-dir $ServiceDir --host $BindHost --port $Port
