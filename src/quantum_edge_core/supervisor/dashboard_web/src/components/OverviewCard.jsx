import { useEffect, useState } from "react";
import { fetchStatus } from "../api/dashboard";

export function OverviewCard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = () => {
      fetchStatus()
        .then((res) => {
          setData(res);
          setError(null);
        })
        .catch((err) => {
          console.error(err);
          setError("DISCONNECTED");
        });
    };

    load();
    const interval = setInterval(load, 1000);
    return () => clearInterval(interval);
  }, []);

  if (error) {
    return (
      <div className="card">
        <h3 className="title">System Status</h3>
        <div className="status warn">{error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="card">
        <h3 className="title">System Status</h3>
        <div className="muted">Connecting...</div>
      </div>
    );
  }

  const { policy, risk_verdict } = data;
  const isCritical = risk_verdict === "CRITICAL";

  // Policy Mode Colors
  const getPolicyColor = (mode) => {
    if (!mode) return "#888";
    const m = mode.toUpperCase();
    if (m === "SNIPER") return "#ef4444"; // Red
    if (m === "STANDARD") return "#3b82f6"; // Blue
    if (m === "CAUTIOUS") return "#eab308"; // Yellow
    return "#16a34a"; // Green default
  };

  return (
    <div className={`card ${isCritical ? "flash-critical" : ""}`}>
      <h3 className="title">System Overview</h3>

      <div className="stat-row">
        <span>Policy Mode</span>
        <strong style={{ color: getPolicyColor(policy?.mode) }}>
          {policy?.mode?.toUpperCase() || "UNKNOWN"}
        </strong>
      </div>

      <div className="stat-row">
        <span>Risk Verdict</span>
        <strong className={isCritical ? "text-red" : "text-green"}>
          {risk_verdict || "NORMAL"}
        </strong>
      </div>

      <div className="stat-row">
        <span>Leverage</span>
        <strong>{policy?.max_leverage ? `${policy.max_leverage}x` : "1.0x"}</strong>
      </div>

      <div className="stat-row">
        <span>Risk Mult</span>
        <strong>{policy?.risk_multiplier?.toFixed(2) || "1.00"}</strong>
      </div>
    </div>
  );
}
