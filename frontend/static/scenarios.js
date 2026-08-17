const captureForm = document.getElementById("scenario-capture-form");
const captureMessage = document.getElementById("scenario-capture-message");
const sourceEventSelect = document.getElementById("scenario-source-event");
const scenariosList = document.getElementById("scenarios-list");
const scenariosCount = document.getElementById("scenarios-count");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function loadEvents() {
  const response = await fetch("/admin/api/events");
  if (!response.ok) return;
  const events = await response.json();
  sourceEventSelect.innerHTML = "";
  for (const event of events) {
    const option = document.createElement("option");
    option.value = event.id;
    option.textContent = `${event.name} (${event.status})`;
    sourceEventSelect.appendChild(option);
  }
}

async function loadScenarios() {
  const response = await fetch("/admin/api/scenarios");
  const body = await response.json();
  const scenarios = body.scenarios || [];
  scenariosCount.textContent = `${scenarios.length} scenario${scenarios.length === 1 ? "" : "s"}`;
  scenariosList.innerHTML = "";
  if (scenarios.length === 0) {
    scenariosList.innerHTML = '<p class="operations-message">No scenarios yet. Save an event as a scenario to get started.</p>';
    return;
  }
  for (const scenario of scenarios) {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${esc(scenario.name)} <span class="badge badge-medium">v${esc(scenario.version)}</span></h3>
      <p>${esc(scenario.description || "—")}</p>
      <p class="operations-step">Created ${esc(scenario.created_at)}</p>
      <footer style="display:flex;gap:0.5rem;margin-top:0.75rem;">
        <button class="btn btn-primary btn-sm" data-action="instantiate" data-id="${scenario.id}" data-name="${esc(scenario.name)}">Instantiate</button>
        <button class="btn btn-danger btn-sm" data-action="delete" data-id="${scenario.id}" data-name="${esc(scenario.name)}">Delete</button>
      </footer>`;
    scenariosList.appendChild(card);
  }
}

scenariosList.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, id, name } = button.dataset;
  if (action === "instantiate") {
    const eventName = window.prompt("Event name for the new draft event:", name) || name;
    const response = await fetch(`/admin/api/scenarios/${id}/instantiate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: eventName }),
    });
    const body = await response.json();
    if (!response.ok) {
      showToast(body.error || "Instantiation failed");
      return;
    }
    if (body.report && body.report.length) {
      showToast(`Instantiated with ${body.report.length} catalogue warning(s)`, 4000);
    } else {
      showToast("Event created");
    }
    window.location.href = `/admin/events/${body.event_id}/plan`;
  } else if (action === "delete") {
    if (!window.confirm(`Delete scenario "${name}"?`)) return;
    const response = await fetch(`/admin/api/scenarios/${id}`, { method: "DELETE" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      showToast(body.error || "Delete failed");
      return;
    }
    showToast("Scenario deleted");
    loadScenarios();
  }
});

captureForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  captureMessage.textContent = "";
  const payload = {
    event_id: Number(sourceEventSelect.value),
    name: document.getElementById("scenario-name").value.trim(),
    description: document.getElementById("scenario-description").value.trim() || null,
  };
  const response = await fetch("/admin/api/scenarios/from-event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    captureMessage.textContent = body.error || "Failed to save scenario";
    return;
  }
  captureMessage.textContent = `Saved "${body.name}" (v${body.version}).`;
  document.getElementById("scenario-name").value = "";
  document.getElementById("scenario-description").value = "";
  loadScenarios();
});

loadEvents();
loadScenarios();
