import { OverviewCard } from "./components/OverviewCard";
import { HealthStatus } from "./components/HealthStatus";
import { EventsTable } from "./components/EventsTable";
import { TsdbStatus } from "./components/TsdbStatus";

export default function App() {
  return (
    <div className="app">
      <header className="header">
        <h1 style={{ margin: 0 }}>QuantumEdge Supervisor Dashboard</h1>
        <span className="muted">API: /api/v1/dashboard/*</span>
      </header>
      <div className="grid">
        <OverviewCard />
        <HealthStatus />
        <TsdbStatus />
      </div>
      <div className="grid" style={{ marginTop: 16 }}>
        <EventsTable />
      </div>
    </div>
  );
}
