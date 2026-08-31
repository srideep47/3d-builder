# Kickoff prompt for GLM-5.3 in ZCode

Paste the block below as your first message. Fill in the two `<...>` fields.

---

```
You are the implementing agent for the repository at D:\Work\LegionStudios\3d-builder.

Read GLM_BRIEF.md in the repository root. It is your complete work order: the
mission, the client's contract, a written transcription of the reference
images, the current state of the repo, and the tasks in sequence. Then read the
four documents it lists in its section 2 before writing any code.

Context you need up front:

1. You are text-only and cannot see images. GLM_BRIEF.md section 5 is a written
   transcription of the reference photographs, produced by a vision-capable
   reviewer. Treat it as your eyes. Do not try to open or decode the image
   files, and never claim to have seen them.

2. We are a freelance 3D asset supplier. The client (MetaZtech) sends reference
   photos plus measurements; we return models and textures that must pass their
   automated validator and a human QA review. You are building the pipeline
   that does this repeatably — you are not hand-modelling one asset.

3. The first job is a mattress, but the mattress is not the point. Future jobs
   will be sofas, lamps, cookware, packaging. Product knowledge belongs only in
   templates/<product_class>.yaml, never in the pipeline code.

Operating rules for this session:

- Section 3 of the brief lists absolute rules. Several encode bugs that have
  already cost real time. Do not violate any of them.
- Work the tasks in order: T0, T1, T2, T3, T4, T5. Do not begin a task until
  the previous task's exit criteria are actually met.
- Every task ends with a passing test or a smoke script. The test baseline is
  68 passing; never reduce it.
- NEVER infer, guess, or derive dimensions. The owner supplies exact dimensions
  with an explicit unit for every job. If they are missing, stop and ask.
- Section 9 lists open questions I have not yet answered. If you hit one, stop
  and ask me rather than assuming. Do not silently pick a default.
- Do not refactor outside the current task's scope.
- Do not work on src/img3d/ or services/img3d_service/ — that track is parked.
  Leave the code in place.

Start with T0: commit the uncommitted VLM work so the tree is clean. Show me
`git status` and the pytest result before you move to T1.

Then, before starting T1, give me a short plan for it in your own words so I can
confirm you have understood the target.

Job details for the first real asset (T4), for context only — do not start it yet:
  Job code:   MAYA00053153
  Product:    Nisien 10 Inch Gel Memory Foam Hybrid Queen Mattress
  Scope:      the mattress only — no bed frame, no pillows, no bedding, no room
  Dimensions: <OWNER TO SUPPLY — L × W × H with explicit unit>
  Complexity: Simple
  Orientation: Floor
```

---

## Notes for the owner

**Before T4 you must supply the dimensions.** The brief deliberately makes GLM
stop rather than guess — the job card's `12 × 12 × 65 IN` looks like the rolled
shipping box, and a previous planning pass hallucinated `60 × 80 × 10 in`. Give
it the real numbers with the unit spelled out.

**Four open questions are logged in `GLM_BRIEF.md` §9.** GLM is instructed to
stop and ask rather than assume on each. Three of them need MetaZtech:

1. Is `.spp` a hard requirement, or are baked PNG sets acceptable?
2. What is the *Simple* tier polycount ceiling? (Medium is 200,000)
3. What FBX axis/unit convention does the validator expect?

The fourth is for you: are the vertical straps on the side border carry
handles, and do you want them modelled?

**If GLM stalls or drifts**, the two highest-value corrections to give it are:

- *"Re-read GLM_BRIEF.md §3 and tell me which rule you just violated."*
- *"State this task's exit criteria, then show me the command output proving
  each one."*

**A note on reasoning budget.** `config/ai.yaml` sets
`reasoning_effort: "low"`, which cuts latency ~30× at equal spec quality for
routine work. T3 (the UV/bake pipeline) is the one genuinely hard design task
in this order — consider raising it to `medium` for that task only, and
restoring it afterwards.
