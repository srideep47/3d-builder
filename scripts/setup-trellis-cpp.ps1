# scripts/setup-trellis-cpp.ps1 — install the TRELLIS.2 backend for the img3d
# service (Phase 8.5 R3: neural image-to-3D behind retopology).
#
# TRELLIS.2 (microsoft/TRELLIS.2, MIT) has no official Windows package; this
# installs the MIT-licensed trellis.cpp port instead: a prebuilt server
# binary plus the TRELLIS.2-4B weights as GGUF. Everything lands under the
# gitignored models\trellis\ — neither Python venv is touched.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/setup-trellis-cpp.ps1             # q8 weights (~9.5 GB, near-lossless)
#   powershell -ExecutionPolicy Bypass -File scripts/setup-trellis-cpp.ps1 -Quant f16 # reference bf16 (~16.5 GB)
#   powershell -ExecutionPolicy Bypass -File scripts/setup-trellis-cpp.ps1 -Quant q4  # smallest (~6 GB)
#   ... -Force    # re-download even if files are already present (repairs a partial download)
#
# After this: scripts\start-img3d.ps1 trellis  (the backend spawns the server
# itself on 127.0.0.1:8712 and terminates it at exit).
param(
    [ValidateSet("f16", "q8", "q4")]
    [string]$Quant = "q8",
    [string]$Version = "v0.6.0",
    # cuda = CUDA 13.1 build, Turing+ (RTX 4080 Super is sm 89 — use this);
    # cuda12 = legacy CUDA 12.9 build for Pascal/Volta; vulkan/rocm for other vendors.
    [ValidateSet("cuda", "cuda12", "vulkan", "rocm")]
    [string]$Backend = "cuda",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TrellisDir = Join-Path $RootDir "models\trellis"
$BinDir = Join-Path $TrellisDir "bin"

$GgufFiles = @(
    "birefnet.gguf", "dinov3.gguf", "ss_flow.gguf", "ss_dec.gguf",
    "shape_flow_512.gguf", "shape_flow_1024.gguf", "shape_dec.gguf",
    "tex_flow_512.gguf", "tex_flow_1024.gguf", "tex_dec.gguf"
)
# Tier -> path inside huggingface.co/ilintar/trellis2-gguf (f16 = repo root)
$Prefix = @{ "f16" = ""; "q8" = "q8/"; "q4" = "q4/" }[$Quant]

function Save-File([string]$Url, [string]$Dest) {
    if (-not $Force -and (Test-Path $Dest) -and ((Get-Item $Dest).Length -gt 0)) {
        Write-Host "    already present: $(Split-Path -Leaf $Dest)" -ForegroundColor DarkGray
        return
    }
    # Atomic download: curl writes to <name>.part and the file only gets its
    # final name after curl exits 0 — a connection reset mid-body can never
    # leave a partial that the skip-if-present check above would later trust
    # (the v0.6.0 first run hit exactly that: curl 56 on ss_dec.gguf left
    # 107 MB of 140.5 MB under the final name). Plain --retry does NOT cover
    # exit 56; --retry-all-errors does.
    $Part = "$Dest.part"
    & curl.exe -L --fail --retry 5 --retry-all-errors --retry-delay 2 -o $Part $Url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Part) -or (Get-Item $Part).Length -eq 0) {
        if (Test-Path $Part) { Remove-Item $Part }
        Write-Error "download failed: $Url"
    }
    Move-Item -Force $Part $Dest
}

# 1) server binary (trellis-server.exe sits at the zip root)
$ServerExe = Join-Path $BinDir "trellis-server.exe"
if (-not $Force -and (Test-Path $ServerExe)) {
    Write-Host "==> trellis-server already installed at $ServerExe" -ForegroundColor Green
} else {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    $ZipPath = Join-Path $BinDir "trellis-$Backend-windows-x64.zip"
    Write-Host "==> Downloading trellis.cpp $Version ($Backend build, ~700 MB)..." -ForegroundColor Cyan
    Save-File "https://github.com/pwilkin/trellis.cpp/releases/download/$Version/trellis-$Backend-windows-x64.zip" $ZipPath
    Write-Host "==> Extracting..."
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath
    if (-not (Test-Path $ServerExe)) {
        Write-Error "trellis-server.exe not found after extract - unexpected zip layout"
    }
}

# 2) GGUF weights (tier flattened into models\trellis\, which is what
#    trellis-server's --models flag points at)
New-Item -ItemType Directory -Path $TrellisDir -Force | Out-Null
Write-Host "==> Downloading TRELLIS.2-4B GGUF weights (tier: $Quant)..." -ForegroundColor Cyan
foreach ($f in $GgufFiles) {
    Save-File "https://huggingface.co/ilintar/trellis2-gguf/resolve/main/$Prefix$f" (Join-Path $TrellisDir $f)
}

# 3) verify the set the backend probes for (providers/trellis.py REQUIRED_GGUF)
$missing = @($GgufFiles | Where-Object { -not (Test-Path (Join-Path $TrellisDir $_)) })
if ($missing.Count -gt 0) {
    Write-Error "incomplete GGUF set (missing: $($missing -join ', '))"
}

Write-Host ""
Write-Host "==> TRELLIS.2 backend ready." -ForegroundColor Green
Write-Host "    weights   : $TrellisDir ($Quant tier, $($GgufFiles.Count) files)"
Write-Host "    server    : $ServerExe"
Write-Host "    server log: $TrellisDir\server.log (appended by the backend's spawn)"
Write-Host "    start     : scripts\start-img3d.ps1 trellis"
Write-Host "    resolution: 512 by default (env IMG3D_TRELLIS_RES; the 1024 cascade"
Write-Host "                also fits the 16 GB card per the port's README)"
