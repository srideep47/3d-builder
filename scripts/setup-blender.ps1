# scripts/setup-blender.ps1 — Ensures Blender 4.x is ready for 3D Builder
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$ToolsDir = Join-Path $RootDir "tools\blender"
$BlenderExe = Join-Path $ToolsDir "blender.exe"

Write-Host "==> Checking for existing Blender installation..." -ForegroundColor Cyan

# Check if locate.py finds an installed Blender
$Found = & python -c "from src.blender.locate import locate_blender; b = locate_blender(); print(b.executable if b else '')" 2>$null
if ($Found -and (Test-Path $Found)) {
    Write-Host "Found Blender at: $Found" -ForegroundColor Green
    & $Found --version
    exit 0
}

Write-Host "No system Blender 3.3+ detected. Preparing standalone portable Blender..." -ForegroundColor Yellow
if (-not (Test-Path $ToolsDir)) {
    New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
}

$ZipUrl = "https://download.blender.org/release/Blender4.2/blender-4.2.3-windows-x64.zip"
$ZipPath = Join-Path $RootDir "tools\blender.zip"

if (-not (Test-Path $BlenderExe)) {
    Write-Host "Downloading portable Blender 4.2 LTS from $ZipUrl..." -ForegroundColor Cyan
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing

    Write-Host "Extracting portable Blender..." -ForegroundColor Cyan
    Expand-Archive -Path $ZipPath -DestinationPath (Join-Path $RootDir "tools\tmp_blender") -Force

    $ExtractedSubdir = Get-ChildItem -Path (Join-Path $RootDir "tools\tmp_blender") -Directory | Select-Object -First 1
    if ($ExtractedSubdir) {
        Move-Item -Path "$($ExtractedSubdir.FullName)\*" -Destination $ToolsDir -Force
        Remove-Item -Path (Join-Path $RootDir "tools\tmp_blender") -Recurse -Force
    }
    if (Test-Path $ZipPath) {
        Remove-Item -Path $ZipPath -Force
    }
}

if (Test-Path $BlenderExe) {
    Write-Host "Portable Blender installed successfully at $BlenderExe" -ForegroundColor Green
    & $BlenderExe --version
} else {
    Write-Error "Failed to configure Blender."
    exit 1
}
