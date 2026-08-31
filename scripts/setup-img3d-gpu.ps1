# scripts/setup-img3d-gpu.ps1 — vendor the TripoSR repo for the img3d service
# GPU backends (the `tsr` package is not on PyPI). Run once after installing
# services/img3d_service/requirements-gpu.txt into the service venv.
$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VendorDir = Join-Path $RootDir "services\img3d_service\vendor"
$RepoDir = Join-Path $VendorDir "TripoSR"

if (Test-Path (Join-Path $RepoDir "tsr")) {
    Write-Host "==> TripoSR already vendored at $RepoDir" -ForegroundColor Green
    exit 0
}

New-Item -ItemType Directory -Path $VendorDir -Force | Out-Null
Write-Host "==> Cloning TripoSR into $RepoDir..." -ForegroundColor Cyan
git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git $RepoDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "git clone failed"
}

Write-Host "==> TripoSR vendored. Weights download automatically from HuggingFace"
Write-Host "    (stabilityai/TripoSR) on first generation, cached under models\." -ForegroundColor Green
