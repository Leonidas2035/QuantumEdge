import React, { useState, useEffect } from 'react';

export default function OrderBook({ wsData }) {
  const [bids, setBids] = useState([]);
  const [asks, setAsks] = useState([]);

  useEffect(() => {
    if (wsData && wsData.type === 'HUB:BTCUSDT:orderbook') {
      const data = wsData.data;
      if (data.bids) setBids(data.bids.slice(0, 15));
      if (data.asks) setAsks(data.asks.slice(0, 15));
    }
  }, [wsData]);

  // Max volume for depth bar calculation
  const maxVolume = Math.max(
    ...bids.map(b => b[1]),
    ...asks.map(a => a[1]),
    1 // prevent div by zero
  );

  return (
    <div className="flex flex-col h-full bg-slate-900 border border-slate-800 rounded-lg overflow-hidden font-mono text-xs">
      <div className="flex justify-between items-center p-3 border-b border-slate-800 bg-slate-900/50">
        <h3 className="font-semibold text-slate-300">Order Book</h3>
        <span className="text-slate-500">BTC/USDT</span>
      </div>
      
      <div className="flex px-3 py-2 text-slate-500 font-semibold border-b border-slate-800/50">
        <div className="flex-1">Price (USDT)</div>
        <div className="flex-1 text-right">Amount (BTC)</div>
        <div className="flex-1 text-right">Total</div>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col justify-center">
        {/* Asks (Sells) - Reverse order to show lowest ask at bottom */}
        <div className="flex flex-col-reverse overflow-hidden">
          {asks.slice().reverse().map((ask, i) => {
            const price = parseFloat(ask[0]);
            const amount = parseFloat(ask[1]);
            const width = (amount / maxVolume) * 100;
            return (
              <div key={`ask-${i}`} className="flex px-3 py-0.5 relative hover:bg-slate-800/50">
                <div 
                  className="absolute right-0 top-0 bottom-0 bg-red-900/20" 
                  style={{ width: `${width}%` }}
                />
                <div className="flex-1 text-red-400 z-10">{price.toFixed(2)}</div>
                <div className="flex-1 text-right text-slate-300 z-10">{amount.toFixed(4)}</div>
                <div className="flex-1 text-right text-slate-500 z-10">{(price * amount).toFixed(2)}</div>
              </div>
            );
          })}
        </div>

        {/* Spread / Mid Market */}
        <div className="py-2 px-3 border-y border-slate-800 flex items-center justify-between bg-slate-800/30">
          <div className="text-lg font-bold text-slate-200">
            {asks[0] && bids[0] ? ((parseFloat(asks[0][0]) + parseFloat(bids[0][0])) / 2).toFixed(2) : '---'}
          </div>
          <div className="text-slate-500 text-xs">
            Spread: {asks[0] && bids[0] ? (parseFloat(asks[0][0]) - parseFloat(bids[0][0])).toFixed(2) : '---'}
          </div>
        </div>

        {/* Bids (Buys) */}
        <div className="flex flex-col overflow-hidden">
          {bids.map((bid, i) => {
            const price = parseFloat(bid[0]);
            const amount = parseFloat(bid[1]);
            const width = (amount / maxVolume) * 100;
            return (
              <div key={`bid-${i}`} className="flex px-3 py-0.5 relative hover:bg-slate-800/50">
                <div 
                  className="absolute right-0 top-0 bottom-0 bg-emerald-900/20" 
                  style={{ width: `${width}%` }}
                />
                <div className="flex-1 text-emerald-400 z-10">{price.toFixed(2)}</div>
                <div className="flex-1 text-right text-slate-300 z-10">{amount.toFixed(4)}</div>
                <div className="flex-1 text-right text-slate-500 z-10">{(price * amount).toFixed(2)}</div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
