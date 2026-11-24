const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.goto('https://www.tradingview.com/symbols/XRPUSDT/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  const title = await page.title();
  console.log('TV title:', title);
  await browser.close();
  console.log('PUPPETEER_OK');
})().catch(e => { console.error('PUPPETEER_ERR', e.message); process.exit(1); });
