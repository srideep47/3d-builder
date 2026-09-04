# GLM WORK ORDER — Hour 1–3 brain test (go/no-go)

> Authored by the reviewer seat. This gates the entire 24-hour build
> (`PLAN_AUTONOMOUS.md` §6). Do not start §5 hour-3 work until the outcome is
> reported and the owner has called it.

---

## 0. What is actually being tested

**One question, and only this one:**

> Can you author a **correct `ObjectSpec` for an object you have never seen**,
> cold, from a written description and a constraint list, with **no reviewer
> correcting you**?

Every spec you have produced so far was written **with five rounds of reviewer
correction**. That is a different task from cold authoring, and cold authoring
is precisely where *3DCodeBench* (arXiv 2606.01057) found 12 models fail.

**This is not a quality exercise. Do not iterate. Do not polish.** One shot per
object is the experiment. If you iterate, you destroy the measurement — we
would learn what you can converge to, not what you can author cold, and the
whole point is to know which of those we have before we spend 20 hours
assuming the answer.

## 1. Why you are getting text and not the photograph

You are text-only. Normally the VLM describes the image for you.

For this test the **reviewer** wrote the descriptions in §4, by hand, from the
photographs. That is deliberate, for two reasons:

1. Vision calls are **blocked** until the owner confirms billing on the Gemini
   key (`docs/VISION_CONFIG.md` §1 — NDA photos have already gone through the
   free tier once).
2. It **isolates the variable.** If the specs come out wrong we need to know
   whether *you* failed or the *describer* failed. With a known-good
   description, a bad spec is unambiguously yours.

Treat each description as your only access to the object. **Do not ask for the
image.** If a description is genuinely insufficient to place a part, say so and
name the missing measurement rather than inventing one — see §6.

## 2. Hard constraints on your output

- Emit **exactly one JSON object per subject**: a valid `ObjectSpec`,
  `schema_version` `"2.0.0"`. Nothing else in the code block.
- **Never set `PartSpec.code`. Never emit Blender Python.** The validated-spec
  boundary is the single biggest architectural advantage this system has over
  Blender-MCP setups (`PLAN_AUTONOMOUS.md` §3). `execute_blender_script` is
  being removed for the same reason.
- `units: "meters"`. Every dimension in metres. §4 states every figure in
  **millimetres** — convert them yourself and **state your arithmetic** in the
  notes (§5). A unit slip is the single most likely silent failure here, and a
  factor of 1000 will sail through every gate that only checks self-consistency.
- Shapes are limited to `ShapeType`: `box`, `rounded_box`, `cylinder`,
  `tapered_cylinder`, `sphere`, `cone`, `torus`, `tapered_extrude`,
  `revolve_lathe`, `extrude`, `sweep`. **`organic` is out of scope** — do not
  use it.
- Populate `measurements` with the overall L/W/H from §4 so the deterministic
  measure gate can actually catch you. A spec with no measurements is an
  automatic fail on this test.
- Add `constraints` where they express real intent — `ground_contact` for
  anything standing on the floor, `symmetry` / `coaxial` where the object has
  it. These are cheap and they catch floating geometry, which is failure mode
  #2 in the benchmark.
- Respect `tri_budget` as given per subject.

## 3. Deliverable

For each of the three subjects, in one reply per subject:

1. The `ObjectSpec` JSON.
2. The §5 notes block.

Then **stop.** The reviewer builds them, renders them, and looks. You will not
see the renders — you get the measured gate output back, and the reviewer's
verdict on what the renders actually show.

Build command the reviewer will run (do not run it yourself):

```bash
uv run python -m src.cli build --spec <spec.json> && uv run python -m src.cli render <glb>
```

## 4. The three subjects

Reference images are committed under `input/references/`. **You cannot see
them** — the descriptions below are the reviewer's, written by hand from those
images. They are your only access.

> **Caveat the reviewer is obliged to state:** these are synthetic
> (AI-generated) product images, not photographs of a real object. Cross-view
> consistency is therefore not guaranteed the way it is with a real photo set.
> Where views disagreed, the reviewer judged and states the judgement. This
> does not weaken the test — cold spec authoring is the thing under test, and a
> coherent description is a fair input.

**Dimensions below are STATED TEST CONSTRAINTS, not measurements taken from the
images.** They are plausible real-world sizes supplied so the measure gate has
something to check. This does not breach the never-infer-dimensions rule: that
rule governs client deliverables, and these are synthetic test subjects that can
never be packaged.

---

### Subject A — writing desk
*Probes:* basic decomposition, ground contact, tapered legs, a recessed pull.
*Images:* `input/references/BRAINTEST-A-DESK/` — three 3/4 views
(`front_right`, `front_left`, `rear_no_drawer`).

**Description.** A rectangular wooden writing desk in medium walnut with a
satin, non-glossy finish and straight grain running along its length.

The top is a flat rectangular slab with softly rounded corners and a slight
round-over on its edges. It overhangs the structure beneath it by a small
margin on all four sides.

Directly beneath the top, set back from the edge, a rail runs the full length
of each long side. On **one** long side a single wide, shallow drawer is set
into that rail, centred along the length. The drawer front sits flush with the
surrounding rail and is separated from it by a fine, even reveal line on all
four sides. Its pull is a horizontal elongated-oval recess cut **into** the
drawer face, centred — there is no protruding handle or knob.

The **opposite** long side carries the same rail but **no drawer** — it is a
plain, continuous, unbroken face. This is visible in `rear_no_drawer` and is
the single most easily missed feature in the set.

Four legs, one near each corner, set slightly inboard of the top's corners.
Each runs from the underside of the rail to the floor. They are square in
section, noticeably thicker at the top than at the bottom, tapering
continuously along their length, and meeting the floor on a small flat foot.
All four stand on the floor.

**Stated dimensions:** overall **1200 mm (L) × 600 mm (W) × 750 mm (H)**.
Drawer opening approximately 600 mm wide × 90 mm tall.
`tri_budget`: 40000

---

### Subject B — teacup
*Probes:* **the axis of revolution, a hollow interior, and a closed-loop handle
that must attach to the body at two separate points.** This is the hardest of
the three and the one most likely to come out floating, solid, or off-axis.
*Images:* `input/references/BRAINTEST-B-CUP/` — three side elevations (handle
right, handle right alt, handle left) and one 3/4 view from above showing the
interior.

**Description.** A plain white glazed porcelain teacup. No pattern, no
lettering, no decoration of any kind. The glaze is glossy with strong specular
highlights.

The body is a rounded bowl, circular in every horizontal section. It is at its
widest just below the rim. From there the wall curves inward and downward in a
smooth continuous arc — the lower portion is close to hemispherical — and
narrows considerably toward the base. The profile has no straight cylindrical
section and no hard corner anywhere along it.

The rim is circular and flares very slightly outward, finishing in a thin
rounded lip. The cup is **hollow**: the interior is glazed and clearly visible
in the 3/4 view, and the interior floor is a shallow curve rather than flat.
The wall reads as thin — the rim shows only a modest thickness.

At the bottom is a short foot ring: a low, roughly cylindrical pedestal
noticeably narrower than the bowl above it, with a visible step or undercut
where the two meet. **The cup stands on this ring, not on the bowl.**

A single handle sits on one side. It is a **closed loop**: it leaves the body
just below the rim, sweeps outward and downward in a rounded ear or D shape,
and **rejoins the body at roughly the lower third of the bowl.** Both ends
meet the body — it is attached at two points, not cantilevered. Its
cross-section is a rounded oval, thicker where it meets the body and thinner
around the outer curve. The opening it encloses is roughly oval, taller than
it is wide.

**Stated dimensions:** rim outer diameter **105 mm**; height excluding the
handle **75 mm**; overall width including the handle **135 mm**; foot ring
diameter **55 mm**.
`tri_budget`: 40000

---

### Subject C — doormat
*Probes:* **surface relief, not decomposition.** The geometry here is trivial —
two nested flat slabs. Almost the entire character of this object is in the
pile texture. If you spend your effort on parts rather than on the surface, you
have answered the wrong question.
*Images:* `input/references/BRAINTEST-C-DOORMAT/` — three iso views and one
straight-down plan view.

**Description.** A rectangular doormat lying flat on the floor, made of two
plainly distinct materials.

The outer element is a smooth matte **black rubber border**, a continuous flat
frame of constant width running around all four sides, with generously rounded
outer corners. Its top surface is smooth and flat.

Inset within that frame is a rectangular field of coarse **mid-brown coir
fibre pile**, following the same rounded-rectangle shape. Its surface is a
dense mass of looped, crimped fibre. Across it runs a regular directional
ribbing — fine parallel ridges spaced evenly across the short dimension and
running parallel to the long edges. In the plan view the ribbing reads as
roughly evenly spaced lines across the full width of the field.

The pile stands slightly **proud of** the rubber border, and at the boundary
the pile edge visibly overhangs the frame a little. Overall the object is a
thin flat slab — its thickness is very small relative to its footprint.

The underside is not visible in any view.

**Stated dimensions:** overall **750 mm (L) × 450 mm (W) × 20 mm (H)**. Rubber
border approximately 35 mm wide. Pile stands approximately 6 mm above the
rubber surface.
`tri_budget`: 40000

## 5. Notes block — required with every spec

Six lines. No prose beyond them.

```
UNITS:       <the inch→metre conversion you performed, with the arithmetic>
DECOMPOSITION: <why these parts, in one sentence>
UNCERTAIN:   <every value you guessed because the description did not give it>
FLOATING:    <how you know each part touches what it should — which constraint or
              which coordinate arithmetic proves it>
BUDGET:      <your triangle estimate and how you arrived at it>
CANNOT VERIFY: <anything you cannot check without seeing a render>
```

`UNCERTAIN` and `CANNOT VERIFY` being non-empty is **a good answer, not a weak
one.** Per `HANDOFF_GLM_AUTONOMOUS.md` §6: *"I cannot verify this — you look"*
is completely acceptable and far more useful than a proxy. A guessed number
presented as a known one is the failure we are hunting.

## 6. Reporting discipline for this test specifically

Your two historical misses were **overstatement, never fabrication**, and both
had the same shape: **you measured something adjacent to the real question.**
The label chroma described the source artwork, not the render. The symmetry
ratio hit 1.0 because both terms went to zero.

So on this test:

- **Do not claim a spec is correct.** You cannot see it. Nobody expects you to.
- Do not report a self-scored confidence number. It measures nothing here.
- If you think a subject is under-specified, **say which measurement is
  missing** and author your best spec anyway, with the gap named in
  `UNCERTAIN`. Do not refuse, and do not silently invent.

## 7. How this will be judged

Not by your report. By the reviewer building each spec and looking at the
render, against `PLAN_AUTONOMOUS.md` §6:

| Outcome | Consequence |
|---|---|
| Recognisable objects | Full autonomous runtime gets built. |
| Rough but fixable | Built, plus a spec-repair step and a bigger iteration budget. |
| Garbage or floating geometry | **Pivot at hour 3.** Fall back to guided authoring — you fill a structured template instead of authoring freely. Still autonomous, far more reliable. |

The third row is **not a failure of yours.** It is the answer the test exists to
find, and finding it at hour 3 instead of hour 20 is the entire value of running
it. Author honestly; do not stretch to make the good outcome happen.
