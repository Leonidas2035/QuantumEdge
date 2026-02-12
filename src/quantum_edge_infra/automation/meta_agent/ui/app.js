const TOKEN = window.__CC_TOKEN__ || "";

async function apiFetch(path, options = {}) {
  const headers = options.headers || {};
  headers["X-CC-Token"] = TOKEN;
  headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || "Request failed");
  }
  return response.json();
}

function setHealth(ok) {
  const dot = document.getElementById("health-dot");
  const text = document.getElementById("health-text");
  if (ok) {
    dot.style.background = "#35f2a6";
    text.textContent = "Healthy";
  } else {
    dot.style.background = "#ff6b6b";
    text.textContent = "Unhealthy";
  }
}

function renderRecentRuns(runs) {
  const container = document.getElementById("recent-runs");
  if (!runs.length) {
    container.textContent = "No runs yet.";
    return;
  }
  container.innerHTML = runs
    .map((run) => `${run.run_id} (${run.verdict}, exit=${run.exit_code})`)
    .join("<br />");
}

function renderSchedulerSummary(items) {
  const container = document.getElementById("scheduler-summary");
  if (!items.length) {
    container.textContent = "No schedules found.";
    return;
  }
  container.innerHTML = items
    .map(
      (s) =>
        `${s.schedule_id} enabled=${s.enabled} next=${s.next_eligible_at || "-"} attempts=${s.attempts || 0}`
    )
    .join("<br />");
}

function renderRunsTable(runs) {
  const table = document.getElementById("runs-table");
  table.innerHTML = "";
  const header = document.createElement("div");
  header.className = "table-row header";
  header.innerHTML = "<div>Run</div><div>Task</div><div>Verdict</div><div>Exit</div><div>Applied</div>";
  table.appendChild(header);

  runs.forEach((run) => {
    const row = document.createElement("div");
    row.className = "table-row";
    row.innerHTML = `<div>${run.run_id}</div><div>${run.task_id || "-"}</div><div>${run.verdict}</div><div>${run.exit_code}</div><div>${run.applied}</div>`;
    row.addEventListener("click", () => loadRunDetail(run.run_id));
    table.appendChild(row);
  });
}

async function loadDashboard() {
  const [health, status] = await Promise.all([apiFetch("/api/health"), apiFetch("/api/status")]);
  setHealth(health.ok);
  renderRecentRuns(status.recent_runs || []);
  renderSchedulerSummary(status.scheduler || []);
  initProjectSelectors(status.projects || [], status.active_project);
}

function initProjectSelectors(projects, activeId) {
  const select = document.getElementById("active-project-select");
  const taskSelect = document.getElementById("task-project");
  [select, taskSelect].forEach((el) => (el.innerHTML = ""));

  projects.forEach((proj) => {
    const option = document.createElement("option");
    option.value = proj.id;
    option.textContent = `${proj.label} (${proj.id})`;
    if (proj.id === activeId) {
      option.selected = true;
    }
    select.appendChild(option.cloneNode(true));
    taskSelect.appendChild(option);
  });

  document.getElementById("active-project-text").textContent = `Active project: ${activeId || "--"}`;
  const warning = document.getElementById("project-warning");
  const active = projects.find((p) => p.id === activeId);
  if (active && active.root_exists === false) {
    warning.textContent = "Active project root missing; tasks will still enqueue but may fail.";
  } else {
    warning.textContent = "";
  }
}

async function updateActiveProject() {
  const select = document.getElementById("active-project-select");
  const projectId = select.value;
  await apiFetch("/api/active-project", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId }),
  });
  document.getElementById("active-project-text").textContent = `Active project: ${projectId}`;
}

async function submitTask(event) {
  event.preventDefault();
  const form = event.target;
  const payload = {
    objective: form.objective.value.trim(),
    instructions: form.instructions.value.trim(),
    project_id: form.project_id.value || "monorepo",
    llm: { model: form.model.value.trim() || undefined },
    constraints: { patch_only: form.patch_only.checked },
    execution: { dry_run: form.dry_run.checked },
    gates_preset: form.gates_preset.checked,
    context: {},
  };

  if (form.include_globs.value.trim()) {
    payload.context.include_globs = form.include_globs.value.split(",").map((v) => v.trim()).filter(Boolean);
  }
  if (form.focus_files.value.trim()) {
    payload.context.focus_files = form.focus_files.value.split(",").map((v) => v.trim()).filter(Boolean);
  }
  if (form.deny_globs.value.trim()) {
    payload.constraints.deny_globs = form.deny_globs.value.split(",").map((v) => v.trim()).filter(Boolean);
  }

  const result = await apiFetch("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
  const target = document.getElementById("task-result");
  target.textContent = `Enqueued ${result.filename}`;
  form.reset();
}

async function loadRuns() {
  const verdict = document.getElementById("runs-filter").value;
  const runs = await apiFetch(`/api/runs?limit=50&verdict=${verdict}`);
  const filtered = filterRuns(runs.runs || []);
  renderRunsTable(filtered);
}

function filterRuns(runs) {
  const query = document.getElementById("runs-search").value.trim().toLowerCase();
  if (!query) return runs;
  return runs.filter(
    (run) =>
      String(run.run_id).toLowerCase().includes(query) ||
      String(run.task_id || "").toLowerCase().includes(query)
  );
}

async function loadRunDetail(runId) {
  const detail = await apiFetch(`/api/run/${runId}`);
  const report = detail.report || {};
  const container = document.getElementById("run-detail-body");
  container.innerHTML = "";
  document.getElementById("run-detail-title").textContent = `Run ${runId}`;

  const summary = document.createElement("div");
  summary.className = "panel";
  summary.innerHTML = `
    <div class="tiny muted">Summary</div>
    <div>${report.summary || "-"}</div>
    <div class="tiny muted">Verdict: ${report.verdict} | exit_code=${report.exit_code}</div>
    <div class="tiny muted">Applied: ${(report.changes || {}).applied}</div>
    <div class="tiny muted">Report: ${(report.artifacts || {}).report_path}</div>
    <div class="tiny muted">Patches: ${(report.artifacts || {}).patches_dir}</div>
  `;
  container.appendChild(summary);

  const patches = document.createElement("div");
  patches.className = "panel";
  patches.innerHTML = `<div class="tiny muted">Patches</div>`;
  (detail.patch_files || []).forEach((file) => {
    const link = document.createElement("button");
    link.className = "ghost";
    link.textContent = file;
    link.addEventListener("click", () => fetchPatch(runId, file));
    patches.appendChild(link);
  });
  container.appendChild(patches);

  const gates = document.createElement("div");
  gates.className = "panel";
  gates.innerHTML = `<div class="tiny muted">Gate outputs</div>`;
  (detail.gate_files || []).forEach((file) => {
    const link = document.createElement("button");
    link.className = "ghost";
    link.textContent = file;
    link.addEventListener("click", () => fetchGate(runId, file));
    gates.appendChild(link);
  });
  (detail.approval_gate_files || []).forEach((file) => {
    const link = document.createElement("button");
    link.className = "ghost";
    link.textContent = file;
    link.addEventListener("click", () => fetchGate(runId, file));
    gates.appendChild(link);
  });
  container.appendChild(gates);

  if (report.verdict === "warn" && !(report.changes || {}).applied) {
    const approve = document.createElement("button");
    approve.className = "primary";
    approve.textContent = "Approve & Apply";
    approve.addEventListener("click", () => approveApply(runId));
    container.appendChild(approve);
  }
}

async function fetchPatch(runId, file) {
  const response = await fetch(`/api/run/${runId}/patch/${encodeURIComponent(file)}`, {
    headers: { "X-CC-Token": TOKEN },
  });
  const text = await response.text();
  showModal("Patch", text);
}

async function fetchGate(runId, file) {
  const response = await fetch(`/api/run/${runId}/gate/${encodeURIComponent(file)}`, {
    headers: { "X-CC-Token": TOKEN },
  });
  const text = await response.text();
  showModal("Gate output", text);
}

function showModal(title, content) {
  const modal = document.createElement("div");
  modal.className = "modal";
  modal.innerHTML = `
    <div class="modal__inner">
      <h3>${title}</h3>
      <pre>${content}</pre>
      <button class="ghost">Close</button>
    </div>
  `;
  modal.querySelector("button").addEventListener("click", () => modal.remove());
  document.body.appendChild(modal);
}

async function approveApply(runId) {
  try {
    const result = await apiFetch(`/api/run/${runId}/approve-apply`, { method: "POST", body: "{}" });
    alert(result.message || "Applied.");
    await loadRuns();
    await loadRunDetail(runId);
  } catch (err) {
    alert(`Approve/apply failed: ${err.message}`);
  }
}

async function loadSchedules() {
  const data = await apiFetch("/api/schedules");
  const container = document.getElementById("schedules-list");
  container.innerHTML = "";
  (data.schedules || []).forEach((sched) => {
    const row = document.createElement("div");
    row.className = "panel";
    row.innerHTML = `
      <div class="row">
        <strong>${sched.schedule_id}</strong>
        <span class="tiny muted">${sched.timezone}</span>
        <span class="tiny muted">next=${sched.next_eligible_at || "-"}</span>
        <span class="tiny muted">attempts=${sched.attempts || 0}</span>
      </div>
    `;
    const toggle = document.createElement("button");
    toggle.className = "ghost";
    toggle.textContent = sched.enabled ? "Disable" : "Enable";
    toggle.addEventListener("click", () => toggleSchedule(sched.schedule_id, !sched.enabled));
    row.appendChild(toggle);
    container.appendChild(row);
  });
}

async function toggleSchedule(scheduleId, enabled) {
  await apiFetch(`/api/schedule/${scheduleId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
  await loadSchedules();
}

function init() {
  document.getElementById("task-form").addEventListener("submit", submitTask);
  document.getElementById("refresh-runs").addEventListener("click", loadRuns);
  document.getElementById("refresh-dashboard").addEventListener("click", loadDashboard);
  document.getElementById("refresh-schedules").addEventListener("click", loadSchedules);
  document.getElementById("set-active-project").addEventListener("click", updateActiveProject);
  document.getElementById("runs-search").addEventListener("input", loadRuns);
  document.getElementById("runs-filter").addEventListener("change", loadRuns);

  loadDashboard();
  loadRuns();
  loadSchedules();
}

document.addEventListener("DOMContentLoaded", init);
