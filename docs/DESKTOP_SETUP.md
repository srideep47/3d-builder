# Desktop setup runbook

> Target host: **Ryzen 9 9950X · RTX 4080 Super 16 GB · 64 GB DDR5-6000 ·
> Gen4 NVMe.** Do this before any other work. Written 2026-09-02.

---

## 0. Before leaving the laptop

Six commits on `wip/defect-fixes` exist only locally. **Push first or round 4
is stranded:**

```bash
git push -u origin wip/defect-fixes
```

## 1. Clone

```bash
git clone https://github.com/srideep47/3d-builder.git
```

```bash
cd 3d-builder && git checkout wip/defect-fixes && uv sync --extra dev
```

## 2. The three things git does not carry

`tools/`, `assets/textures/` and `output/` are all gitignored.

**Blender** (`tools/` is ignored):

```bash
powershell -File scripts/setup-blender.ps1
```

**Composed textures** — without these, 23 tests fail with
`FileNotFoundError: Composed texture surface 'knit_white' is missing`. This is
expected on a fresh clone and is **not** a regression:

```bash
uv run python scripts/gen_template_textures.py --template templates/mattress.yaml
```

**Renders and packages** under `output/` do not transfer. Regenerate what you
need. The client reference photos **do** transfer — they are committed at
`input/references/MAYA00053153/`.

## 3. API key

Environment only, never in the repo.

```bash
setx THREED_VLM_API_KEY "your-gemini-key"
```

Restart the shell (and ZCode) afterwards so it inherits the variable —
`setx` does not affect already-running processes.

> **Enable billing on this key in Google Cloud before any vision call.**
> Free-tier submissions are used for training and may be human-reviewed; the
> paid tier is not. Client NDA reference photos have already gone through the
> free tier. See `docs/VISION_CONFIG.md`.

Also turn the vision provider on in `config/ai.yaml` — it ships off:

```yaml
vision:
  vlm:
    provider: gemini
    model: gemini-3.5-flash-lite   # pinned; -latest aliases are rejected
```

## 4. Verify — all three must pass

```bash
uv run python -m src.cli health
```

```bash
uv run python -m pytest tests -q
```

You want **250 passed**. Blender-marked tests skip if Blender is absent —
that is expected until step 2 completes, but a smaller number is not "green".

## 5. GPU baking — the new capability, confirm it explicitly

This is the biggest single change from the laptop. The GTX 1650 Ti's 4 GB
could not do meaningful GPU Cycles baking, so 4K bakes ran on CPU at **~19
minutes** and the generic 300 s op timeout silently killed them mid-chain
(now a real `bake_timeout_sec` parameter, exposed as `--bake-timeout`).

The 4080 Super has 16 GB. Confirm Cycles is actually using CUDA/OptiX and
not silently falling back to CPU, then run a real 4K bake and **record the
wall clock**:

```bash
uv run python -m src.cli package --job input/jobs/TEST-QUEEN.yaml --res 4096
```

> **Elapsed time is the WRONG signal here — GPU utilisation is the right
> one.** This machine does a CPU 4K bake in ~8 minutes, which IS "minutes";
> the original "~19 min means CPU" heuristic would have passed while the
> GPU sat idle (it did — 531 s wall, GPU pinned at 0% / 0 MiB, because
> `scene.cycles.device` was hardcoded "CPU"). Measure the mechanism, not
> the proxy:
>
> ```bash
> nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 1
> ```
>
> Non-zero VRAM during the bake is the proof. The device actually used is
> also recorded in qa_report.json under `finish.bake_device_resolved`
> (requested type, GPU/CPU, enabled devices, fallback reason), and
> `scripts/bench_bake_device.py` measures utilisation + wall clock per
> device (CPU/OptiX/CUDA) on the real 4K workload with map diffs.

Expect minutes, not tens of minutes, AND non-zero GPU utilisation. If the
GPU stays at 0%, the bake is on CPU — fix that before building anything
else, because the whole throughput plan assumes GPU baking.

> **Measured on the real TEST-QUEEN 4K workload (2026-09-02,
> `output/bakeoff/bake_device_bench.json`):** CPU 531 s · OptiX 590 s
> (555 s with persistent data) · CUDA 560 s but hard-crashed Blender
> natively in 1 of 2 identical runs. The GPU is ENGAGED (utilisation +
> VRAM evidenced) yet not faster than CPU on this workload — the bake op
> is ~266 small serial Cycles sessions (the selected-to-active normal
> pass alone is 196 of them) with a full scene re-sync between each, so
> wall time is sync-overhead-bound, not ray-bound. Device choice cannot
> deliver 2–4 min here; the pass-structure fix (per-target HP source
> selection) is the identified follow-up. `--bake-device cpu` picks CPU
> explicitly; default `auto` prefers OptiX → CUDA → … → CPU.

## 6. Optional — local Qwen 27B as vision fallback

Owner has this running at **10–15 tok/s with GPU + CPU offload.**

It is **too slow to drive the agent loop** — that measures out at roughly 90
minutes of thinking per model, so ~22 hours for 15 models. It is the right
answer to a Gemini daily-quota exhaustion, where a 1–2 minute verdict a few
times a day costs nothing.

**It owns the GPU while loaded, so Blender cannot bake.** Load on demand,
take the verdict, unload, hand the GPU back. Never inside the hot loop.

## 7. Hardware reference

| Capability | Laptop (GTX 1650 Ti 4 GB) | Desktop (RTX 4080 Super 16 GB) |
|---|---|---|
| GPU Cycles baking | unusable — 19 min per 4K on CPU | engaged + evidenced (measured: ~9–10 min, CPU-parity — the workload is session-overhead-bound, not ray-bound; see §5) |
| TRELLIS 2 image-to-3D, 512³ | impossible | fits — 16 GB is the stated minimum, 5–10 s/asset |
| Hunyuan3D 2.1 | impossible | fits — 12–16 GB |
| Local VLM 8B Q4 | no | yes, ~6 GB |
| Local VLM 27–32B | no | yes, with CPU offload, 10–15 tok/s |
| Parallel Blender jobs | 6 cores | 16C / 32T, 64 GB |

Image-to-3D stays parked until retopology exists — see `PLAN_AUTONOMOUS.md`
§2 and §9. The hardware is no longer the blocker; the missing pipeline stage is.

## 8. Known first-run gotchas

| Symptom | Cause | Fix |
|---|---|---|
| 7 failed, 16 errors on `composed texture ... missing` | `assets/textures/` is gitignored | step 2, `gen_template_textures.py` |
| `health` shows Blender missing | `tools/` is gitignored | step 2, `setup-blender.ps1` |
| Vision provider constructs as `None` | `config/ai.yaml` ships `provider: local`, `model: null` | step 3 |
| 4K bake dies silently around 5 min | generic 300 s op timeout | fixed — pass `--bake-timeout`; confirm the parameter is threaded through |
| Vision returns 403 / "unregistered caller" | shell started before `setx` | restart the shell |
| `output/` empty of renders | gitignored, does not transfer | regenerate |
