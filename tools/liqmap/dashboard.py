"""
HTML-дашборд карти ліквідацій (Plotly, self-contained).
Heatmap інтенсивності ліквідацій (ціна × час) + оверлей свічок BTC +
профіль магнітів праворуч + горизонтальні лінії топ-зон.
"""
from __future__ import annotations
import json, datetime as dt
from pathlib import Path

TEMPLATE = """<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<title>QuantumEdge — Liquidation Heatmap (BTC)</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  body {{ background:#0d1117; color:#c9d1d9; font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:16px; }}
  h1 {{ font-size:18px; margin:0 0 4px; }}
  .sub {{ color:#8b949e; font-size:13px; margin-bottom:12px; }}
  .zones {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }}
  .z {{ padding:6px 10px; border-radius:6px; font-size:12px; border:1px solid #30363d; }}
  .long {{ background:#12261a; color:#3fb950; }}
  .short {{ background:#2d1418; color:#f85149; }}
  #hm {{ width:100%; height:640px; }}
  .note {{ color:#8b949e; font-size:12px; margin-top:10px; line-height:1.5; }}
</style>
</head>
<body>
  <h1>QuantumEdge — Карта ліквідацій BTC/USDT</h1>
  <div class="sub">Згенеровано: {generated} UTC · TF {tf} · ohlcv:{ohlcv_src} oi:{oi_src} · ціна: <b>{last_price}</b> · funding: {funding} · OI: {oi_usd}</div>
  <div class="zones">{zone_chips}</div>
  <div id="hm"></div>
  <div class="note">
    <b>Як читати:</b> яскраві зони = скупчення потенційних ліквідацій (магніти ліквідності).
    Зони НИЖЧЕ ціни = ліквідації ЛОНГІВ (магніт при падінні). Зони ВИЩЕ = ліквідації ШОРТІВ (магніт при рості).
    Ціну часто «тягне» до найближчого/найсильнішого скупчення. Модель евристична (плечі 10/25/50/100x, зважено обсягом+OI), не інсайд біржі — як у CoinGlass/Gate.
  </div>
<script>
const grid = {grid_json};          // [price_bin][time]
const prices = {prices_json};      // price centers
const times = {times_json};        // ms timestamps
const candles = {candles_json};    // [ts,o,h,l,c]
const zones = {zones_json};
const lastPrice = {last_price};

const timeLabels = times.map(t => new Date(t).toISOString().slice(5,16).replace('T',' '));

const heat = {{
  type:'heatmap', x:timeLabels, y:prices, z:grid,
  colorscale:[[0,'#0d1117'],[0.2,'#0d2233'],[0.45,'#1f6feb'],[0.7,'#f0b429'],[1,'#f85149']],
  colorbar:{{title:'liq intensity', titleside:'right'}}, zsmooth:'best', hoverongaps:false
}};

const candle = {{
  type:'candlestick', x:timeLabels,
  open:candles.map(c=>c[1]), high:candles.map(c=>c[2]),
  low:candles.map(c=>c[3]), close:candles.map(c=>c[4]),
  increasing:{{line:{{color:'#3fb95088'}}}}, decreasing:{{line:{{color:'#f8514988'}}}},
  yaxis:'y', name:'BTC', showlegend:false, opacity:0.55
}};

const shapes = zones.map(z => ({{
  type:'line', xref:'paper', x0:0, x1:1, yref:'y', y0:z.price, y1:z.price,
  line:{{color: z.side==='LONG_LIQ'?'#3fb950':'#f85149', width:1, dash:'dot'}}
}}));
shapes.push({{type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:lastPrice,y1:lastPrice,
  line:{{color:'#e6edf3',width:1.5}}}});

const ann = zones.map(z => ({{
  xref:'paper', x:1.005, yref:'y', y:z.price, xanchor:'left',
  text:(z.side==='LONG_LIQ'?'\u25bc L ':'\u25b2 S ')+z.price+' ('+z.dist_pct+'%)',
  showarrow:false, font:{{size:10, color:z.side==='LONG_LIQ'?'#3fb950':'#f85149'}}
}}));

const layout = {{
  paper_bgcolor:'#0d1117', plot_bgcolor:'#0d1117', font:{{color:'#c9d1d9'}},
  margin:{{l:60,r:120,t:10,b:60}},
  xaxis:{{title:'час (UTC)', gridcolor:'#21262d', rangeslider:{{visible:false}}}},
  yaxis:{{title:'ціна USDT', gridcolor:'#21262d'}},
  shapes:shapes, annotations:ann
}};

Plotly.newPlot('hm', [heat, candle], layout, {{responsive:true, displaylogo:false}});
</script>
</body>
</html>"""


def render_dashboard(hm, ctx, out_path: Path):
    grid = hm["grid"]              # [price_bin][time]
    prices = hm["price_levels"]
    times = hm["time_axis"]
    # candles from time_axis не збігається — беремо з meta? Треба ohlcv; hm не містить.
    # Реконструюємо свічки з grid неможливо → зберігаємо окремо в hm нижче (див. model).
    candles = hm.get("candles", [])

    zone_chips = ""
    for z in hm["zones"]:
        cls = "long" if z["side"] == "LONG_LIQ" else "short"
        lbl = "L-liq" if z["side"] == "LONG_LIQ" else "S-liq"
        zone_chips += f'<span class="z {cls}">{lbl} {z["price"]} ({z["dist_pct"]:+.2f}%) · {z["intensity"]:.2f}</span>'

    html = TEMPLATE.format(
        generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        tf=hm["meta"].get("tf", "1h"),
        ohlcv_src=hm["meta"].get("ohlcv_src", "-"),
        oi_src=hm["meta"].get("oi_src", "-"),
        last_price=round(hm["meta"]["last_price"], 2),
        funding=ctx.get("funding"),
        oi_usd=ctx.get("oi_usd"),
        zone_chips=zone_chips,
        grid_json=json.dumps(grid),
        prices_json=json.dumps([round(p, 2) for p in prices]),
        times_json=json.dumps(times),
        candles_json=json.dumps(candles),
        zones_json=json.dumps(hm["zones"]),
    )
    out_path.write_text(html)
    return out_path
