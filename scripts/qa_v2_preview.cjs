// Browser evidence for the final exported HTML, not a real mail-client test.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { pathToFileURL } = require('node:url');

(async () => {
  const input = path.resolve(process.argv[2]);
  const output = path.resolve(process.argv[3]);
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.AI_DAILY_CHROME, headless: true });
  try {
    const page = await browser.newPage();
    const checks = [];
    for (const width of [1100, 320, 390, 430]) {
      await page.setViewportSize({ width, height: 960 });
      await page.goto(pathToFileURL(input).href);
      await page.screenshot({ path: path.join(output, `email-${width}.png`), fullPage: true });
      checks.push(await page.evaluate(() => ({
        width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
        imageCount: document.images.length,
        anchorsValid: [...document.querySelectorAll('a[href^="#"]')].every(a => a.hash.length > 1 && document.getElementById(a.hash.slice(1))),
        wordmarkFont: getComputedStyle(document.querySelector('.np-wordmark-ai')).fontFamily,
        wordmarkShift: getComputedStyle(document.querySelector('.np-wordmark-ai')).verticalAlign,
        overflow: [...document.querySelectorAll('.np-title,.np-brief,.rd-paper,.np-toc,.np-edition')]
          .filter(e => { const b = e.getBoundingClientRect(); return b.right > innerWidth + 1 || b.left < -1; })
          .map(e => e.className),
      })));
    }
    const result = { kind: 'browser_only', html_sha256: crypto.createHash('sha256').update(fs.readFileSync(input)).digest('hex'), checks };
    result.passed = checks.every(c => c.scrollWidth <= c.width && c.imageCount === 0 && c.anchorsValid && !c.overflow.length);
    fs.writeFileSync(path.join(output, 'browser-qa.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    if (!result.passed) process.exitCode = 1;
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
