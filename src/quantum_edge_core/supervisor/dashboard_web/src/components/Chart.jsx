import React, { useEffect, useRef } from 'react';
import { createChart } from 'lightweight-charts';

export default function Chart({ wsData }) {
  const chartContainerRef = useRef();
  const chartRef = useRef(null);
  const candlestickSeriesRef = useRef(null);
  const currentBar = useRef({
    time: 0,
    open: 0,
    high: 0,
    low: 0,
    close: 0,
  });

  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && chartContainerRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth, height: chartContainerRef.current.clientHeight });
      }
    };

    chartRef.current = createChart(chartContainerRef.current, {
      layout: {
        background: { type: 'solid', color: '#0f172a' }, // slate-900
        textColor: '#94a3b8', // slate-400
      },
      grid: {
        vertLines: { color: '#1e293b' }, // slate-800
        horzLines: { color: '#1e293b' },
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight,
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
    });

    candlestickSeriesRef.current = chartRef.current.addCandlestickSeries({
      upColor: '#10b981', // emerald-500
      downColor: '#ef4444', // red-500
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
      }
    };
  }, []);

  useEffect(() => {
    if (wsData && wsData.type === 'HUB:BTCUSDT:trade') {
      const trade = wsData.data;
      const price = parseFloat(trade.price);
      const time = Math.floor(trade.timestamp / 1000); 
      
      // Simple tick aggregator for real-time 1m candle updates
      // This is a naive implementation just for the dashboard mockup
      const currentMinute = Math.floor(time / 60) * 60;
      
      if (currentBar.current.time !== currentMinute) {
        // New candle
        currentBar.current = {
          time: currentMinute,
          open: price,
          high: price,
          low: price,
          close: price,
        };
      } else {
        // Update current candle
        currentBar.current.high = Math.max(currentBar.current.high, price);
        currentBar.current.low = Math.min(currentBar.current.low, price);
        currentBar.current.close = price;
      }

      candlestickSeriesRef.current.update(currentBar.current);
    }
  }, [wsData]);

  return (
    <div className="flex flex-col h-full bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
      <div className="flex justify-between items-center p-3 border-b border-slate-800 bg-slate-900/50">
        <div className="flex items-center gap-3">
          <h3 className="font-semibold text-slate-300">BTC/USDT</h3>
          <span className="text-xs bg-slate-800 text-slate-400 px-2 py-0.5 rounded">Perpetual</span>
        </div>
      </div>
      <div className="flex-1 w-full" ref={chartContainerRef} />
    </div>
  );
}
