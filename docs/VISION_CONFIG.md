# Vision configuration — Gemini limits, cost, tiering

> Reference for the vision layer. Figures gathered 2026-09-02 from Gemini's
> own documentation via the owner, cross-checked against public pricing pages.
> **Where the two disagreed it is flagged — verify against the official page
> before relying on a number.**

---

## 1. Do this before any vision call

> ### ⚠ Enable billing on the API key
>
> **Free tier:** submitted content is used to train and improve Google
> products and may be reviewed by human annotators.
> **Paid tier:** submitted content is not used to train Google models.
>
> Client reference photographs covered by NDA have **already** been sent
> through the free tier — during the describe-verify pass over all 12
> photos, and in every render-vs-reference verdict since. The owner has been
> informed. Enabling billing closes this going forward.
>
> Same key, one toggle in Google Cloud, no code change — the indirection
> already exists in `src/ai/vlm.py`.

## 2. Why the free tier stopped working

| | Free tier | Paid tier 1 |
|---|---|---|
| Requests / minute | 5–15 | 150–300 |
| Tokens / minute | 250k–1M | 2M–4M |
| **Requests / day** | **20–50** | **uncapped** |
| Daily reset | midnight Pacific | n/a |

Our workload is 30–75 vision requests/day. The four 429s over 35 minutes,
including after a 15-minute wait, were the **daily** ceiling — not a burst
limit. Tier 2 (1,000+ RPM) unlocks automatically after ~$250 of settled
billing, which we will never reach.

## 3. Models

| Model id | Role |
|---|---|
| `gemini-3.5-flash-lite` | **Default.** Every iteration. Fast, cheap, high throughput. Owner's call and correct for the volume path |
| `gemini-3.6-flash` | **Escalation.** One call before packaging, and whenever flash-lite disagrees with the measured gates |
| `gemini-2.5-*` | Legacy. Returns 404 on new keys/projects |

Both ids live in `config/ai.yaml`. **Never hardcode them.** `-latest` aliases
are rejected at construction by design, so a silent model drift cannot pass
review.

**Why two tiers.** `gemini-3.6-flash` is documented as materially more
reliable at spatial alignment and at detecting subtle rendering artifacts.
This project's verdict history supports that: Gemini reported the carry straps
as missing when pixel evidence showed them present, and scored two rounds at
4/10 without ever noticing that form contrast had collapsed below one grey
level. A couple of dollars a month for a better final check is cheap
insurance on the one component with a track record of missing things.

## 4. Image tokenisation — and why not to downscale uniformly

Images ≤ 384 px cost a flat **258 tokens**. Larger images are tiled into
768 × 768 patches at **258 tokens per tile**.

| Input | Tiles | Tokens |
|---|---|---|
| 768 × 768 | 1 | 258 |
| 1024 × 1024 | 4 | 1,032 |
| 1600 × 1600 | 9 | 2,322 |

A naive reading says downscale everything to 768 and cut image tokens 75%.
**Do not.** The two defects that mattered most on this project were an
**illegible label** and a quilt at **under one grey level** — both fine-detail
problems. Blanket downscaling would have hidden the label defect completely.

**Send this instead:**

| View | Size | Tokens | Why |
|---|---|---|---|
| iso, top, front, side | 768 × 768 | 258 ea | structure and layout only |
| **label, border close-ups** | **1024+** | 1,032 ea | fine detail is their entire purpose |
| reference photos | 768 × 768 | 258 ea | currently 2,322 ea for no benefit |

That takes a verdict request from ~11,600 tokens to roughly **4,000**, with no
loss where it matters.

`media_resolution: LOW` forces a single 258-token tile. Never use it on a
close-up.

## 5. Context caching does not apply

Explicit caching supports images, but requires a minimum payload around
**32,768 tokens**. Our request is ~11,600 — well below it. Paid tier only in
any case.

**Do not build it.** This was initially the reviewer's top cost-saving
candidate; it is ruled out.

## 6. What to build instead

**Cache verdicts by image hash.** The same render sets are re-verified
repeatedly. Hash the set, cache the verdict, skip the call. Free, and correct
regardless of quota.

**Gate-first ordering.** Measured gates run every iteration — they cost
nothing and are deterministic. Vision runs **only when the gates are green**.
This cuts vision calls from ~5 to ~2 per model, i.e. ~30/day at 15 models
rather than ~75.

## 7. 429 handling

Branch on the reason code in `error.details`:

| Reason | Action |
|---|---|
| `RATE_LIMIT_EXCEEDED` | exponential backoff with jitter, 2 s → 60 s |
| `QUOTA_EXCEEDED` / `RESOURCE_EXHAUSTED` | **stop retrying.** Load local Qwen 27B, take the verdict, unload, return the GPU to Blender |

There is **no pre-flight endpoint** to query remaining quota. Handle it
reactively in client middleware.

## 8. Batch API — right tool, wrong place for the loop

50% discount on input and output, supports images, runs on an independent
queue that does not consume synchronous RPM. Turnaround up to 24 h, typically
15–45 min.

**Not for the agent loop** — the brain needs the verdict to choose its next
move, and 15–45 minutes per verdict destroys the 20–30 min per-model budget.

**Right use: an end-of-day audit pass** re-verifying the day's finished models
at half price on separate quota. That partly replaces the reviewer
spot-checking that does not scale to 90 renders/day. Build it last.

## 9. Cost

Per verdict after the §4 sizing: ~4,000 input tokens, ~500 output.
At 45 requests/day × 30 days = 1,350 calls/month.

| | flash-lite | 3.6-flash |
|---|---|---|
| Estimated monthly total | **~$1.40** | **~$6.20** |

Both trivial. Billing is worth enabling for the **data-privacy** reason in §1
regardless of cost.

> **Numbers to verify before relying on them.** The figures supplied for
> `gemini-3.5-flash-lite` were $0.075 / $0.30 per million input/output.
> Independent public pricing pages showed 2.5 Flash-Lite at $0.10 / $0.40 and
> 3.1 Flash-Lite at $0.25 / $1.50, and did not list a 3.5 Flash-Lite at all.
> The supplied figure is cheaper than anything corroborated. It does not
> change any decision — everything lands under $10/month — but do not quote
> it as settled.

## 10. Thinking tokens

On 3.5/3.6 models, internal thinking tokens count against the **output**
budget and bill at output rates. `gemini-3.5-flash-lite` reportedly defaults
to a minimal thinking level and rejects custom sampling parameters
(temperature, penalties) — do not send them.

For defect detection, allow a **modest configurable thinking budget**:
accuracy on the one weak component is worth more than the pennies. **Verify
the exact parameter name against current official documentation before wiring
it** — do not trust a remembered flag name.

## 11. Do not build a Gemini CLI bridge

The owner proposed logging a Gemini Pro subscription into the CLI and having
the API model redirect image requests to it. Reasons against:

- **An API model cannot route to a local CLI.** The API is a stateless
  endpoint on Google's servers with no path back to the machine. Any switching
  lives in our `VisionProvider`, not in the model.
- **Headless image input is undocumented.** Headless mode itself is well
  supported (`-p`, `--output-format json`, JSONL events), but Google's
  headless reference says nothing about passing images.
- **The CLI is an agent, not an inference endpoint.** It has tool access, file
  access and its own reasoning loop; extracting a deterministic structured
  verdict fights what the tool is for.
- **Consumer subscriptions grant zero API quota** — entirely separate billing
  systems — and Code Assist terms restrict driving it headless as a general
  inference backend for non-coding production work.
- **Paid API is ~$1.40–6.20/month**, which removes the quota outright and is
  contractually clean commercial use.

If the owner later decides otherwise, add it as a third implementation behind
the existing `VisionProvider` ABC. Do not special-case it anywhere else.

## 12. Standing rule

**Vision is advisory. It must never gate a release.** A VLM score is not
calibrated — it catches gross failures, not fine pattern fidelity. Its
documented misses on this project are listed in
`HANDOFF_CLAUDE_DESKTOP.md` §4. Every verdict recorded before round 4 was
scored against renders now known to be misleading; treat that score history
as void, not as a baseline.
