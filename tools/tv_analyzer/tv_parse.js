/**
 * TradingView analyzer for XRPUSDT via Puppeteer (system google-chrome).
 * Extracts: technical rating, oscillators, moving averages, key levels.
 * Output: JSON to stdout (consumed by supervisor_market_analysis.py).
 *
 * Run: node tv_analyzer/tv_parse.js XRPUSDT
 */
const puppeteer = require('puppeteer');

const SYMBOL = process.argv[2] || 'XRPUSDT';
const URL = `https://www.tradingview.com/symbols/${SYMBOL}/`;

function text(el) { return el ? (el.innerText || '').trim() : null; }

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 900 });
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 45000 });

  // Give widgets time to render
  await new Promise(r => setTimeout(r, 4000));

  const data = await page.evaluate(() => {
    const out = {};
    // Technical rating (top widget: "Strong Buy", "Buy", "Neutral"...)
    const recEl = document.querySelector('[data-testid="technical-analysis-summary"]')
      || [...document.querySelectorAll('div')].find(d => /Strong Buy|Buy|Neutral|Sell|Strong Sell/.test(d.innerText || '') && (d.innerText || '').length < 40);
    out.recommendation = recEl ? (recEl.innerText || '').replace(/\s+/g, ' ').trim() : null;

    // Try to find the recommendation text near "Technical" panel
    const techPanel = [...document.querySelectorAll('*')].find(n => /Recommendation|Technical\b/.test(n.innerText || '') && (n.innerText||'').length < 120);
    if (techPanel) out.techPanelText = (techPanel.innerText||'').replace(/\s+/g,' ').trim();

    // Oscillators + Moving Averages summary lines
    const allText = document.body.innerText;
    const oscMatch = allText.match(/(Sell|Buy|Neutral)\s+(\d+)\s*(Sell|Neutral|Buy)\s+(\d+)\s*(Sell|Neutral|Buy)\s+(\d+)/);
    if (oscMatch) out.oscillators = oscMatch[0].replace(/\s+/g, ' ');

    // Price
    const priceEl = document.querySelector('[data-symbol-last]') || document.querySelector('.js-symbol-last');
    out.priceText = priceEl ? priceEl.innerText : null;

    // Key levels: support / resistance if present
    const bodies = [...document.querySelectorAll('div')].map(d => (d.innerText||'').trim()).filter(t => /Resistance|Support/.test(t) && t.length < 200);
    out.levels = bodies.slice(0, 6);
    out.fullTextSample = allText.slice(0, 1500);
    return out;
  });

  // Also fetch the dedicated technical analysis page for cleaner rating
  try {
    await page.goto(`https://www.tradingview.com/symbols/${SYMBOL}/technicals/`, { waitUntil: 'networkidle2', timeout: 45000 });
    await new Promise(r => setTimeout(r, 3500));
    const tech = await page.evaluate(() => {
      const txt = document.body.innerText;
      const m = txt.match(/(Strong Buy|Buy|Neutral|Sell|Strong Sell)/);
      const osc = txt.match(/Oscillators\s+(Strong Buy|Buy|Neutral|Sell|Strong Sell)/);
      const ma = txt.match(/Moving Averages\s+(Strong Buy|Buy|Neutral|Sell|Strong Sell)/);
      // support/resistance levels
      const levels = [...txt.matchAll(/(Resistance|Support)\s*[:]?\s*([\d.]+)/g)].map(x => `${x[1]} ${x[2]}`);
      return { rating: m ? m[1] : null, oscillators: osc ? osc[1] : null, movingAverages: ma ? ma[1] : null, levels: levels.slice(0, 10) };
    });
    data.technicalsPage = tech;
  } catch (e) {
    data.technicalsPage = { error: e.message };
  }

  await browser.close();
  console.log(JSON.stringify({ symbol: SYMBOL, ...data }, null, 2));
})().catch(e => { console.error('TV_PARSE_ERR', e.message); process.exit(1); });
