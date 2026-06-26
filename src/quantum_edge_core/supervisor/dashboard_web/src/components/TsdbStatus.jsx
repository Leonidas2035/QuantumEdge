import { useEffect, useState } from "react";
import { fetchTsdbStatus } from "../api/dashboard";

function badge(status) {
  if (status === true || status === "true") return "status ok";
  return "status warn";
}

export function TsdbStatus() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchTsdbStatus()
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div className="card">
      <h3 className="title">TSDB Status</h3>
      {error && <div className="status warn">Error: {error}</div>}
      {!error && !data && <div className="muted">Loading...</div>}
      {data && (
        <>
          <div className="stat-row">
            <span>Backend</span>
            <strong>{data.backend || "none"}</strong>
          </div>
          <div className="stat-row">
            <span>Enabled</span>
            <span className={badge(data.enabled)}>{data.enabled ? "enabled" : "disabled"}</span>
          </div>
          <div className="stat-row">
            <span>Reachable</span>
            <span className={badge(data.reachable)}>{data.reachable ? "reachable" : "unreachable"}</span>
          </div>
          <div className="stat-row">
            <span>Queue depth</span>
            <strong>{data.queue_depth ?? 0}</strong>
          </div>
          <div className="stat-row">
            <span>Last write</span>
            <span className="muted">{data.last_write_at || "n/a"}</span>
          </div>
        </>
      )}
    </div>
  );
}
