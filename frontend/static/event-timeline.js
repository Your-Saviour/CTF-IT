import { clampOffset, effectiveMinutes } from "./event-timeline-state.js";

const app = document.querySelector(".timeline-app");
const eventId = app.dataset.eventId;
const readOnly = app.dataset.readOnly === "true";
const PX_PER_MIN = 6;
const canvas = document.getElementById("timeline-canvas");
const scaleEl = document.getElementById("timeline-scale");
const messageEl = document.getElementById("timeline-message");
const injectListEl = document.getElementById("inject-list");
const injectDialog = document.getElementById("inject-dialog");
const injectForm = document.getElementById("inject-form");
const healthDialog = document.getElementById("health-dialog");

let eventMinutes = Number(app.dataset.eventMinutes) || 120;
let updatedAt = null;
let timeline = { version: 1, phases: [], injects: [] };
let operations = [];
let modules = [];
let vms = [];
let editingInjectId = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function setMessage(text) {
  messageEl.textContent = text || "";
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || "Request failed");
  return body;
}

function operationOffset(op) {
  if (op.trigger_type !== "scheduled_trigger") return null;
  return op.offset_minutes ?? null;
}

async function loadData() {
  const [timelineBody, opsBody, moduleBody] = await Promise.all([
    fetchJson(`/admin/api/events/${eventId}/timeline`),
    fetchJson(`/admin/api/events/${eventId}/operations`),
    fetchJson(`/admin/api/events/${eventId}/module-plan`),
  ]);
  timeline = timelineBody.timeline;
  updatedAt = timelineBody.updated_at;
  operations = opsBody.operations || [];
  modules = moduleBody.modules || [];
  vms = moduleBody.vms || [];

  const scheduled = operations.filter((op) => op.trigger_type === "scheduled_trigger");
  const plans = await Promise.all(scheduled.map((op) =>
    fetchJson(`/admin/api/events/${eventId}/operations/${op.id}/plan`).catch(() => null)
  ));
  operations = operations.map((op) => {
    const plan = plans[scheduled.findIndex((s) => s.id === op.id)];
    if (plan && plan.operation_plan) {
      const trigger = (plan.operation_plan.nodes || []).find(
        (node) => node.type === "scheduled_trigger" && !node.disabled
      );
      op.offset_minutes = trigger ? trigger.config.offset_minutes : null;
      op.duration = (plan.operation_plan.policy || {}).time_limit_minutes || 0;
    }
    return op;
  });

  eventMinutes = effectiveMinutes(timeline.injects, timeline.phases, operations, eventMinutes);
  render();
  renderInjectList();
}

function renderScale() {
  scaleEl.innerHTML = "";
  const width = eventMinutes * PX_PER_MIN;
  const step = Math.max(15, Math.ceil(eventMinutes / 24 / 15) * 15);
  for (let minute = 0; minute <= eventMinutes; minute += step) {
    const tick = document.createElement("span");
    tick.className = "tick";
    tick.style.left = `${minute * PX_PER_MIN}px`;
    tick.textContent = minute;
    scaleEl.appendChild(tick);
  }
}

function render() {
  renderScale();
  canvas.style.width = `${eventMinutes * PX_PER_MIN}px`;
  canvas.innerHTML = "";

  for (const phase of timeline.phases) {
    const band = document.createElement("div");
    band.className = "timeline-phase";
    band.style.left = `${phase.start_offset_minutes * PX_PER_MIN}px`;
    band.style.width = `${(phase.end_offset_minutes - phase.start_offset_minutes) * PX_PER_MIN}px`;
    band.style.background = phase.color;
    const label = document.createElement("span");
    label.className = "timeline-phase-label";
    label.textContent = phase.name;
    band.appendChild(label);
    canvas.appendChild(band);
  }

  for (const op of operations) {
    if (op.offset_minutes == null) continue;
    const bar = document.createElement("div");
    bar.className = "timeline-op";
    bar.style.left = `${op.offset_minutes * PX_PER_MIN}px`;
    bar.style.top = "40px";
    bar.style.width = `${Math.max(40, (op.duration || 30) * PX_PER_MIN)}px`;
    bar.textContent = op.name;
    bar.title = `${op.name} — T+${op.offset_minutes}m`;
    canvas.appendChild(bar);
  }

  timeline.injects.forEach((inject, index) => {
    const marker = document.createElement("div");
    marker.className = "timeline-inject";
    marker.style.left = `${inject.offset_minutes * PX_PER_MIN}px`;
    marker.style.top = "120px";
    marker.title = `${inject.name} — T+${inject.offset_minutes}m`;
    const label = document.createElement("span");
    label.className = "timeline-inject-label";
    label.style.left = `${inject.offset_minutes * PX_PER_MIN}px`;
    label.style.top = "138px";
    label.textContent = inject.name;
    canvas.appendChild(marker);
    canvas.appendChild(label);

    marker.addEventListener("mousedown", (event) => startDrag(event, inject, marker, label));
    marker.addEventListener("dblclick", () => openInjectEditor(inject));
  });
}

function startDrag(event, inject, marker, label) {
  if (readOnly) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const move = (ev) => {
    const minute = clampOffset((ev.clientX - rect.left) / PX_PER_MIN, eventMinutes);
    inject.offset_minutes = minute;
    marker.style.left = `${minute * PX_PER_MIN}px`;
    label.style.left = `${minute * PX_PER_MIN}px`;
    label.textContent = `${inject.name} T+${minute}m`;
  };
  const up = () => {
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    saveTimeline();
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

async function saveTimeline() {
  try {
    const body = await fetchJson(`/admin/api/events/${eventId}/timeline`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timeline, expected_updated_at: updatedAt }),
    });
    updatedAt = body.updated_at;
    setMessage("Saved");
    renderInjectList();
  } catch (error) {
    setMessage(error.message);
    if (error.message.includes("changed")) await loadData();
  }
}

function renderInjectList() {
  injectListEl.innerHTML = "";
  for (const inject of timeline.injects) {
    const item = document.createElement("li");
    item.innerHTML = `<span>${esc(inject.name)}</span><span class="timeline-inject-offset">T+${inject.offset_minutes}m</span>`;
    item.addEventListener("click", () => openInjectEditor(inject));
    injectListEl.appendChild(item);
  }
}

function openInjectEditor(inject) {
  editingInjectId = inject ? inject.id : null;
  injectDialog.querySelector("#inject-dialog-title").textContent = inject ? "Edit inject" : "New inject";
  injectForm["inject-name"].value = inject ? inject.name : "";
  injectForm["inject-offset"].value = inject ? inject.offset_minutes : 0;
  injectForm["inject-kind"].value = inject ? inject.kind : "apply_module";
  injectForm["inject-description"].value = inject ? (inject.description || "") : "";
  const payload = inject ? (inject.payload || {}) : {};
  injectForm["inject-module"].value = payload.module_id || "";
  injectForm["inject-target"].value = payload.target || "";
  injectForm["inject-operation"].value = payload.operation || "";
  injectForm["inject-severity"].value = payload.severity || "info";
  injectForm["inject-message"].value = payload.message || "";
  document.getElementById("inject-error").textContent = "";
  populateInjectOptions();
  syncInjectPayloadVisibility();
  injectDialog.showModal();
}

function populateInjectOptions() {
  const moduleSelect = injectForm["inject-module"];
  moduleSelect.innerHTML = "";
  for (const module of modules) {
    const option = document.createElement("option");
    option.value = module.id;
    option.textContent = module.name;
    moduleSelect.appendChild(option);
  }
  const targetSelect = injectForm["inject-target"];
  targetSelect.innerHTML = "";
  for (const vm of vms) {
    const option = document.createElement("option");
    option.value = vm.id;
    option.textContent = `${vm.name} (${vm.id})`;
    targetSelect.appendChild(option);
  }
  const opSelect = injectForm["inject-operation"];
  opSelect.innerHTML = "";
  for (const op of operations) {
    const option = document.createElement("option");
    option.value = op.name;
    option.textContent = op.name;
    opSelect.appendChild(option);
  }
}

function syncInjectPayloadVisibility() {
  const kind = injectForm["inject-kind"].value;
  document.getElementById("inject-payload-module").hidden = kind !== "apply_module";
  document.getElementById("inject-payload-operation").hidden = kind !== "start_operation";
  document.getElementById("inject-payload-notify").hidden = kind !== "notify";
}

injectForm["inject-kind"].addEventListener("change", syncInjectPayloadVisibility);
injectDialog.querySelectorAll("[data-close-inject]").forEach((button) =>
  button.addEventListener("click", () => injectDialog.close())
);

injectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const kind = injectForm["inject-kind"].value;
  const payload = {};
  if (kind === "apply_module") {
    payload.module_id = injectForm["inject-module"].value;
    payload.target = injectForm["inject-target"].value;
  } else if (kind === "start_operation") {
    payload.operation = injectForm["inject-operation"].value;
  } else if (kind === "notify") {
    payload.severity = injectForm["inject-severity"].value;
    payload.message = injectForm["inject-message"].value;
  }
  const data = {
    id: editingInjectId || `inject:${Date.now()}`,
    name: injectForm["inject-name"].value.trim(),
    offset_minutes: Number(injectForm["inject-offset"].value),
    kind,
    payload,
    description: injectForm["inject-description"].value.trim(),
  };
  if (editingInjectId) {
    const index = timeline.injects.findIndex((i) => i.id === editingInjectId);
    if (index >= 0) timeline.injects[index] = data;
  } else {
    timeline.injects.push(data);
  }
  injectDialog.close();
  render();
  renderInjectList();
  await saveTimeline();
});

document.getElementById("inject-add").addEventListener("click", () => openInjectEditor(null));
document.getElementById("timeline-health").addEventListener("click", async () => {
  healthDialog.showModal();
  const content = document.getElementById("health-content");
  content.innerHTML = "<p>Loading…</p>";
  const body = await fetchJson(`/admin/api/events/${eventId}/plan-health`);
  const sections = [
    ["Module issues", body.module_issues],
    ["Timeline issues", body.timeline_issues],
    ["Operation issues", (body.operation_issues || []).flatMap((o) => o.issues)],
  ];
  content.innerHTML = "";
  let empty = true;
  for (const [title, issues] of sections) {
    if (!issues || issues.length === 0) continue;
    empty = false;
    const block = document.createElement("div");
    block.className = "health-issues";
    block.innerHTML = `<h3>${esc(title)}</h3>`;
    const list = document.createElement("ul");
    for (const issue of issues) {
      const item = document.createElement("li");
      item.textContent = `${issue.code}: ${issue.message}`;
      list.appendChild(item);
    }
    block.appendChild(list);
    content.appendChild(block);
  }
  if (empty) content.innerHTML = '<p class="timeline-message">No planning issues detected.</p>';
});

loadData();
