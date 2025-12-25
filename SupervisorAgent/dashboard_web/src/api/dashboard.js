const BASE = "";

async function handleResponse(res) {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
  }
  return res.json();
}

export async function fetchOverview() {
  const res = await fetch(`${BASE}/api/v1/dashboard/overview`);
  return handleResponse(res);
}

export async function fetchHealth() {
  const res = await fetch(`${BASE}/api/v1/dashboard/health`);
  return handleResponse(res);
}

export async function fetchEvents(params = {}) {
  const search = new URLSearchParams();
  if (params.limit) search.set("limit", String(params.limit));
  if (params.types && params.types.length) search.set("types", params.types.join(","));
  const qs = search.toString() ? `?${search.toString()}` : "";
  const res = await fetch(`${BASE}/api/v1/dashboard/events${qs}`);
  return handleResponse(res);
}

export async function fetchTsdbStatus() {
  const res = await fetch(`${BASE}/api/v1/tsdb/status`);
  return handleResponse(res);
}
