/* 3D Builder Studio — main application.
   State machine over WebSocket run events; renders timeline, gates, viewer,
   renders gallery, spec and log. Vanilla ES modules, no build step. */

import { ModelViewer } from "./viewer.js";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ── api client ───────────────────────────────────────────────────────────

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    init.body = JSON.stringify(opts.body);
    init.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  } else if (opts.body instanceof FormData) {
    // never set Content-Type on FormData — the browser owns the multipart boundary
    init.headers = { ...(opts.headers || {}) };
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// ── state ────────────────────────────────────────────────────────────────

const state = {
  mode: "ai",
  runId: null,
  ws: null,
  running: false,
  startedAt: null,
  timerInterval: null,
  spec: null,
  gates: null,
  mesh: null,
  renders: {},
  logLines: [],
  images: [],           // uploaded reference images [{name, path, url}]
  neuralViews: {},      // §4.1 labelled slots: {front|back|left|right: {name, path, previewUrl}}
  analyse: null,        // §4.3 neural analyse report (event or manifest)
  clientGates: null,    // §4.4 delivery client gates [{gate, expected, received, passed}]
  clientGatesAllPassed: null,
  viewer: null,
  lastMeasure: null,
};

const EXAMPLES = {
  desk: {
    prompt: "A modern wooden office desk with a wide beveled top and four tapered legs",
    measurements: "overall width 1.4 m, overall depth 0.7 m, overall height 0.76 m",
  },
  stool: {
    prompt: "A four-legged wooden counter stool with a round seat and slightly tapered legs",
    measurements: "seat height 0.66 m, seat diameter 0.38 m, leg height 0.61 m",
  },
  table: {
    prompt: "A rectangular coffee table with four slightly tapered legs and a beveled hardwood top",
    measurements: "overall length 1.2 m, overall width 0.6 m, overall height 0.40 m",
  },
  mug: {
    prompt: "A ceramic coffee mug with a hollow interior and a round loop handle on one side",
    measurements: "overall height 0.10 m, body diameter 0.095 m",
  },
};

// ── boot ─────────────────────────────────────────────────────────────────

async function boot() {
  initNav();
  initBuildForm();
  initTabs();
  initViewer();
  initRunsView();
  initDeliveryView();
  await refreshHealth();
  await refreshRunsCount();
  setInterval(refreshHealth, 30000);
}

// ── navigation ───────────────────────────────────────────────────────────

function initNav() {
  $$("#nav .nav-item").forEach((btn) =>
    btn.addEventListener("click", () => {
      $$("#nav .nav-item").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".view").forEach((v) => v.classList.add("hidden"));
      $(`#view-${btn.dataset.view}`).classList.remove("hidden");
      if (btn.dataset.view === "runs") refreshRunsTable();
      if (btn.dataset.view === "delivery") refreshDelivery();
      if (btn.dataset.view === "system") renderSystemView();
      if (btn.dataset.view === "build" && state.viewer) {
        state.viewer._resize();
      }
    })
  );
}

// ── health ───────────────────────────────────────────────────────────────

let lastHealth = null;

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    lastHealth = h;
    setDot("#dot-blender", h.blender.available ? "ok" : "bad");
    setDot("#dot-ai", h.ai.healthy ? "ok" : "bad");
    setDot("#dot-vision", h.ai.vision_supported ? "ok" : "warn");
    const note = $("#vision-note");
    if (note) {
      note.textContent = h.ai.vision_supported
        ? "— vision active"
        : "— text-only mode (no vision endpoint); images are stored for the future VLM";
    }
  } catch {
    setDot("#dot-blender", "warn");
    setDot("#dot-ai", "bad");
    setDot("#dot-vision", "warn");
  }
}

function setDot(sel, cls) {
  const el = $(sel);
  if (!el) return;
  el.className = `dot ${cls}`;
}

// ── build form ───────────────────────────────────────────────────────────

function initBuildForm() {
  // mode switch
  $$("#mode-switch .mode-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      $$("#mode-switch .mode-btn").forEach((b) => b.classList.toggle("active", b === btn));
      state.mode = btn.dataset.mode;
      $("#form-ai").classList.toggle("hidden", state.mode !== "ai");
      $("#form-spec").classList.toggle("hidden", state.mode !== "spec");
      $("#form-neural").classList.toggle("hidden", state.mode !== "neural");
      if (state.mode === "neural") scheduleIntakePreview();
    })
  );

  // material presets
  api("/api/presets").then((presets) => {
    const sel = $("#inp-material");
    presets.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = `${p.name} (${p.category})`;
      sel.appendChild(opt);
    });
  }).catch(() => {});

  // examples
  $("#inp-example").addEventListener("change", (e) => {
    const ex = EXAMPLES[e.target.value];
    if (!ex) return;
    $("#inp-prompt").value = ex.prompt;
    $("#inp-measurements").value = ex.measurements;
    e.target.value = "";
  });

  // dropzone
  const dz = $("#dropzone");
  const fileInput = $("#inp-images");
  dz.addEventListener("click", (e) => {
    if (e.target.closest(".dz-rm")) return;
    fileInput.click();
  });
  fileInput.addEventListener("change", () => addImages(Array.from(fileInput.files)));
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragover"); })
  );
  dz.addEventListener("drop", (e) => addImages(Array.from(e.dataTransfer.files)));

  // spec mode helpers
  $("#btn-spec-file").addEventListener("click", () => $("#spec-file-input").click());
  $("#spec-file-input").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (f) $("#inp-spec").value = await f.text();
  });
  $("#btn-spec-validate").addEventListener("click", validateSpec);

  // build!
  $("#btn-build").addEventListener("click", startBuild);
  $("#btn-cancel").addEventListener("click", cancelRun);

  initNeuralForm();

  // viewer toolbar
  $("#btn-reset-view").addEventListener("click", () => state.viewer && state.viewer.resetView());
  $("#btn-wireframe").addEventListener("click", (e) => {
    if (!state.viewer) return;
    const on = !state.viewer.wireframe;
    state.viewer.setWireframe(on);
    e.currentTarget.classList.toggle("accent", on);
  });
  $("#btn-autorotate").addEventListener("click", (e) => {
    if (!state.viewer) return;
    const on = !state.viewer.autoRotate;
    state.viewer.setAutoRotate(on);
    e.currentTarget.classList.toggle("accent", on);
  });
}

async function addImages(files) {
  const imgs = files.filter((f) => f.type.startsWith("image/"));
  if (!imgs.length) return;
  const fd = new FormData();
  imgs.forEach((f) => fd.append("files", f));
  try {
    const res = await api("/api/uploads", { method: "POST", body: fd });
    res.files.forEach((f, i) =>
      state.images.push({ ...f, previewUrl: URL.createObjectURL(imgs[i]) })
    );
    renderThumbs();
  } catch (e) {
    toast(`Upload failed: ${e.message}`, "bad");
  }
}

function renderThumbs() {
  const wrap = $("#dz-thumbs");
  wrap.innerHTML = "";
  $(".dz-empty").style.display = state.images.length ? "none" : "";
  state.images.forEach((img, i) => {
    const div = document.createElement("div");
    div.className = "dz-thumb";
    const preview = document.createElement("img");
    preview.src = img.previewUrl || "";
    div.appendChild(preview);
    const rm = document.createElement("button");
    rm.className = "dz-rm";
    rm.textContent = "×";
    rm.addEventListener("click", (e) => {
      e.stopPropagation();
      state.images.splice(i, 1);
      renderThumbs();
    });
    div.appendChild(rm);
    wrap.appendChild(div);
  });
}

function validateSpec() {
  const msg = $("#spec-validate-msg");
  try {
    const data = JSON.parse($("#inp-spec").value);
    $("#inp-spec").value = JSON.stringify(data, null, 2);
    msg.textContent = "✓ valid JSON";
    msg.className = "spec-msg ok";
  } catch (e) {
    msg.textContent = `✗ ${e.message}`;
    msg.className = "spec-msg bad";
  }
}

// ── neural intake (§4.1): labelled views + diversity + route preview ──────

const VIEW_LABELS = ["front", "back", "left", "right"];
let intakePreviewTimer = null;

function initNeuralForm() {
  VIEW_LABELS.forEach((label) => {
    const drop = $(`#np-drop-${label}`);
    const fileInput = $(`#np-file-${label}`);
    drop.addEventListener("click", (e) => {
      if (e.target.closest(".vs-rm")) return;
      fileInput.click();
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files[0]) setNeuralView(label, fileInput.files[0]);
      fileInput.value = "";
    });
    ["dragenter", "dragover"].forEach((ev) =>
      drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); })
    );
    ["dragleave", "drop"].forEach((ev) =>
      drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("dragover"); })
    );
    drop.addEventListener("drop", (e) => {
      const f = e.dataTransfer.files[0];
      if (f && f.type.startsWith("image/")) setNeuralView(label, f);
    });
  });

  ["#np-prompt", "#np-l", "#np-w", "#np-h", "#np-class"].forEach((sel) =>
    $(sel).addEventListener("input", scheduleIntakePreview)
  );
  ["#np-unit", "#np-route"].forEach((sel) =>
    $(sel).addEventListener("change", scheduleIntakePreview)
  );
}

async function setNeuralView(label, file) {
  const fd = new FormData();
  fd.append("files", file);
  try {
    const res = await api("/api/uploads", { method: "POST", body: fd });
    state.neuralViews[label] = { ...res.files[0], previewUrl: URL.createObjectURL(file) };
    renderNeuralSlot(label);
    scheduleIntakePreview();
  } catch (e) {
    toast(`Upload failed (${label}): ${e.message}`, "bad");
  }
}

function removeNeuralView(label) {
  delete state.neuralViews[label];
  renderNeuralSlot(label);
  scheduleIntakePreview();
}

function renderNeuralSlot(label) {
  const drop = $(`#np-drop-${label}`);
  const v = state.neuralViews[label];
  const thumb = drop.querySelector(".vs-thumb");
  const hint = drop.querySelector(".vs-hint");
  let rm = drop.querySelector(".vs-rm");
  if (v) {
    thumb.src = v.previewUrl;
    thumb.classList.remove("hidden");
    hint.classList.add("hidden");
    drop.classList.add("filled");
    if (!rm) {
      rm = document.createElement("button");
      rm.className = "vs-rm";
      rm.textContent = "×";
      rm.addEventListener("click", (e) => { e.stopPropagation(); removeNeuralView(label); });
      drop.appendChild(rm);
    }
  } else {
    thumb.src = "";
    thumb.classList.add("hidden");
    hint.classList.remove("hidden");
    drop.classList.remove("filled");
    if (rm) rm.remove();
  }
}

function neuralPayload() {
  const views = {};
  Object.entries(state.neuralViews).forEach(([label, v]) => { views[label] = v.path; });
  return {
    mode: "neural",
    prompt: $("#np-prompt").value.trim(),
    views,
    dims: {
      length: parseFloat($("#np-l").value) || null,
      width: parseFloat($("#np-w").value) || null,
      height: parseFloat($("#np-h").value) || null,
      unit: $("#np-unit").value,
    },
    route: $("#np-route").value,
    product_class: $("#np-class").value.trim() || null,
    job_code: $("#np-code").value.trim() || null,
    declared_fabric: $("#np-fabric").checked,
  };
}

function scheduleIntakePreview() {
  if (state.mode !== "neural") return;
  clearTimeout(intakePreviewTimer);
  intakePreviewTimer = setTimeout(refreshIntakePreview, 500);
}

async function refreshIntakePreview() {
  if (state.mode !== "neural") return;
  const payload = neuralPayload();
  const divBox = $("#np-diversity");
  const routeBox = $("#np-route-preview");
  try {
    const res = await api("/api/intake/preview", { method: "POST", body: payload });

    // §3.1 view diversity: loud visible warning, NEVER a refusal
    if (Object.keys(payload.views).length >= 2) {
      const d = res.diversity;
      divBox.classList.remove("hidden");
      divBox.classList.toggle("warn", !!d.warned);
      divBox.innerHTML =
        `<span class="score">${(d.score ?? 0).toFixed(3)}</span>` +
        `<span>view diversity ${d.warned ? "" : "✓"}</span>` +
        (d.warned ? `<span>⚠ ${escapeHtml(d.reason || "LOW VIEW DIVERSITY — views look near-identical")}</span>` : "");
    } else {
      divBox.classList.add("hidden");
    }

    // §4.0.5 route decision + one-line reason, BEFORE the run
    const r = res.route;
    routeBox.classList.remove("hidden");
    routeBox.classList.toggle("bad", !res.intake.ok);
    routeBox.innerHTML =
      `<span class="rp-route">${escapeHtml(r.route || "?")}</span> will build this` +
      `<span class="rp-reason">${escapeHtml(r.reason || "")}</span>` +
      (res.intake.ok ? "" : `<span class="rp-reason">✗ ${escapeHtml(res.intake.error || "")}</span>`);
  } catch (e) {
    // forced route that cannot run, bad label, missing file — loud, never silent
    divBox.classList.add("hidden");
    routeBox.classList.remove("hidden");
    routeBox.classList.add("bad");
    routeBox.textContent = `✗ ${e.message}`;
  }
}

// ── build / run lifecycle ────────────────────────────────────────────────

async function startBuild() {
  if (state.running) {
    toast("A build is already running — wait or cancel it first", "bad");
    return;
  }

  let payload;
  if (state.mode === "spec") {
    let spec;
    try {
      spec = JSON.parse($("#inp-spec").value);
    } catch (e) {
      toast(`Spec JSON is invalid: ${e.message}`, "bad");
      return;
    }
    payload = { mode: "spec", spec };
  } else if (state.mode === "neural") {
    payload = neuralPayload();
    const d = payload.dims;
    if (!d.length || !d.width || !d.height || !d.unit) {
      toast("Dimensions are required with an explicit unit — never inferred (rule 9)", "bad");
      return;
    }
  } else {
    const prompt = $("#inp-prompt").value.trim();
    if (!prompt) {
      toast("Describe the object first", "bad");
      $("#inp-prompt").focus();
      return;
    }
    payload = {
      mode: "ai",
      prompt,
      measurements: $("#inp-measurements").value.trim(),
      material_preset: $("#inp-material").value || null,
      images: state.images.map((i) => i.path),
    };
  }

  const btn = $("#btn-build");
  btn.disabled = true;
  btn.textContent = "Building…";
  try {
    const { run_id } = await api("/api/build", { method: "POST", body: payload });
    openRun(run_id, { live: true });
  } catch (e) {
    toast(`Build failed to start: ${e.message}`, "bad");
    btn.disabled = false;
    btn.textContent = "⚡ Build";
  }
}

async function cancelRun() {
  if (!state.runId) return;
  try {
    await api(`/api/runs/${state.runId}/cancel`, { method: "POST", body: {} });
    toast("Cancellation requested…");
  } catch (e) {
    toast(e.message, "bad");
  }
}

function resetBuildButton() {
  const btn = $("#btn-build");
  btn.disabled = false;
  btn.textContent = "⚡ Build";
}

// ── run view / websocket ─────────────────────────────────────────────────

async function openRun(runId, { live = false } = {}) {
  closeWS();
  state.runId = runId;
  state.spec = null;
  state.gates = null;
  state.mesh = null;
  state.renders = {};
  state.logLines = [];
  state.lastMeasure = null;
  state.analyse = null;
  state.clientGates = null;
  state.clientGatesAllPassed = null;

  $("#empty-state").classList.add("hidden");
  $("#output-body").classList.remove("hidden");
  $("#output-title").textContent = "Output";
  setRunStatus(null);
  $("#btn-download").classList.add("hidden");
  clearTimeline();
  $("#renders-grid").innerHTML = "";
  $("#gates-body").innerHTML = "";
  $("#spec-json").textContent = "";
  $("#log-view").innerHTML = "";
  $("#viewer-dims").textContent = "";
  switchTab("view3d");

  if (live) {
    setRunStatus("running", "● building");
    startTimer();
    $("#btn-cancel").classList.remove("hidden");
    connectWS(runId);
  } else {
    await loadFinishedRun(runId);
  }
}

function connectWS(runId) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws/${runId}`);
  state.ws = ws;
  ws.onmessage = (msg) => {
    try {
      handleEvent(JSON.parse(msg.data));
    } catch {}
  };
  ws.onclose = () => {
    if (state.ws === ws) state.ws = null;
    // If the socket closes before the run finished, fall back to polling once.
    if (state.running) {
      loadFinishedRun(runId).catch(() => {});
    }
  };
}

function closeWS() {
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
    state.ws = null;
  }
}

function startTimer() {
  state.running = true;
  state.startedAt = Date.now();
  const el = $("#run-timer");
  el.classList.remove("hidden");
  clearInterval(state.timerInterval);
  state.timerInterval = setInterval(() => {
    const s = Math.floor((Date.now() - state.startedAt) / 1000);
    el.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }, 500);
}

function stopTimer() {
  state.running = false;
  clearInterval(state.timerInterval);
  $("#run-timer").classList.add("hidden");
  $("#btn-cancel").classList.add("hidden");
  resetBuildButton();
  refreshRunsCount();
}

// ── event handling (the state machine) ───────────────────────────────────

const STAGE_LABELS = {
  analyst_started: ["Analyst", "GLM-5.3 is drafting the ObjectSpec…"],
  analyst_done: ["Analyst", "ObjectSpec ready"],
  analyst_error: ["Analyst", "failed to produce a valid spec"],
  iteration_started: null, // separator
  route_decided: ["Route", null],
  neural_part_started: ["Neural", "img3d service is generating a mesh…"],
  neural_part_done: ["Neural", "neural mesh generated"],
  neural_part_error: ["Neural", "neural generation failed"],
  neural_skipped: ["Neural", "img3d unavailable — organic parts skipped"],
  neural_generation_started: ["Neural gen", "TRELLIS is generating from the views…"],
  neural_generation_done: ["Neural gen", "mesh generated"],
  analyse_done: ["Analyse", null],
  conform_done: ["Conform", "delivery spec authored"],
  conform_refused: ["Conform", "REFUSED (S1) — aspect ratio off beyond tolerance"],
  package_started: ["Package", "weld → retopo → atlas → bake → FBX → gates…"],
  package_done: ["Package", "package emitted"],
  package_log: null, // log view only — no timeline spam
  template_compiling: ["Template", "compiling product template…"],
  template_compiled: ["Template", "spec compiled"],
  template_warning: ["Template", null],
  build_started: ["Build", "Blender is constructing the parts…"],
  build_done: ["Build", "geometry built"],
  build_error: ["Build", "Blender build failed"],
  measure_done: ["Measure", null],
  render_done: ["Render", "studio renders ready"],
  verification: ["Gates", null],
  visual_gate: ["Visual gate", "VLM compared renders to the reference"],
  correction_started: ["Corrector", "GLM-5.3 is fixing the spec…"],
  correction_done: ["Corrector", "spec corrected"],
  run_finished: null,
  run_error: ["Run", "crashed"],
};

function handleEvent(ev) {
  appendLog(ev);

  switch (ev.event) {
    case "run_started":
      clearTimeline();
      if (ev.prompt) $("#output-title").textContent = ev.prompt.slice(0, 64);
      break;

    case "analyst_started":
      addStep("analyst_started", "running");
      break;

    case "analyst_done":
      markStep("analyst_started", "done");
      state.spec = ev.spec;
      renderSpec();
      addStep("analyst_done", "done", `${(ev.spec?.parts || []).length} parts · ${(ev.spec?.measurements || []).length} measurements`);
      break;

    case "analyst_error":
      markStep("analyst_started", "error");
      addStep("analyst_error", "error", (ev.error || "").slice(0, 300));
      break;

    case "iteration_started":
      addSeparator(`Iteration ${ev.index}`);
      break;

    case "route_decided":
      addStep("route_decided", "done",
        `${ev.route}${ev.forced ? " (forced)" : ""} · ${(ev.reason || "").slice(0, 220)}`);
      break;

    case "neural_generation_started":
      addStep("neural_generation_started", "running",
        `${(ev.views || []).join(", ")} · ceiling ${(ev.max_tris ?? 0).toLocaleString()} tris`);
      break;

    case "neural_generation_done":
      markStep("neural_generation_started", "done",
        `${(ev.tri_count ?? 0).toLocaleString()} tris · ${ev.duration_s ?? "?"}s`);
      break;

    case "analyse_done": {
      const ok = ev.passed;
      addStep(
        "analyse_done",
        ok ? "done" : "error",
        ok
          ? `${(ev.triangles ?? 0).toLocaleString()} tris · ${ev.bodies ?? "?"} bodies · metallic ${ev.metallic != null ? (100 * ev.metallic).toFixed(0) + "%" : "?"}`
          : `gates failed: ${(ev.failed || []).join(", ")}`
      );
      state.analyse = { ...ev, checks: [] };
      renderGates();
      break;
    }

    case "conform_refused":
      addStep("conform_refused", "error", (ev.reason || "").slice(0, 300));
      break;

    case "conform_done":
      addStep("conform_done", "done",
        `${ev.retopology?.tool || "retopo"}${ev.retopology?.voxel_size != null ? ` voxel ${ev.retopology.voxel_size} m` : ""} · maps: ${(ev.maps || []).join(", ") || "none"}`);
      loadSpecFromRun();
      break;

    case "package_started":
      addStep("package_started", "running");
      break;

    case "package_log":
      break; // detail lines ride the log view only

    case "package_done":
      markStep("package_started", ev.all_passed ? "done" : "warn",
        `${ev.all_passed ? "all delivery gates passed" : "delivery gates failed — see qa_report.json"}`);
      state.clientGates = ev.gates || [];
      state.clientGatesAllPassed = !!ev.all_passed;
      renderGates();
      break;

    case "template_compiling":
      addStep("template_compiling", "running", (ev.template || "").split(/[\\/]/).pop());
      break;

    case "template_warning":
      addStep("template_warning", "warn", (ev.warning || "").slice(0, 220));
      break;

    case "template_compiled":
      markStep("template_compiling", "done");
      loadSpecFromRun();
      break;

    case "neural_part_started":
      addStep("neural_part_started", "running", `${ev.part} · ${ev.image?.split(/[\\/]/).pop() || ""}`);
      break;

    case "neural_part_done":
      addStep("neural_part_done", "ok", `${ev.part} · ${ev.tri_count ?? "?"} tris · ${ev.duration_s ?? "?"}s`);
      break;

    case "neural_part_error":
      addStep("neural_part_error", "warn", (ev.error || "").slice(0, 200));
      break;

    case "neural_skipped":
      addStep("neural_skipped", "warn", (ev.reason || "").slice(0, 200));
      break;

    case "build_started":
      addStep("build_started", "running", (ev.parts || []).join(", "));
      break;

    case "build_done":
      markStep("build_started", "done");
      break;

    case "build_error":
      markStep("build_started", "error");
      addStep("build_error", "error", (ev.error || "").slice(0, 260));
      break;

    case "measure_done": {
      state.lastMeasure = ev.overall || {};
      markStep("build_started", "done");
      const dims = state.lastMeasure.dimensions;
      if (dims) {
        $("#viewer-dims").textContent =
          `X ${dims[0].toFixed(3)}  Y ${dims[1].toFixed(3)}  Z ${dims[2].toFixed(3)} m`;
      }
      break;
    }

    case "render_done":
      state.renders = toRenderUrls(ev.views || {});
      renderRenders();
      break;

    case "verification": {
      state.gates = ev.dimension_gate;
      state.mesh = ev.mesh;
      renderGates();
      const dimOk = ev.dimension_gate?.passed;
      addStep(
        "verification",
        dimOk ? "done" : "warn",
        dimOk
          ? "dimension + mesh gates passed"
          : (ev.feedback || "").split("\n").slice(0, 3).join(" · ").slice(0, 220)
      );
      break;
    }

    case "visual_gate": {
      const ok = ev.matches_reference;
      addStep(
        "visual_gate",
        ev.parsed === false ? "warn" : ok ? "done" : "warn",
        ev.parsed === false
          ? "VLM verdict could not be parsed"
          : `score ${ev.score ?? "?"}/10${ev.summary ? " — " + ev.summary : ""}`
      );
      state.visualVerdict = ev;
      break;
    }

    case "correction_started":
      addStep("correction_started", "running");
      break;

    case "correction_done":
      markStep("correction_started", "done");
      break;

    case "run_finished":
      stopTimer();
      setRunStatus(ev.success ? "ok" : "warn", ev.success ? "✓ passed" : (ev.status || "finished"));
      settleRunningSteps(ev.success ? "done" : "error",
        ev.success ? null : (ev.error || ev.status || "run ended"));
      if (ev.spec_name || ev.model_name) $("#output-title").textContent = ev.model_name || $("#output-title").textContent;
      if (ev.final_glb) {
        const glbUrl = ev.final_glb_rel
          ? `/api/runs/${state.runId}/file/${ev.final_glb_rel}`
          : `/api/runs/${state.runId}/file/final.glb`;
        loadModel(glbUrl);
      } else if (ev.renders && Object.keys(ev.renders).length) switchTab("renders");
      if (ev.renders && Object.keys(ev.renders).length) {
        state.renders = toRenderUrls(ev.renders);
        renderRenders();
      }
      const dl = $("#btn-download");
      dl.href = ev.final_glb_rel
        ? `/api/runs/${state.runId}/file/${ev.final_glb_rel}`
        : `/api/runs/${state.runId}/file/final.glb`;
      dl.setAttribute("download", `${(ev.model_name || "model").replace(/\W+/g, "_")}.glb`);
      dl.classList.remove("hidden");
      if (ev.package_dir) {
        markStep("package_done", ev.success ? "done" : "warn",
          `${(ev.package_dir || "").split(/[\\/]/).pop()} · ${ev.wall_clock_s ?? "?"}s`);
      }
      if (!ev._quiet) {
        toast(ev.success ? "Build passed all gates ✓" : "Build finished with warnings", ev.success ? "ok" : "bad");
      }
      closeWS();
      break;

    case "run_error":
      stopTimer();
      setRunStatus("bad", "✗ failed");
      settleRunningSteps("error", null);
      addStep("run_error", "error", (ev.error || "").slice(0, 300));
      if (!ev._quiet) toast("Run failed", "bad");
      closeWS();
      break;

    case "cancel_requested":
      addStep("cancel_requested", "warn", "cancellation requested…");
      break;
  }
}

// ── finished-run loader (history) ────────────────────────────────────────

async function loadFinishedRun(runId) {
  try {
    const data = await api(`/api/runs/${runId}`);
    $("#output-title").textContent = data.label || data.run_id;
    const status = data.status || "unknown";
    setRunStatus(
      status === "completed" ? "ok" : status === "failed" ? "bad" : "warn",
      status.replace(/_/g, " ")
    );

    if (data.spec) {
      state.spec = data.spec;
      renderSpec();
    }
    if (data.manifest) {
      const m = data.manifest;
      state.gates = { passed: m.dimension_gate_passed, details: m.metrics?.dimension_details || [] };
      state.mesh = {
        passed: m.mesh_gate_passed,
        faces_count: m.tri_count,
        vertices_count: m.vertex_count,
        bounding_box_m: m.dimensions_m,
        warnings: m.metrics?.mesh_warnings || [],
      };
      const nm = m.metrics || {};
      if (nm.analyse) state.analyse = nm.analyse;
      if (nm.package && Array.isArray(nm.package.gates)) {
        state.clientGates = nm.package.gates;
        state.clientGatesAllPassed = !!nm.package.all_passed;
      }
      renderGates();
      if (m.dimensions_m) {
        $("#viewer-dims").textContent =
          `X ${m.dimensions_m[0].toFixed(3)}  Y ${m.dimensions_m[1].toFixed(3)}  Z ${m.dimensions_m[2].toFixed(3)} m`;
      }
      const dl = $("#btn-download");
      if (data.final_glb) {
        dl.href = data.final_glb;
        dl.setAttribute("download", `${(m.model_name || "model").replace(/\W+/g, "_")}.glb`);
        dl.classList.remove("hidden");
      }
    }
    state.renders = data.renders || {};
    renderRenders();

    // Replay the stored event history into the timeline + log.
    clearTimeline();
    (data.events || []).forEach(handleEventQuiet);
    if (!(data.events || []).length && data.manifest) {
      addSeparator("Summary");
      addStep("verification", data.manifest.dimension_gate_passed ? "done" : "warn",
        `${data.manifest.metrics?.iterations ?? "?"} iterations · ${data.manifest.metrics?.wall_clock_s ?? "?"}s`);
    }

    if (data.final_glb) loadModel(data.final_glb);
    else if (Object.keys(state.renders).length) switchTab("renders");
  } catch (e) {
    toast(`Could not load run: ${e.message}`, "bad");
  }
}

function handleEventQuiet(ev) {
  // History replay: same handling, but finish toasts are suppressed.
  handleEvent({ ...ev, _quiet: true });
}

// conform_done / template_compiled write run_dir/spec.json — pull it into the
// Spec tab live instead of waiting for a history reload.
async function loadSpecFromRun() {
  if (!state.runId) return;
  try {
    const res = await fetch(`/api/runs/${state.runId}/file/spec.json`);
    if (!res.ok) return;
    const spec = await res.json();
    if (spec && spec.schema_name) {
      state.spec = spec;
      renderSpec();
    }
  } catch {}
}

// ── timeline ─────────────────────────────────────────────────────────────

function clearTimeline() {
  $("#timeline").innerHTML = "";
}

function addSeparator(text) {
  const div = document.createElement("div");
  div.className = "tl-sep";
  div.textContent = text;
  $("#timeline").appendChild(div);
  scrollTimeline();
}

function addStep(key, status, detail) {
  const div = document.createElement("div");
  div.className = `tl-step ${status}`;
  div.dataset.key = key + ":" + (status === "running" ? "r" : "s") + ":" + counter++;
  const ico = status === "running" ? "◌" : status === "error" ? "✕" : status === "warn" ? "!" : "✓";
  div.innerHTML = `<span class="tl-ico">${ico}</span><div><div class="tl-text">${
    STAGE_LABELS[key]?.[0] || key
  }</div>${detail ? `<div class="tl-detail">${escapeHtml(detail)}</div>` : ""}</div>`;
  $("#timeline").appendChild(div);
  scrollTimeline();
  return div;
}

function markStep(key, status, detail) {
  const steps = $$("#timeline .tl-step");
  for (let i = steps.length - 1; i >= 0; i--) {
    const el = steps[i];
    if (el.dataset.key?.startsWith(key + ":") || el.dataset.plain === key) {
      el.className = `tl-step ${status}`;
      const ico = status === "done" ? "✓" : status === "error" ? "✕" : status === "warn" ? "!" : "◌";
      el.querySelector(".tl-ico").textContent = ico;
      if (detail) {
        let d = el.querySelector(".tl-detail");
        if (!d) {
          d = document.createElement("div");
          d.className = "tl-detail";
          el.querySelector("div").appendChild(d);
        }
        d.textContent = detail;
      }
      return;
    }
  }
  // No matching running step — add a completed one.
  const div = addStep(key, status, detail);
  div.dataset.plain = key;
}

// A run that dies mid-chain (e.g. the neural flow failing at generation)
// leaves its last step spinning — settle every still-running step at finish.
function settleRunningSteps(status, detail) {
  $$("#timeline .tl-step.running").forEach((el) => {
    el.className = `tl-step ${status}`;
    el.querySelector(".tl-ico").textContent =
      status === "done" ? "✓" : status === "warn" ? "!" : "✕";
    if (detail) {
      let d = el.querySelector(".tl-detail");
      if (!d) {
        d = document.createElement("div");
        d.className = "tl-detail";
        el.querySelector("div").appendChild(d);
      }
      d.textContent = detail;
    }
  });
}

let counter = 0;

function scrollTimeline() {
  const tl = $("#timeline");
  tl.scrollTop = tl.scrollHeight;
}

// ── renderers ────────────────────────────────────────────────────────────

function renderSpec() {
  if (!state.spec) return;
  $("#spec-json").textContent = JSON.stringify(state.spec, null, 2);
}

// The agent emits absolute filesystem paths for renders; the runs API emits
// ready URLs. Normalize both to the file-serving endpoint.
function toRenderUrls(views) {
  const out = {};
  for (const [view, url] of Object.entries(views || {})) {
    if (!url) continue;
    if (url.startsWith("/api/")) out[view] = url;
    else out[view] = `/api/runs/${state.runId}/file/renders/${url.split(/[\\/]/).pop()}`;
  }
  return out;
}

function renderRenders() {
  const grid = $("#renders-grid");
  grid.innerHTML = "";
  const entries = Object.entries(state.renders || {});
  if (!entries.length) {
    grid.innerHTML = `<div class="empty-state" style="grid-column: 1/-1"><p>No renders yet</p></div>`;
    return;
  }
  entries.forEach(([view, url]) => {
    const card = document.createElement("div");
    card.className = "render-card";
    const img = document.createElement("img");
    img.src = url;
    img.alt = view;
    img.loading = "lazy";
    card.appendChild(img);
    const label = document.createElement("div");
    label.className = "render-label";
    label.innerHTML = `<span>${escapeHtml(view)}</span>`;
    card.appendChild(label);
    card.addEventListener("click", () => openLightbox(url));
    grid.appendChild(card);
  });
}

function renderGates() {
  const body = $("#gates-body");
  if (!state.gates && !state.mesh && !state.analyse && !state.clientGates) {
    body.innerHTML = `<div class="empty-state"><p>No gate results yet</p></div>`;
    return;
  }
  let html = "";

  if (state.gates) {
    const rows = (state.gates.details || []).map((d) => {
      const ok = d.passed;
      const delta = d.delta_mm != null ? `${d.delta_mm >= 0 ? "+" : ""}${Number(d.delta_mm).toFixed(1)} mm` : "—";
      return `<tr>
        <td>${escapeHtml(d.name || "?")}</td>
        <td class="num">${escapeHtml(d.applies_to || "")}</td>
        <td class="num">${Number(d.target_m).toFixed(3)} m</td>
        <td class="num">${Number(d.actual_m ?? 0).toFixed(4)} m</td>
        <td class="num ${ok ? "pass" : "fail"}">${delta}</td>
        <td class="${ok ? "pass" : "fail"}">${ok ? "PASS" : "FAIL"}</td>
      </tr>`;
    }).join("");
    html += `<div class="gate-section">
      <div class="gate-title">Dimension Gate
        <span class="chip ${state.gates.passed ? "ok" : "bad"}">${state.gates.passed ? "passed" : "failed"}</span>
      </div>
      ${rows ? `<table class="gate-table">
        <thead><tr><th>Measurement</th><th>Applies to</th><th>Target</th><th>Actual</th><th>Δ</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>` : `<div class="tl-detail">no measurements declared</div>`}
    </div>`;
  }

  if (state.mesh) {
    const m = state.mesh;
    const bb = m.bounding_box_m || [];
    const facts = [
      ["Watertight", m.is_watertight ? "✓ yes" : "✗ no"],
      ["Triangles", (m.faces_count ?? 0).toLocaleString()],
      ["Vertices", (m.vertices_count ?? 0).toLocaleString()],
      ["Bounds X", bb[0] != null ? `${bb[0].toFixed(3)} m` : "—"],
      ["Bounds Y", bb[1] != null ? `${bb[1].toFixed(3)} m` : "—"],
      ["Bounds Z", bb[2] != null ? `${bb[2].toFixed(3)} m` : "—"],
    ];
    const warns = (m.warnings || []).map((w) => `<div class="tl-detail">⚠ ${escapeHtml(w)}</div>`).join("");
    html += `<div class="gate-section">
      <div class="gate-title">Mesh Gate
        <span class="chip ${m.passed ? "ok" : "warn"}">${m.passed ? "passed" : "warnings"}</span>
      </div>
      <div class="mesh-facts">${facts
        .map(([k, v]) => `<div class="fact"><div class="fact-label">${k}</div><div class="fact-value">${v}</div></div>`)
        .join("")}</div>
      ${warns}
    </div>`;
  }

  // §4.3 neural analyse report — which of the 5 maps exist at what resolution
  if (state.analyse) {
    const a = state.analyse;
    const maps = a.maps || {};
    const mapNames = ["albedo", "roughness", "metallic", "normal", "ao"];
    const mapsHtml = mapNames.map((name) => {
      const v = maps[name];
      const present = typeof v === "object" && v !== null ? !!v.present : !!v;
      const res = typeof v === "object" && v !== null && v.resolution
        ? ` ${v.resolution.join("×")}` : "";
      return `<span style="color:${present ? "var(--green)" : "var(--text-faint)"}">${present ? "✓" : "—"} ${name}${res}</span>`;
    }).join(" · ");
    const facts = [
      ["Triangles", (a.triangles ?? 0).toLocaleString()],
      ["Vertices", (a.vertices ?? 0).toLocaleString()],
      ["Bodies", a.bodies ?? "—"],
      ["Metallic", a.metallic != null ? `${(100 * a.metallic).toFixed(1)}%` : "—"],
      ["Roughness", a.roughness != null ? `${(100 * a.roughness).toFixed(1)}%` : "—"],
      ["Extents (m)", Array.isArray(a.extents_m) ? a.extents_m.map((d) => (d ?? 0).toFixed(3)).join(" × ") : "—"],
    ];
    const checks = (a.checks || []).map((c) => `<tr>
        <td>${escapeHtml(c.name || "?")}</td>
        <td>${c.gating ? "gate" : "record"}</td>
        <td class="num">${escapeHtml(String(c.value ?? "—")).slice(0, 90)}</td>
        <td class="${c.passed ? "pass" : "fail"}">${c.passed ? "PASS" : "FAIL"}</td>
      </tr>`).join("");
    html += `<div class="gate-section">
      <div class="gate-title">Neural Analyse
        <span class="chip ${a.passed ? "ok" : "bad"}">${a.passed ? "gates green" : "gates failed"}</span>
      </div>
      <div class="mesh-facts">${facts
        .map(([k, v]) => `<div class="fact"><div class="fact-label">${k}</div><div class="fact-value">${v}</div></div>`)
        .join("")}</div>
      <div class="tl-detail" style="margin:8px 0">${mapsHtml}</div>
      ${checks ? `<table class="gate-table">
        <thead><tr><th>Check</th><th>Kind</th><th>Value</th><th></th></tr></thead>
        <tbody>${checks}</tbody>
      </table>` : ""}
    </div>`;
  }

  // §4.4 delivery client gates (six pure validators, package time)
  if (state.clientGates) {
    html += `<div class="gate-section">
      <div class="gate-title">Delivery Gates
        <span class="chip ${state.clientGatesAllPassed ? "ok" : "bad"}">${state.clientGatesAllPassed ? "all passed" : "failed"}</span>
      </div>
      ${gateRows(state.clientGates)}
    </div>`;
  }

  body.innerHTML = html;
}

function appendLog(ev) {
  const view = $("#log-view");
  const line = document.createElement("span");
  line.className = "ln" + (ev.event.includes("error") ? " err" : ev.event === "run_finished" && ev.success ? " ok" : "");
  const t = new Date((ev.ts || Date.now() / 1000) * 1000).toLocaleTimeString();
  let detail = "";
  if (ev.spec) detail = `${(ev.spec.parts || []).length} parts`;
  else if (ev.overall?.dimensions) detail = ev.overall.dimensions.map((d) => d.toFixed(3)).join(" × ") + " m";
  else if (ev.feedback) detail = String(ev.feedback).slice(0, 120);
  else if (ev.error) detail = String(ev.error).slice(0, 160);
  else if (ev.views) detail = Object.keys(ev.views).join(", ");
  else if (ev.parts) detail = ev.parts.join(", ");
  else if (ev.message) detail = String(ev.message).slice(0, 160);
  line.innerHTML = `<span class="t">${t}</span>${escapeHtml(ev.event)}${detail ? "  ·  " + escapeHtml(detail) : ""}`;
  view.appendChild(line);
  view.scrollTop = view.scrollHeight;
}

// ── viewer / tabs ────────────────────────────────────────────────────────

function initViewer() {
  state.viewer = new ModelViewer($("#viewer-container"));
}

async function loadModel(url) {
  switchTab("view3d");
  try {
    await state.viewer.load(url);
  } catch (e) {
    console.error("viewer load failed", e);
  }
}

function initTabs() {
  $$("#output-tabs .tab").forEach((tab) =>
    tab.addEventListener("click", () => switchTab(tab.dataset.tab))
  );
}

function switchTab(name) {
  $$("#output-tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${name}`));
  if (name === "view3d" && state.viewer) state.viewer._resize();
}

// ── runs view ────────────────────────────────────────────────────────────

async function refreshRunsCount() {
  try {
    const runs = await api("/api/runs");
    $("#runs-count").textContent = runs.length || "";
  } catch {}
}

function initRunsView() {
  $("#btn-refresh-runs").addEventListener("click", refreshRunsTable);
}

async function refreshRunsTable() {
  const tbody = $("#runs-tbody");
  tbody.innerHTML = `<tr><td colspan="8" class="tl-detail">loading…</td></tr>`;
  try {
    const runs = await api("/api/runs");
    refreshRunsCount();
    if (!runs.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="tl-detail">no runs yet — build something!</td></tr>`;
      return;
    }
    tbody.innerHTML = "";
    runs.forEach((r) => {
      const st = r.status || (r.live ? "running" : "unknown");
      const cls = st === "completed" ? "ok" : ["failed", "budget_exhausted", "iteration_cap_exhausted"].includes(st) ? "bad" : st === "running" ? "info" : "warn";
      const dims = (r.dimensions_m || []).map((d) => (d ?? 0).toFixed(2)).join(" × ");
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="run-id-cell">${escapeHtml(r.run_id || "")}</td>
        <td>${escapeHtml(r.model_name || r.label || "—")}</td>
        <td><span class="chip ${cls}">${st.replace(/_/g, " ")}${r.live ? " ●" : ""}</span></td>
        <td class="mono">${dims || "—"}</td>
        <td class="mono">${r.metrics?.iterations ?? "—"}</td>
        <td class="mono">${r.tri_count != null ? r.tri_count.toLocaleString() : "—"}</td>
        <td class="mono">${new Date((r.created_at || 0) * 1000).toLocaleString()}</td>
        <td><button class="btn ghost sm">Open →</button></td>`;
      tr.addEventListener("click", () => {
        $$("#nav .nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === "build"));
        $$(".view").forEach((v) => v.classList.add("hidden"));
        $("#view-build").classList.remove("hidden");
        openRun(r.run_id);
      });
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="tl-detail">failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

// ── delivery view (T5): job intake + compliance panel ────────────────────

function initDeliveryView() {
  $("#btn-refresh-jobs").addEventListener("click", refreshJobsTable);
  $("#btn-refresh-packages").addEventListener("click", refreshPackagesTable);
  $("#btn-create-job").addEventListener("click", createJob);
  $("#dj-placeholder").addEventListener("change", (e) => {
    // rule 9 must be unmissable when stand-ins are chosen
    e.target.closest(".checkbox-field").classList.toggle("danger", e.target.checked);
  });
}

async function refreshDelivery() {
  await Promise.all([loadTemplateOptions(), refreshJobsTable(), refreshPackagesTable()]);
}

async function loadTemplateOptions() {
  const sel = $("#dj-template");
  if (sel.options.length > 1) return; // already loaded
  try {
    const list = await api("/api/templates");
    sel.innerHTML =
      `<option value="">— manual product_class —</option>` +
      list
        .map((t) =>
          t.error
            ? `<option value="" disabled>${escapeHtml(t.file)} (invalid)</option>`
            : `<option value="${escapeHtml(t.product_class)}">${escapeHtml(t.file)} — ${escapeHtml((t.description || "").slice(0, 70))}</option>`
        )
        .join("");
  } catch (e) {
    sel.innerHTML = `<option value="">load failed</option>`;
  }
}

async function refreshJobsTable() {
  const tbody = $("#jobs-tbody");
  tbody.innerHTML = `<tr><td colspan="3" class="tl-detail">loading…</td></tr>`;
  try {
    const jobs = await api("/api/jobs");
    if (!jobs.length) {
      tbody.innerHTML = `<tr><td colspan="3" class="tl-detail">no job cards — create one on the left</td></tr>`;
      return;
    }
    tbody.innerHTML = "";
    jobs.forEach((j) => {
      if (j.error) {
        tbody.insertAdjacentHTML("beforeend",
          `<tr><td class="mono">${escapeHtml(j.file)}</td><td colspan="2" class="fail">${escapeHtml(j.error)}</td></tr>`);
        return;
      }
      const dims = j.dims
        ? `${j.dims.length} × ${j.dims.width} × ${j.dims.height} ${j.dims.unit}`
        : "—";
      const badge = j.dims_placeholder
        ? ` <span class="chip bad" title="delivery refused until real dims arrive">placeholder</span>`
        : "";
      tbody.insertAdjacentHTML("beforeend",
        `<tr><td class="mono">${escapeHtml(j.job_code)}</td>` +
        `<td class="mono">${escapeHtml(dims)}${badge}</td>` +
        `<td>${escapeHtml(j.product_class || "—")}</td></tr>`);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="3" class="tl-detail">failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

async function refreshPackagesTable() {
  const tbody = $("#packages-tbody");
  tbody.innerHTML = `<tr><td colspan="4" class="tl-detail">loading…</td></tr>`;
  $("#package-detail").innerHTML = "";
  try {
    const pkgs = await api("/api/packages");
    if (!pkgs.length) {
      tbody.innerHTML = `<tr><td colspan="4" class="tl-detail">no packages yet — run <span class="mono">python -m src.cli package --template … --job …</span></td></tr>`;
      return;
    }
    tbody.innerHTML = "";
    pkgs.forEach((p) => {
      const verdict = p.refused
        ? `<span class="chip bad">REFUSED</span>`
        : p.all_passed
          ? `<span class="chip ok">all passed</span>`
          : `<span class="chip bad">failed</span>`;
      const gates = p.gates_total != null ? `${p.gates_passed}/${p.gates_total}` : "—";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="mono">${escapeHtml(p.job_code)}</td>
        <td>${p.kind === "blocked" ? '<span class="chip warn">blocked</span>' : "package"}</td>
        <td class="mono">${gates}</td>
        <td>${verdict}</td>`;
      tr.addEventListener("click", () => showPackage(p.job_code, p.kind));
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="tl-detail">failed to load: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function gateRows(gates) {
  if (!Array.isArray(gates)) return `<div class="tl-detail">no gates recorded</div>`;
  const rows = gates
    .map(
      (g) => `<tr>
        <td>${escapeHtml(g.gate || "?")}</td>
        <td class="mono">${escapeHtml(g.expected ?? "—")}</td>
        <td class="mono">${escapeHtml(g.received ?? "—")}</td>
        <td class="${g.passed ? "pass" : "fail"}">${g.passed ? "PASS" : "FAIL"}</td>
      </tr>`
    )
    .join("");
  return `<table class="gate-table">
    <thead><tr><th>Gate</th><th>Expected</th><th>Received</th><th></th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function showPackage(jobCode, kind) {
  const detail = $("#package-detail");
  detail.innerHTML = `<div class="tl-detail">loading ${escapeHtml(jobCode)}…</div>`;
  try {
    const { report } = await api(`/api/packages/${encodeURIComponent(jobCode)}`);
    let html = "";

    if (report.refused) {
      html += `<div class="refusal-box">
        <strong>REFUSED — placeholder dimensions</strong>
        <p>${escapeHtml(report.refusal_reason || "")}</p>
        <p class="tl-detail">Unblock: ${escapeHtml(report.unblock || "supply real dimensions in the job card")}</p>
      </div>`;
    }

    html += `<div class="gate-section">
      <div class="gate-title">Client Validator (local mirror)
        <span class="chip ${report.all_passed ? "ok" : "bad"}">${report.all_passed ? "all passed" : "failed"}</span>
      </div>
      ${gateRows(report.gates)}
    </div>`;

    const fin = report.finish || {};
    if (Object.keys(fin).length) {
      const uv = fin.uv_diagnostics || {};
      html += `<div class="gate-section">
        <div class="gate-title">Finish pipeline</div>
        <div class="sys-row"><span class="k">FBX source</span><span class="v mono">${escapeHtml(fin.fbx_source || "—")}</span></div>
        <div class="sys-row"><span class="k">LP / HP tri-eq</span><span class="v mono">${(fin.lp_tri_equivalent ?? "—").toLocaleString?.() ?? "—"} / ${(fin.hp_tri_equivalent ?? "—").toLocaleString?.() ?? "—"} (budget ${fin.lp_budget ?? "—"})</span></div>
        <div class="sys-row"><span class="k">Texture resolution</span><span class="v mono">${fin.texture_resolution ?? "—"}</span></div>
        <div class="sys-row"><span class="k">UV islands / overlaps</span><span class="v mono">${uv.islands_total ?? "—"} / ${uv.overlapping_island_pairs ?? "—"}</span></div>
        <div class="sys-row"><span class="k">Detail parts</span><span class="v mono">${escapeHtml(Object.keys(fin.detail_parts || {}).join(", ") || "—")}</span></div>
      </div>`;
      if (Array.isArray(fin.review_renders) && fin.review_renders.length) {
        html += `<div class="gate-section"><div class="gate-title">Review renders (awaiting owner review)</div>
          <div class="tl-detail">${fin.review_renders.map(escapeHtml).join("<br>")}</div></div>`;
      }
    }

    const ph = report.placeholders;
    if (ph && (ph.lp_hp_single_source || ph.textures_synthetic)) {
      html += `<div class="refusal-box warn">
        <strong>T3 placeholders present</strong>
        <p class="tl-detail">${escapeHtml(ph.detail || "")}</p>
      </div>`;
    }

    if (kind !== "blocked" && !report.refused) {
      html += `<div class="form-actions">
        <button id="btn-revalidate" class="btn ghost">↻ Re-run validator</button>
        <span id="revalidate-note" class="build-note"></span>
      </div>`;
    }

    detail.innerHTML = html;
    const btn = $("#btn-revalidate");
    if (btn) btn.addEventListener("click", () => revalidatePackage(jobCode));
  } catch (e) {
    detail.innerHTML = `<div class="tl-detail">failed to load: ${escapeHtml(e.message)}</div>`;
  }
}

async function revalidatePackage(jobCode) {
  const note = $("#revalidate-note");
  note.textContent = "running (fresh Blender process for mesh facts)…";
  try {
    const res = await api(`/api/packages/${encodeURIComponent(jobCode)}/validate`, { method: "POST" });
    const detail = $("#package-detail");
    detail.insertAdjacentHTML(
      "afterbegin",
      `<div class="gate-section">
        <div class="gate-title">Live re-validation
          <span class="chip ${res.all_passed ? "ok" : "bad"}">${res.all_passed ? "all passed" : "failed"}</span>
          ${res.blender_facts ? "" : '<span class="chip warn">no mesh facts (Blender unavailable — mesh gates fail closed)</span>'}
        </div>
        ${gateRows(res.gates)}
      </div>`
    );
    note.textContent = res.all_passed ? "all gates green ✓" : "gate failures above";
    toast(`Re-validated ${jobCode}: ${res.all_passed ? "PASS" : "FAIL"}`, res.all_passed ? "ok" : "bad");
  } catch (e) {
    note.textContent = "";
    toast(`Re-validation failed: ${e.message}`, "bad");
  }
}

async function createJob() {
  const note = $("#job-note");
  const code = $("#dj-code").value.trim();
  const l = $("#dj-l").value, w = $("#dj-w").value, h = $("#dj-h").value;
  const unit = $("#dj-unit").value;
  const placeholder = $("#dj-placeholder").checked;

  if (!code) return toast("Job code is required", "bad");
  if (!l || !w || !h || !unit) {
    return toast("Dimensions are required with an explicit unit — never inferred (rule 9)", "bad");
  }
  if (placeholder && !confirm("Save with placeholder stand-in dimensions? NO deliverable package will be emitted for this job until real dimensions replace them.")) {
    return;
  }

  note.textContent = "saving…";
  try {
    const tplVal = $("#dj-template").value;
    const res = await api("/api/jobs", {
      method: "POST",
      body: {
        job_code: code,
        dims: { length: parseFloat(l), width: parseFloat(w), height: parseFloat(h), unit },
        dims_placeholder: placeholder,
        complexity: $("#dj-complexity").value,
        orientation: $("#dj-orientation").value,
        product_class: tplVal || "generic",
        part_scope: $("#dj-scope").value.trim(),
        reference_dir: $("#dj-refdir").value.trim() || undefined,
      },
    });
    note.textContent = "";
    toast(`Job card saved: ${res.path}${res.dims_placeholder ? " (placeholder — delivery will be REFUSED)" : ""}`, "ok");
    refreshJobsTable();
  } catch (e) {
    note.textContent = "";
    toast(`Save failed: ${e.message}`, "bad");
  }
}

// ── system view ──────────────────────────────────────────────────────────

async function renderSystemView() {
  if (!lastHealth) await refreshHealth();
  const h = lastHealth;
  if (!h) return;

  const rows = (panel, list) => {
    $(`#sys-${panel} .sys-body`).innerHTML = list
      .map(([k, v]) => `<div class="sys-row"><span class="k">${k}</span><span class="v">${escapeHtml(String(v))}</span></div>`)
      .join("");
  };

  rows("blender", [
    ["Available", h.blender.available ? "yes" : "no"],
    ["Version", h.blender.version || "—"],
    ["Path", h.blender.path || "—"],
  ]);
  rows("ai", [
    ["Endpoint", h.ai.endpoint],
    ["Model", h.ai.model],
    ["Healthy", h.ai.healthy ? "yes" : "no"],
    ["Reasoning effort", h.config.reasoning_effort || "default"],
    ["Max tokens", h.config.max_tokens || "—"],
  ]);
  rows("agent", [
    ["Max iterations", h.agent.max_iterations],
    ["Wall-clock budget", `${h.agent.wall_clock_budget_s}s`],
  ]);
  const vision = $("#sys-vision .sys-body");
  if (h.ai.vision_supported) {
    vision.innerHTML = `<div class="sys-row"><span class="k">Status</span><span class="v" style="color:var(--green)">active</span></div>`;
  } else {
    vision.innerHTML = `
      <div class="sys-row"><span class="k">Status</span><span class="v" style="color:var(--amber)">text-only</span></div>
      <div class="sys-note">The Aptos GLM-5.3 endpoint is not multimodal. Reference images are accepted and stored,
      but analysis falls back to text descriptions. Wire a local vision model (e.g. Qwen2.5-VL served via vLLM,
      OpenAI-compatible) into <code>config/ai.yaml</code> to enable reference analysis and the visual gate.</div>`;
  }
}

// ── misc ui ──────────────────────────────────────────────────────────────

function setRunStatus(cls, text) {
  const el = $("#run-status");
  if (!cls) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.className = `chip ${cls}`;
  el.textContent = text;
}

function openLightbox(url) {
  $("#lightbox-img").src = url;
  $("#lightbox").classList.remove("hidden");
}
$("#lightbox").addEventListener("click", () => $("#lightbox").classList.add("hidden"));

function toast(text, cls = "") {
  const t = document.createElement("div");
  t.className = `toast ${cls}`;
  t.textContent = text;
  $("#toasts").appendChild(t);
  setTimeout(() => {
    t.style.opacity = "0";
    t.style.transition = "opacity .4s";
    setTimeout(() => t.remove(), 400);
  }, 4200);
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ── go ───────────────────────────────────────────────────────────────────

boot();
