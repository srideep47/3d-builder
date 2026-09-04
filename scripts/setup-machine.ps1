# scripts/setup-machine.ps1 -- one-shot machine bring-up for 3D Builder
#
# Installs everything git does NOT carry:
#   1. Project Python deps (uv)
#   2. Blender            (delegates to scripts/setup-blender.ps1)
#   3. Composed textures  (gen_template_textures.py)
#   4. ComfyUI + its Python 3.11 venv + TRELLIS 2 wheels + custom nodes
#   5. TRELLIS 2 / DINOv3 model weights (~25.6 GB)
#
# Every version pin below was established by live measurement on a
# Windows 11 / RTX 4080 Super machine. Read docs/MACHINE_SETUP.md before
# changing any of them -- several are load-bearing and fail in non-obvious ways.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/setup-machine.ps1
#   ... -SkipModels        # everything except the 25.6 GB download
#   ... -SkipComfy         # core pipeline only, no neural path
#   ... -ComfyRoot "D:\AI\ComfyUI"
#   ... -VerifyOnly        # check an existing install, change nothing

[CmdletBinding()]
param(
    [string]$ComfyRoot = "D:\Work\AI_Tools\ComfyUI",
    [switch]$SkipModels,
    [switch]$SkipComfy,
    [switch]$SkipBlender,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

# -- Load-bearing pins. Do not change without reading the guide. --------------
$TORCH_VER       = "2.8.0"
$TORCHVISION_VER = "0.23.0"
$TORCHAUDIO_VER  = "2.8.0"       # MUST match torch or ComfyUI dies: WinError 127
$XFORMERS_VER    = "0.0.32.post2" # newer pulls torch>=2.10 and breaks the wheels
$CUDA_INDEX      = "https://download.pytorch.org/whl/cu128"
$WHEEL_DIR_NAME  = "Torch280"     # wheels/Windows/<this>
$PY_TAG          = "cp311"        # only cp311/312/313 ship; repo standard is 3.11

$CUSTOM_NODES = @(
    @{ name = "ComfyUI-Trellis2";       url = "https://github.com/visualbruno/ComfyUI-Trellis2" },
    @{ name = "ComfyUI-Easy-Use";       url = "https://github.com/yolain/ComfyUI-Easy-Use" },
    @{ name = "rgthree-comfy";          url = "https://github.com/rgthree/rgthree-comfy" }
)

$MODELS = @(
    @{ repo = "visualbruno/TRELLIS.2-4B-FP8";                dest = "visualbruno\TRELLIS.2-4B-FP8" },
    @{ repo = "visualbruno/dinov3-vitl16-pretrain-lvd1689m"; dest = "facebook\dinov3-vitl16-pretrain-lvd1689m" },
    @{ repo = "microsoft/TRELLIS.2-4B";                      dest = "microsoft\TRELLIS.2-4B" }
)

function Say($m)  { Write-Host "==> $m"  -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !!  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  XX  $m" -ForegroundColor Red; exit 1 }

# -- 0. Prerequisites ---------------------------------------------------------
Say "Checking prerequisites"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git not found on PATH." }
Ok "git"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Die "uv not found. Install: https://docs.astral.sh/uv/getting-started/installation/"
}
Ok "uv"

$py311 = $null
foreach ($c in @("C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe",
                 "C:\Python311\python.exe")) {
    if (Test-Path $c) { $py311 = $c; break }
}
if (-not $py311) {
    try { $probe = (& py -3.11 -c "import sys;print(sys.executable)" 2>$null); if ($probe) { $py311 = $probe.Trim() } } catch {}
}
if (-not $py311 -and -not $SkipComfy) {
    Die @"
Python 3.11 not found, and ComfyUI/TRELLIS 2 REQUIRES it.
The TRELLIS 2 wheels ship for cp311/cp312/cp313 only -- there is NO cp310 build.
Install 3.11 from https://www.python.org/downloads/release/python-3119/
then re-run. (Or pass -SkipComfy to set up the core pipeline only.)
"@
}
if ($py311) { Ok "Python 3.11 at $py311" }

$gpuName = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpuName = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | Select-Object -First 1)
    Ok "GPU: $gpuName"
} else {
    Warn "nvidia-smi not found -- GPU paths will fall back to CPU."
}

if ($VerifyOnly) { Say "VerifyOnly: skipping all installation" }

# -- 1. Project dependencies --------------------------------------------------
if (-not $VerifyOnly) {
    Say "Installing project dependencies (uv sync --extra dev)"
    Push-Location $RepoRoot
    & uv sync --extra dev
    if ($LASTEXITCODE -ne 0) { Pop-Location; Die "uv sync failed" }
    Pop-Location
    Ok "project deps"
}

# -- 2. Blender ---------------------------------------------------------------
if (-not $SkipBlender -and -not $VerifyOnly) {
    Say "Ensuring Blender"
    $bs = Join-Path $PSScriptRoot "setup-blender.ps1"
    if (Test-Path $bs) { & powershell -ExecutionPolicy Bypass -File $bs }
    else { Warn "scripts/setup-blender.ps1 not found -- skipping" }
}

# -- 3. Composed textures -----------------------------------------------------
# git does not carry these; without them 23 tests fail on a missing
# 'knit_white' surface. That is EXPECTED on a fresh clone, not a regression.
if (-not $VerifyOnly) {
    Say "Generating composed textures"
    Push-Location $RepoRoot
    & uv run python scripts/gen_template_textures.py --template templates/mattress.yaml
    if ($LASTEXITCODE -ne 0) { Warn "texture generation returned $LASTEXITCODE" } else { Ok "textures" }
    Pop-Location
}

# -- 4. ComfyUI + venv311 + wheels + custom nodes -----------------------------
if (-not $SkipComfy) {
    Say "ComfyUI at $ComfyRoot"

    if (-not $VerifyOnly) {
        if (-not (Test-Path (Join-Path $ComfyRoot "main.py"))) {
            $parent = Split-Path -Parent $ComfyRoot
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            Say "Cloning ComfyUI"
            & git clone https://github.com/comfyanonymous/ComfyUI $ComfyRoot
            if ($LASTEXITCODE -ne 0) { Die "ComfyUI clone failed" }
        } else { Ok "ComfyUI present" }

        Say "Installing custom nodes"
        $cnDir = Join-Path $ComfyRoot "custom_nodes"
        if (-not (Test-Path $cnDir)) { New-Item -ItemType Directory -Path $cnDir -Force | Out-Null }
        foreach ($n in $CUSTOM_NODES) {
            $target = Join-Path $cnDir $n.name
            if (Test-Path $target) { Ok "$($n.name) present" }
            else { & git clone --depth 1 $n.url $target | Out-Null; Ok "cloned $($n.name)" }
        }

        $venv = Join-Path $ComfyRoot "venv311"
        $vpy  = Join-Path $venv "Scripts\python.exe"
        if (-not (Test-Path $vpy)) {
            Say "Creating Python 3.11 venv"
            & $py311 -m venv $venv
            if ($LASTEXITCODE -ne 0) { Die "venv creation failed" }
        }
        Ok "venv311"

        Say "Installing torch $TORCH_VER (cu128) -- pinned, see guide"
        & $vpy -m pip install --upgrade pip setuptools wheel --quiet
        & $vpy -m pip install "torch==$TORCH_VER" "torchvision==$TORCHVISION_VER" `
              "torchaudio==$TORCHAUDIO_VER" --index-url $CUDA_INDEX
        if ($LASTEXITCODE -ne 0) { Die "torch install failed" }

        Say "Installing ComfyUI requirements"
        & $vpy -m pip install -r (Join-Path $ComfyRoot "requirements.txt")

        # ComfyUI's requirements pull the LATEST torchaudio, which mismatches
        # torch and fails at import with OSError: [WinError 127]. Re-pin.
        Say "Re-pinning torchaudio (ComfyUI requirements override it)"
        & $vpy -m pip install "torchaudio==$TORCHAUDIO_VER" --index-url $CUDA_INDEX --quiet

        Say "Installing TRELLIS 2 compiled wheels ($PY_TAG)"
        $wdir = Join-Path $ComfyRoot "custom_nodes\ComfyUI-Trellis2\wheels\Windows\$WHEEL_DIR_NAME"
        if (-not (Test-Path $wdir)) { Die "wheel dir missing: $wdir" }
        foreach ($w in @("cumesh-1.0","nvdiffrast-0.4.0","nvdiffrec_render-0.0.0",
                         "flex_gemm-0.0.1","o_voxel-0.0.1","custom_rasterizer-0.1")) {
            $whl = Join-Path $wdir "$w-$PY_TAG-$PY_TAG-win_amd64.whl"
            if (Test-Path $whl) { & $vpy -m pip install $whl --quiet; Ok $w }
            else { Warn "wheel not found: $w" }
        }

        # --no-deps: plain `pip install xformers` silently upgrades torch to
        # 2.11 and breaks every wheel installed above.
        Say "Installing xformers $XFORMERS_VER (--no-deps, pinned)"
        & $vpy -m pip install "xformers==$XFORMERS_VER" --index-url $CUDA_INDEX --no-deps --quiet

        Say "Installing custom-node requirements"
        foreach ($n in $CUSTOM_NODES) {
            $req = Join-Path $cnDir "$($n.name)\requirements.txt"
            if (Test-Path $req) { & $vpy -m pip install -r $req --quiet }
        }
        & $vpy -m pip install hf_transfer huggingface_hub --quiet
        Ok "ComfyUI environment"
    }
}

# -- 5. Model weights ---------------------------------------------------------
if (-not $SkipComfy -and -not $SkipModels -and -not $VerifyOnly) {
    Say "Downloading model weights (~25.6 GB) -- hf_transfer enabled"
    $vpy       = Join-Path $ComfyRoot "venv311\Scripts\python.exe"
    $modelsDir = Join-Path $ComfyRoot "models"
    $dl = Join-Path $env:TEMP "3db_dl_models.py"
    $lines = @(
        'import os, sys, time',
        'os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"',
        'from huggingface_hub import snapshot_download',
        'root = sys.argv[1]',
        'jobs = ['
    )
    foreach ($m in $MODELS) {
        $d = ($m.dest -replace '\\','/')
        $lines += "    (`"$($m.repo)`", `"$d`"),"
    }
    $lines += @(
        ']',
        'for repo, rel in jobs:',
        '    dest = os.path.join(root, *rel.split("/"))',
        '    t = time.time()',
        '    print(f"--> {repo}", flush=True)',
        '    try:',
        '        snapshot_download(repo_id=repo, local_dir=dest, max_workers=8)',
        '        print(f"    done in {time.time()-t:.0f}s", flush=True)',
        '    except Exception as e:',
        '        print(f"    FAILED {type(e).__name__}: {e}", flush=True)'
    )
    Set-Content -Path $dl -Value $lines -Encoding utf8
    & $vpy $dl $modelsDir
    Ok "models"
}

# -- 6. Verification ----------------------------------------------------------
Say "Verifying"

Push-Location $RepoRoot
& uv run python -c "from src.blender.locate import locate_blender; b=locate_blender(); print('Blender:', b.version if b else 'NOT FOUND')"
Pop-Location

if (-not $SkipComfy) {
    $vpy = Join-Path $ComfyRoot "venv311\Scripts\python.exe"
    if (Test-Path $vpy) {
        & $vpy -c @"
import sys, torch
print('ComfyUI python :', sys.version.split()[0])
print('torch          :', torch.__version__, '| cuda available:', torch.cuda.is_available())
bad = []
for m in ('cumesh','o_voxel','flex_gemm','nvdiffrast','xformers','torchaudio','torchvision'):
    try:
        __import__(m); print('  OK  ', m)
    except Exception as e:
        bad.append(m); print('  XX  ', m, type(e).__name__)
sys.exit(1 if bad else 0)
"@
        if ($LASTEXITCODE -ne 0) { Warn "some ComfyUI imports failed -- see docs/MACHINE_SETUP.md troubleshooting" }
        else { Ok "ComfyUI imports" }
    }
}

Write-Host ""
Say "Setup complete"
Write-Host @"
Next steps:
  1. Set the vision API key yourself (never stored in this repo):
       setx THREED_VLM_API_KEY "<your-key>"
     then restart the shell.
  2. Run the tests:
       uv run python -m pytest tests -q
  3. Start the web UI:
       uv run python -m src.cli ui
  4. Start ComfyUI (only needed for the neural route):
       $ComfyRoot\venv311\Scripts\python.exe $ComfyRoot\main.py --port 8189

Full details, pins and troubleshooting: docs/MACHINE_SETUP.md
"@ -ForegroundColor Gray
