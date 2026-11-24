import { useEffect, useState } from "react";
import { fetchHealth } from "../api/dashboard";

function badgeClass(status) {
  const s = status?.toLowerCase() || "warn";
  if (s === "ok") return "status ok";
  if (s === "fail") return "status fail";
  return "status warn";
}

export function HealthStatus() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchHealth()
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  if (error) {
    return (
      <div className="card">
        <h3 className="title">Health</h3>
        <div className="status warn">Error: {error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="card">
        <h3 className="title">Health</h3>
        <div className="muted">Loading...</div>
      </div>
    );
  }

  return (
    <div className="card">
      <h3 className="title">Health</h3>
      <div className={badgeClass(data.status)}>Status: {data.status}</div>
      {data.issues && data.issues.length > 0 ? (
        <ul>
          {data.issues.map((i) => (
            <li key={i} className="muted">
              {i}
            </li>
          ))}
        </ul>
      ) : (
        <div className="muted">No issues detected.</div>
      )}
      <div className="stat-row">
        <span>Last heartbeat</span>
        <span className="muted">{data.last_heartbeat_at || "n/a"}</span>
      </div>
      <div className="stat-row">
        <span>Last snapshot</span>
        <span className="muted">{data.last_snapshot_at || "n/a"}</span>
      </div>
    </div>
  );
}
