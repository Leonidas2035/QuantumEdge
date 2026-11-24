import React, { useState, useEffect } from 'react';
import OrderBook from './components/OrderBook';
import Chart from './components/Chart';
import { Activity, Wallet, Layers, Lock, ShieldAlert } from 'lucide-react';

function App() {
  const [wsData, setWsData] = useState(null);
  const [status, setStatus] = useState('Disconnected');

  useEffect(() => {
    const ws = new WebSocket('ws://localhost:8000/ws');

    ws.onopen = () => setStatus('Connected');
    ws.onclose = () => setStatus('Disconnected');
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setWsData(data);
      } catch (e) {
        console.error(e);
      }
    };

    return () => ws.close();
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-300 flex flex-col font-sans">
      {/* Header */}
      <header className="h-14 border-b border-slate-800 bg-slate-900/50 flex items-center px-4 justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-emerald-400 font-bold text-lg">
            <Layers className="w-5 h-5" />
            QuantumEdge
          </div>
          <div className="flex items-center gap-2 text-sm bg-slate-800/50 px-3 py-1 rounded-full ml-4">
            <div className={`w-2 h-2 rounded-full ${status === 'Connected' ? 'bg-emerald-400' : 'bg-red-400'}`}></div>
            {status}
          </div>
        </div>
        
        <div className="flex items-center gap-6 text-sm">
          <div className="flex flex-col items-end">
            <span className="text-slate-500 text-xs">VST Balance</span>
            <span className="font-mono text-emerald-400 font-semibold">10,000.00 USDT</span>
          </div>
          <div className="flex flex-col items-end border-l border-slate-800 pl-6">
            <span className="text-slate-500 text-xs">Today's PnL</span>
            <span className="font-mono text-emerald-400 font-semibold">+42.50 USDT</span>
          </div>
        </div>
      </header>

      {/* Main Grid Layout */}
      <main className="flex-1 p-2 gap-2 grid grid-cols-12 grid-rows-[1fr_250px]">
        {/* Left Column - Chart */}
        <div className="col-span-9 row-span-1">
          <Chart wsData={wsData} />
        </div>

        {/* Right Column - OrderBook */}
        <div className="col-span-3 row-span-1">
          <OrderBook wsData={wsData} />
        </div>

        {/* Bottom Left - Positions */}
        <div className="col-span-9 row-span-1 bg-slate-900 border border-slate-800 rounded-lg overflow-hidden flex flex-col">
          <div className="flex gap-4 p-3 border-b border-slate-800 bg-slate-900/50 text-sm">
            <button className="font-semibold text-emerald-400 border-b-2 border-emerald-400 pb-1 -mb-[13px]">Open Positions</button>
            <button className="font-semibold text-slate-500 hover:text-slate-300">Open Orders</button>
            <button className="font-semibold text-slate-500 hover:text-slate-300">Order History</button>
          </div>
          <div className="flex-1 p-4 flex items-center justify-center text-slate-600 italic">
            No active positions
          </div>
        </div>

        {/* Bottom Right - Order Form / Bot Controls */}
        <div className="col-span-3 row-span-1 bg-slate-900 border border-slate-800 rounded-lg p-4 flex flex-col gap-4">
          <h3 className="font-semibold text-slate-300 flex items-center gap-2">
            <Activity className="w-4 h-4 text-emerald-400" />
            Bot Controls
          </h3>
          <div className="flex-1 flex flex-col gap-3">
            <button className="w-full py-2 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded-md font-semibold hover:bg-emerald-500/20 transition-colors">
              RESUME TRADING
            </button>
            <button className="w-full py-2 bg-red-500/10 text-red-400 border border-red-500/20 rounded-md font-semibold hover:bg-red-500/20 transition-colors">
              HALT ALL BOTS
            </button>
            <button className="w-full py-2 bg-slate-800 text-slate-300 border border-slate-700 rounded-md font-semibold hover:bg-slate-700 transition-colors mt-auto flex items-center justify-center gap-2">
              <ShieldAlert className="w-4 h-4" />
              Kill Switch (Close All)
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
