const state = {
  token: "",
  symbol: "BTCUSDT",
  killSwitchEnabled: false,
  charts: {
    inference: "inference_p95_ms",
    tick: "tick_age_ms",
    book: "book_age_ms",
  },
};

function $(id) {
  return document.getElementById(id);
}

function setStatus(text, ok) {
  const el = $("connection-status");
  if (!el) return;
  el.textContent = text;
  el.className = ok ? "muted status-ok" : "muted status-fail";
}

function apiHeaders(isPost) {
  const headers = { "Content-Type": "application/json" };
  if (isPost && state.token) {
    headers["Authorization"] = `Bearer ${state.token}`;
  }
  return headers;
}

async function apiGet(path) {
  const res = await fetch(path, { headers: apiHeaders(false) });
  if (!res.ok) throw new Error(`GET ${path} ${res.status}`);
  return res.json();
}

async function apiPost(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: apiHeaders(true),
    body: JSON.stringify(payload || {}),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST ${path} ${res.status}: ${text}`);
  }
  return res.json();
}

function fmtAge(ms) {
  if (ms === null || ms === undefined) return "-";
  return `${ms} ms`;
}

function fmtBool(val) {
  return val ? "YES" : "NO";
}

function renderSummary(summary) {
  $("summary-mode").textContent = summary.summary?.mode || "-";
  const autopilot = summary.autopilot || {};
  $("summary-autopilot").textContent = `${autopilot.state || "-"} (${fmtBool(autopilot.enabled)})`;
  const health = autopilot.health?.status || "-";
  const healthEl = $("summary-health");
  healthEl.textContent = health;
  healthEl.className = `value ${health === "OK" ? "status-ok" : health === "WARN" ? "status-warn" : "status-fail"}`;
  $("summary-breaker").textContent = summary.summary?.breaker_active ? "ACTIVE" : "OK";
  $("summary-tick-age").textContent = fmtAge(summary.summary?.tick_age_ms);
  $("summary-book-age").textContent = fmtAge(summary.summary?.book_age_ms);
  const lag = summary.ingest?.event_lag_sec;
  $("summary-ingest-lag").textContent = lag !== undefined && lag !== null ? `${lag}s` : "-";
  const policy = summary.policy_current || {};
  $("summary-policy").textContent = policy.policy_hash ? policy.policy_hash.slice(0, 8) : "-";
  $("summary-updated").textContent = `Updated ${summary.ts || "-"}`;
  const killSwitch = summary.kill_switch || {};
  state.killSwitchEnabled = Boolean(killSwitch.enabled);
  const killBtn = $("kill-switch-toggle");
  if (killBtn) {
    killBtn.textContent = state.killSwitchEnabled ? "Disable Kill Switch" : "Enable Kill Switch";
  }
}

function renderOverview(overview) {
  $("overview-strategies").textContent = overview.strategies_total ?? "-";
  $("overview-alerts").textContent = overview.alerts_active ?? "-";
  const stale = overview.stale_telemetry || {};
  $("overview-stale").textContent = `${stale.count ?? "-"} (max ${stale.max_age_ms ?? "-"} ms)`;
  const perf = overview.performance || {};
  $("overview-pnl").textContent = perf.net_pnl !== undefined ? perf.net_pnl.toFixed(4) : "-";
  $("overview-updated").textContent = `Updated ${overview.ts_ms || "-"}`;
}

function renderList(containerId, items, formatter) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = "";
  if (!items || items.length === 0) {
    container.innerHTML = "<div class=\"muted\">No entries</div>";
    return;
  }
  items.forEach((item) => {
    const el = document.createElement("div");
    el.className = "list-item";
    el.innerHTML = formatter(item);
    container.appendChild(el);
  });
}

function renderAlerts(active, recent) {
  renderList("alerts-active", active, (alert) => {
    const meta = `${alert.rule} - ${alert.severity}`;
    return `<div><div>${alert.message || alert.rule}</div><div class="meta">${meta}</div></div>
      <div>
        <button data-ack="${alert.alert_id}">Ack</button>
        <button class="secondary" data-silence="${alert.rule}">Silence</button>
      </div>`;
  });
  renderList("alerts-recent", recent, (alert) => {
    return `<div><div>${alert.type} - ${alert.rule || ""}</div><div class="meta">${new Date(
      (alert.ts || 0) * 1000
    ).toISOString()}</div></div>`;
  });
}

function renderStrategies(strategies) {
  renderList("strategies-list", strategies, (item) => {
    const updates = item.last_update_ts_ms ? new Date(item.last_update_ts_ms).toISOString() : "-";
    const breaches = (item.limit_breaches || []).join(", ") || "none";
    return `<div><div>${item.strategy_id} / ${item.symbol}</div><div class="meta">Updated ${updates}</div></div>
      <div class="meta">Breaches: ${breaches}</div>`;
  });
}

function renderPerformance(perf) {
  const session = perf.session || {};
  renderList("performance-session", [session], (item) => {
    return `<div><div>Closed deals: ${item.closed_deals || 0}</div>
      <div class="meta">Net PnL: ${item.net_pnl || 0} | Wins: ${item.wins || 0} | Losses: ${item.losses || 0}</div></div>`;
  });
  renderList("performance-strategies", perf.by_strategy || [], (item) => {
    return `<div><div>${item.strategy_id} / ${item.symbol}</div><div class="meta">Deals: ${
      item.closed_deals || 0
    } Net: ${item.net_pnl || 0}</div></div>`;
  });
}

function renderEvents(containerId, events) {
  renderList(containerId, events, (ev) => {
    return `<div><div>${ev.event_type || ev.type || "EVENT"}</div><div class="meta">${
      ev.timestamp || ev.ts || ""
    }</div></div>`;
  });
}

function drawSparkline(containerId, rows, valueKey) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = "";
  if (!rows || rows.length === 0) {
    container.textContent = "No data";
    return;
  }
  const values = rows.map((row) => Number(row.value ?? row[valueKey] ?? 0)).filter((v) => !Number.isNaN(v));
  if (values.length === 0) {
    container.textContent = "No data";
    return;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = 260;
  const height = 80;
  const points = values.map((v, idx) => {
    const x = (idx / (values.length - 1)) * width;
    const y = height - ((v - min) / (max - min || 1)) * height;
    return `${x},${y}`;
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  polyline.setAttribute("fill", "none");
  polyline.setAttribute("stroke", "#36b37e");
  polyline.setAttribute("stroke-width", "2");
  polyline.setAttribute("points", points.join(" "));
  svg.appendChild(polyline);
  container.appendChild(svg);
}

async function refreshCharts() {
  const now = new Date();
  const end = now.toISOString();
  const start = new Date(now.getTime() - 60 * 60 * 1000).toISOString();
  const bucket = "30s";
  const metrics = [
    { id: "chart-inference", metric: state.charts.inference },
    { id: "chart-tick-age", metric: state.charts.tick },
    { id: "chart-book-age", metric: state.charts.book },
  ];
  for (const chart of metrics) {
    try {
      const data = await apiGet(
        `/api/v1/dashboard/timeseries?metric=${chart.metric}&symbol=${state.symbol}&from=${encodeURIComponent(
          start
        )}&to=${encodeURIComponent(end)}&bucket=${bucket}`
      );
      if (data.error) {
        $(chart.id).textContent = data.error;
      } else {
        drawSparkline(chart.id, data.rows || [], "value");
      }
    } catch (err) {
      $(chart.id).textContent = "TSDB unavailable";
    }
  }
}

async function refreshAll() {
  try {
    const overview = await apiGet("/api/v1/dashboard/overview");
    renderOverview(overview);
    const summary = await apiGet(`/api/v1/dashboard/summary?symbol=${state.symbol}`);
    renderSummary(summary);
    const strategies = await apiGet("/api/v1/dashboard/strategies");
    renderStrategies(strategies.strategies || []);
    const performance = await apiGet("/api/v1/dashboard/performance");
    renderPerformance(performance);
    setStatus("Connected", true);
    const alerts = await apiGet("/api/v1/dashboard/alerts");
    renderAlerts(alerts.active || [], alerts.recent || []);
    const events = await apiGet("/api/v1/dashboard/events/recent?limit=50");
    renderEvents("events-recent", events.events || []);
    const audit = await apiGet("/api/v1/dashboard/audit?limit=50");
    renderEvents("audit-recent", audit.items || []);
    await refreshCharts();
  } catch (err) {
    setStatus("Disconnected", false);
  }
}

function attachHandlers() {
  $("save-token").addEventListener("click", () => {
    state.token = $("auth-token").value.trim();
    sessionStorage.setItem("dashboard_token", state.token);
  });
  $("autopilot-enable").addEventListener("click", async () => {
    await apiPost("/api/v1/autopilot/enable", {});
    refreshAll();
  });
  $("autopilot-disable").addEventListener("click", async () => {
    await apiPost("/api/v1/autopilot/disable", {});
    refreshAll();
  });
  $("set-target").addEventListener("click", async () => {
    const stateVal = $("target-state").value;
    await apiPost("/api/v1/autopilot/target_state", { state: stateVal });
    refreshAll();
  });
  $("policy-rollout").addEventListener("click", async () => {
    const path = $("policy-path").value.trim();
    if (!path) return;
    await apiPost("/api/v1/policy/rollout", { symbol: state.symbol, path });
    refreshAll();
  });
  $("policy-rollback").addEventListener("click", async () => {
    await apiPost("/api/v1/policy/rollback", { symbol: state.symbol });
    refreshAll();
  });
  $("kill-switch-toggle").addEventListener("click", async () => {
    const challenge = await apiGet("/api/v1/safety/kill_switch");
    const target = !state.killSwitchEnabled;
    const confirmed = window.confirm(`Toggle kill switch ${target ? "ON" : "OFF"}?`);
    if (!confirmed) return;
    await apiPost("/api/v1/safety/kill_switch", {
      enabled: target,
      challenge_id: challenge.challenge_id,
    });
    refreshAll();
  });
  $("reset-counters").addEventListener("click", async () => {
    await apiPost("/api/v1/dashboard/reset-counters", {});
    refreshAll();
  });
  document.body.addEventListener("click", async (event) => {
    const target = event.target;
    if (target.dataset.ack) {
      await apiPost("/api/v1/alerts/ack", { alert_id: target.dataset.ack, note: "ack" });
      refreshAll();
    }
    if (target.dataset.silence) {
      await apiPost("/api/v1/alerts/silence", { rule: target.dataset.silence, minutes: 60 });
      refreshAll();
    }
  });
}

function init() {
  const stored = sessionStorage.getItem("dashboard_token");
  if (stored) {
    state.token = stored;
    $("auth-token").value = stored;
  }
  attachHandlers();
  refreshAll();
  setInterval(refreshAll, 10000);
}

window.addEventListener("load", init);
