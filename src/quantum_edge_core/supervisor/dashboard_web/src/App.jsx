import { OverviewCard } from "./components/OverviewCard";
import { HealthStatus } from "./components/HealthStatus";
import { EventsTable } from "./components/EventsTable";
import { TsdbStatus } from "./components/TsdbStatus";
import { MarketFeatures } from "./components/MarketFeatures";

export default function App() {
  const handleEmergencyStop = () => {
    // Stub for now - just log or alert
    if (confirm("CONFIRM EMERGENCY STOP? This will halt all trading.")) {
      console.log("EMERGENCY STOP TRIGGERED");
      fetch("/api/v1/bot/stop", { method: "POST" })
        .then(() => alert("Stop signal sent. Check logs."))
        .catch((e) => alert("Failed to send stop signal: " + e));
    }
  };

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1 style={{ margin: 0, fontSize: "24px", letterSpacing: "-0.02em" }}>
            <span style={{ color: "#3b82f6" }}>QUANTUM</span>EDGE SUPERVISOR
          </h1>
          <span className="muted" style={{ fontSize: "12px", fontFamily: "monospace" }}>
            v2.0 :: SYSTEM ACTIVE
          </span>
        </div>
        <button className="btn-danger" onClick={handleEmergencyStop}>
          ⚠️ EMERGENCY STOP
        </button>
      </header>

      <div className="grid">
        <OverviewCard />
        <HealthStatus />
        <TsdbStatus />
      </div>

      <div style={{ marginTop: 16 }}>
        <MarketFeatures />
      </div>

      <div style={{ marginTop: 16 }}>
        <EventsTable />
      </div>
    </div>
  );
}
