import { useEffect, useState } from "react";
import { fetchOverview } from "../api/dashboard";

export function OverviewCard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchOverview()
      .then(setData)
      .catch((err) => setError(err.message));
  }, []);

  if (error) {
    return (
      <div className="card">
        <h3 className="title">Overview</h3>
        <div className="status warn">Error: {error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="card">
        <h3 className="title">Overview</h3>
        <div className="muted">Loading...</div>
      </div>
    );
  }

  const pnlColor = data.total_pnl >= 0 ? "#16a34a" : "#dc2626";
  const pnl1hColor = data.pnl_1h >= 0 ? "#16a34a" : "#dc2626";

  return (
    <div className="card">
      <h3 className="title">Overview</h3>
      <div className="stat-row">
        <span>Total PnL</span>
        <strong style={{ color: pnlColor }}>{data.total_pnl.toFixed(2)}</strong>
      </div>
      <div className="stat-row">
        <span>PnL (1h)</span>
        <strong style={{ color: pnl1hColor }}>{data.pnl_1h.toFixed(2)}</strong>
      </div>
      <div className="stat-row">
        <span>Open positions</span>
        <strong>{data.open_positions}</strong>
      </div>
      <div className="stat-row">
        <span>Open orders</span>
        <strong>{data.open_orders}</strong>
      </div>
      <div className="stat-row">
        <span>Strategy mode</span>
        <strong>{data.strategy_mode || "n/a"}</strong>
      </div>
      <div className="stat-row">
        <span>Trend</span>
        <strong>{data.market_trend || "UNKNOWN"}</strong>
      </div>
      <div className="stat-row">
        <span>Market risk</span>
        <strong>{data.market_risk_level || "n/a"}</strong>
      </div>
    </div>
  );
}
