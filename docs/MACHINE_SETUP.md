# Machine setup — full bring-up guide

Everything git does **not** carry, and every version pin that matters.

One command does the lot:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-machine.ps1
```

Useful flags:

| Flag | Effect |
|---|---|
| `-SkipModels` | everything except the 25.6 GB download |
| `-SkipComfy` | core pipeline only, no neural route |
| `-SkipBlender` | leave an existing Blender alone |
| `-VerifyOnly` | check an existing install, change nothing |
| `-ComfyRoot "D:\AI\ComfyUI"` | install ComfyUI elsewhere |

The script is **idempotent** — safe to re-run.

---

## 1. What you need first

| | Requirement | Notes |
|---|---|---|
| OS | Windows 10/11 | ComfyUI paths and wheels are Windows-specific |
| GPU | NVIDIA, **16 GB+** | verified on RTX 4080 Super |
| Driver | recent, CUDA 12.8 capable | verified on 610.47 |
| **Python** | **3.11** — mandatory for the neural route | see §3 |
| `git`, `uv` | on PATH | `uv` from astral.sh |
| Disk | **~40 GB** | 25.6 GB models + ~8 GB torch + Blender |
| RAM | 32 GB+ | 64 GB comfortable |

## 2. What the script installs

1. **Project deps** — `uv sync --extra dev`
2. **Blender 4.x** — delegates to `scripts/setup-blender.ps1`
3. **Composed textures** — `gen_template_textures.py`
4. **ComfyUI** + a dedicated **Python 3.11 venv** + TRELLIS 2 wheels + 3 custom node packs
5. **Model weights**, ~25.6 GB

### The composed textures are not optional

Git does not carry `assets/textures/`. Without them **23 tests fail** with
*"Composed texture surface 'knit_white' is missing"*. That is **expected on a
fresh clone, not a regression** — just run the generator.

## 3. Python 3.11 is mandatory, and this is why

TRELLIS 2 needs five compiled CUDA extensions: `cumesh`, `o_voxel`,
`flex_gemm`, `nvdiffrast`, `nvdiffrec_render`.

The node pack ships prebuilt Windows wheels for **cp311, cp312 and cp313 only.
There is no cp310 wheel anywhere in the pack.** Compiled extensions are
ABI-locked to a Python version, so a cp311 wheel physically cannot install on
3.10. Building from source needs the CUDA toolkit plus MSVC and is hours of
work with a poor success rate.

**3.11 is the repo standard** — it is what the pack's author tested on.

Your main project can stay on whatever Python `uv` manages. ComfyUI gets its
own `venv311`; the two never mix.

## 4. Load-bearing version pins

These are not preferences. Each one was found by something breaking.

| Package | Pin | What happens otherwise |
|---|---|---|
| `torch` | **2.8.0+cu128** | wheels are built against it |
| `torchvision` | 0.23.0+cu128 | must match torch |
| **`torchaudio`** | **2.8.0+cu128** | ComfyUI's `requirements.txt` pulls 2.11 → **`OSError: [WinError 127]`** at startup. Looks like a wheel problem; is not. The script re-pins it *after* installing requirements |
| **`xformers`** | **0.0.32.post2**, `--no-deps` | plain `pip install xformers` **silently upgrades torch to 2.11** and breaks all five compiled wheels at once |

If you ever hand-install into that venv, check afterwards:

```powershell
D:\Work\AI_Tools\ComfyUI\venv311\Scripts\python.exe -m pip list | Select-String "^torch"
```

Anything other than 2.8.0 / 0.23.0 / 2.8.0 means something dragged it.

## 5. Node settings that actually work

In `Trellis2LoadModel`:

```
modelname      = "visualbruno/TRELLIS.2-4B-FP8"   # fp8 — fits 16 GB
backend        = "sdpa"                            # NOT flash_attn
device         = "cuda"
low_vram       = True
conv_backend   = "flex_gemm"
sparse_backend = "xformers"
use_reconviagen= False                             # incompatible with fp8
```

**`flash_attn` is not installed and is painful to build on Windows.** Saved
workflows often default to it and will crash. Use `sdpa`.

## 6. Models the script downloads

| Repo | Size | Purpose |
|---|---|---|
| `visualbruno/TRELLIS.2-4B-FP8` | 7.6 GB | fp8 build — the 16 GB-friendly one |
| `microsoft/TRELLIS.2-4B` | 16 GB | full precision |
| `visualbruno/dinov3-vitl16-...` | 1.2 GB | vision encoder |

Downloaded with `hf_transfer` — about **7 minutes** on a fast line.

**DINOv3 is not gated.** Meta's original repo requires licence acceptance; the
wrapper pulls a non-gated mirror instead. Nothing for you to accept.

Extra checkpoints (`TRELLIS-image-large`) download automatically on first run.

## 7. API keys — never in this repo

```powershell
setx THREED_VLM_API_KEY "your-key-here"
```

Then **restart your shell** (and any IDE) so it inherits.

Resolution order is `THREED_VLM_API_KEY` → `GEMINI_API_KEY`, environment only.
`.gitignore` covers `.env`. No key is ever read from a config file.

Vision provider lives in `config/ai.yaml` under `vision.vlm`.

> **Enable billing before sending any client photo.** Free-tier submissions are
> used for training and may be human-reviewed. Paid tier is not. Same key, one
> toggle in Google Cloud.

## 8. Verifying

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-machine.ps1 -VerifyOnly
uv run python -m pytest tests -q      # expect 575 passed
uv run python -m src.cli health       # Blender + provider
```

## 9. Running it

```powershell
uv run python -m src.cli ui                     # web UI on :8137
uv run python -m src.cli health
uv run python -m src.cli package --job input/jobs/TEST-QUEEN.yaml `
    --template templates/mattress.yaml --res 4096 --bake-timeout 3600
```

ComfyUI, only for the neural route:

```powershell
D:\Work\AI_Tools\ComfyUI\venv311\Scripts\python.exe `
    D:\Work\AI_Tools\ComfyUI\main.py --port 8189
```

**Two `--bake-timeout` notes:** the default is 300 s and a 4K bake needs far
more — pass 3600. And `--template` needs the **path**, not the bare name.

## 10. Troubleshooting

**`OSError: [WinError 127]` on ComfyUI startup** — torchaudio/torch mismatch.
Re-pin torchaudio to 2.8.0 (§4).

**`ModuleNotFoundError: cumesh` (or o_voxel, flex_gemm)** — wheels not
installed, or something upgraded torch. Check §4, then reinstall the wheels.

**`ComfyUI-StableXWrapper` fails to import** — harmless. Those nodes are
bypassed in the shipped workflows. Do not spend time on it.

**A bake runs but the GPU sits at 0%** — Cycles device not engaged. Acceptance
is **GPU utilisation**, not elapsed time: a CPU 4K bake here takes ~8 minutes,
which already reads as "minutes", so timing alone cannot tell you.
Sample `nvidia-smi` during the bake.

**Out of memory with ComfyUI running** — TRELLIS holds the card. Free it:

```powershell
curl -X POST http://127.0.0.1:8189/free -H "Content-Type: application/json" `
     -d '{\"unload_models\":true,\"free_memory\":true}'
```

**Tests fail on missing `knit_white`** — run the texture generator (§2).

## 11. Reference — measured on this hardware

RTX 4080 Super, Ryzen 9 9950X, 64 GB.

| Operation | Time |
|---|---|
| Full test suite (575) | ~155 s |
| 4K bake, full chain, CPU | 531 s |
| 4K bake, OptiX | 590 s — **slower**, see note |
| TRELLIS 2 mesh, 4 views | 280–680 s |
| TRELLIS 2 texturing pass | ~220 s |
| Model download (25.6 GB) | ~7 min |

**On the GPU being slower:** the bake op is session-overhead-bound, not
ray-tracing-bound. Blender splits a selected-to-active bake into one Cycles
session *per selected source*, so the normal phase runs 196 sessions where 14
would do — 64% of total bake time. The GPU only ray-traces ~60–80 s of a 555 s
operation. Fixing the session count, not the device, is where the win is.

CUDA also hard-crashed once in two identical runs (native exit −1, no error
text). `auto` prefers OptiX first — keep that order.

## 12. Layout after setup

```
3d-builder/                     this repo
├─ assets/textures/             GENERATED, not in git
├─ tools/blender-*/             INSTALLED, not in git
├─ output/                      run artifacts, not in git
└─ .venv/                       uv-managed

D:\Work\AI_Tools\ComfyUI\        separate install
├─ venv311/                      Python 3.11 + torch 2.8.0
├─ custom_nodes/
│   ├─ ComfyUI-Trellis2/         + prebuilt wheels
│   ├─ ComfyUI-Easy-Use/
│   └─ rgthree-comfy/
├─ models/                       ~25.6 GB
└─ output/                       generated GLBs
```

Keep ComfyUI **outside** this repo. It is a separate application with its own
Python, and vendoring it would put 25 GB of weights near your source tree.
