import { useEffect, useMemo, useState } from "react";
import { fetchEvents } from "../api/dashboard";

const DEFAULT_TYPES = ["ORDER_DECISION", "ORDER_RESULT", "RISK_LIMIT_BREACH", "SUPERVISOR_SNAPSHOT", "STRATEGY_UPDATE"];
const REFRESH_MS = 15000;

export function EventsTable() {
  const [events, setEvents] = useState([]);
  const [limit, setLimit] = useState(100);
  const [types, setTypes] = useState(DEFAULT_TYPES);
  const [error, setError] = useState(null);

  const load = () => {
    fetchEvents({ limit, types })
      .then(setEvents)
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [limit, types]);

  const toggleType = (t) => {
    setTypes((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  };

  const sortedEvents = useMemo(() => {
    return [...events].sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1));
  }, [events]);

  return (
    <div className="card" style={{ gridColumn: "1 / -1" }}>
      <div className="events-controls">
        <strong>Events</strong>
        <label>
          Limit:
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} style={{ marginLeft: 6 }}>
            {[50, 100, 200].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {DEFAULT_TYPES.map((t) => (
            <label key={t} style={{ fontSize: 12 }}>
              <input type="checkbox" checked={types.includes(t)} onChange={() => toggleType(t)} /> {t}
            </label>
          ))}
        </div>
        {error && <span className="status warn">Error: {error}</span>}
      </div>
      <div style={{ maxHeight: 320, overflow: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Type</th>
              <th>Symbol</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {sortedEvents.map((ev, idx) => (
              <tr key={`${ev.timestamp}-${idx}`}>
                <td>{new Date(ev.timestamp).toLocaleString()}</td>
                <td>
                  <span className="badge">{ev.event_type}</span>
                </td>
                <td>{ev.symbol || "-"}</td>
                <td style={{ maxWidth: 420, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  <code style={{ fontSize: 12 }}>{JSON.stringify(ev.details)}</code>
                </td>
              </tr>
            ))}
            {sortedEvents.length === 0 && (
              <tr>
                <td colSpan={4} className="muted">
                  No events yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
