import { useEffect, useState } from "react";
import { fetchStatus } from "../api/dashboard";

export function MarketFeatures() {
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

    if (error || !data) {
        return (
            <div className="card">
                <h3 className="title">AI Vision</h3>
                <div className="muted">{error ? error : "Connecting..."}</div>
            </div>
        );
    }

    const { market_context } = data;
    if (!market_context) {
        return (
            <div className="card">
                <h3 className="title">AI Vision</h3>
                <div className="muted">No Data</div>
            </div>
        );
    }

    const { ofi, cvd_slope, vwap_z, volatility } = market_context;

    // Format OFI
    const ofiVal = ofi || 0;
    const ofiColor = ofiVal > 0 ? "#16a34a" : ofiVal < 0 ? "#dc2626" : "#888";
    const ofiArrow = ofiVal > 0 ? "↑" : ofiVal < 0 ? "↓" : "-";

    // Format CVD Slope
    const cvdVal = cvd_slope || 0;
    const cvdText = cvdVal > 0 ? "BULLISH" : cvdVal < 0 ? "BEARISH" : "NEUTRAL";
    const cvdColor = cvdVal > 0 ? "#16a34a" : cvdVal < 0 ? "#dc2626" : "#888";

    // Format VWAP Z
    const vwapVal = vwap_z || 0;
    const vwapStr = `${vwapVal > 0 ? "+" : ""}${vwapVal.toFixed(2)}σ`;

    return (
        <div className="card">
            <h3 className="title">AI Vision (Microstructure)</h3>
            <div className="grid-2-col">
                <div className="metric-box">
                    <span className="label">Order Flow (OFI)</span>
                    <div className="value" style={{ color: ofiColor }}>
                        {ofiArrow} {Math.abs(ofiVal).toFixed(2)}
                    </div>
                </div>

                <div className="metric-box">
                    <span className="label">CVD Slope</span>
                    <div className="value" style={{ color: cvdColor }}>
                        {cvdText}
                    </div>
                </div>

                <div className="metric-box">
                    <span className="label">VWAP Deviation</span>
                    <div className="value" style={{ color: "#3b82f6" }}>
                        {vwapStr}
                    </div>
                </div>

                <div className="metric-box">
                    <span className="label">Volatility</span>
                    <div className="value" style={{ color: "#eab308" }}>
                        {(volatility * 100).toFixed(2)}%
                    </div>
                </div>
            </div>
        </div>
    );
}
