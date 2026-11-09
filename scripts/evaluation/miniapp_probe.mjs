// Opt-in feasibility probe. This is not part of the app or a production sandbox.
import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';

if (!process.argv.includes('--execute')) throw new Error('Pass --execute to run the disposable browser probe.');
const require = createRequire(path.join(process.env.LOCALMIND_BROWSER_DEPS || process.cwd(), 'package.json'));
const { chromium } = require('playwright');
const out = path.resolve('artifacts/visual-batch/miniapp-probe.json');
const server = await chromium.launchServer({ headless: true, executablePath: process.env.LOCALMIND_BROWSER_EXECUTABLE || undefined });
const browser = await chromium.connect(server.wsEndpoint());
const report = { decision: 'no-go for shipping arbitrary generated code', checks: {}, limitations: [
  'Headless Chromium only. This does not establish isolation in Tauri/WebView2, WKWebView or WebKitGTK.',
  'The synthetic parent bridge and canary are not real credentials or host access.',
  'An external controller can kill the browser. The app has no such per-artifact process and resource budget.',
  'Opaque origin and CSP do not establish CPU or memory limits, safe messaging, navigation isolation or download control.',
] };
try {
  const context = await browser.newContext();
  const reachedNetwork = [];
  await context.route('**/*', route => { reachedNetwork.push(route.request().url()); return route.abort(); });
  const page = await context.newPage();
  await page.setContent('<main>Trusted host</main>');
  await page.evaluate(() => {
    window.syntheticCredential = 'FAKE_CANARY';
    window.__TAURI_INTERNALS__ = { invoke: () => 'FAKE_BRIDGE' };
    window.results = {};
    window.addEventListener('message', event => {
      if (event.source === document.querySelector('iframe')?.contentWindow && event.data?.probe)
        window.results = event.data.results;
    });
    const frame = document.createElement('iframe');
    frame.sandbox = 'allow-scripts';
    frame.srcdoc = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; style-src 'none'; base-uri 'none'; form-action 'none'">
      <script>(async () => {
        const results = {};
        for (const [name, attempt] of Object.entries({
          parentDocument: () => parent.document.body.innerText,
          credential: () => parent.syntheticCredential,
          bridge: () => parent.__TAURI_INTERNALS__.invoke('read_file'),
          storage: () => localStorage.getItem('test'),
          network: async () => (await fetch('http://127.0.0.1:9/probe')).text(),
          file: async () => (await fetch('file:///localmind-synthetic-missing-canary.txt')).text()
        })) { try { await attempt(); results[name] = 'allowed'; } catch { results[name] = 'blocked'; } }
        parent.postMessage({probe:true, results}, '*');
      })();<\/script>`;
    document.body.append(frame);
  });
  await page.waitForFunction(() => Object.keys(window.results).length === 6, { timeout: 5000 });
  report.checks = await page.evaluate(() => window.results);
  for (const value of Object.values(report.checks)) assert.equal(value, 'blocked');
  assert.deepEqual(reachedNetwork, []);
  report.networkRequestsReachedController = reachedNetwork;
  // This deliberately hostile loop runs only in the disposable browser, never LocalMind.
  const frame = page.frames().find(item => item !== page.mainFrame());
  const started = page.waitForEvent('console', { predicate: message => message.text() === 'synthetic-loop-started', timeout: 5000 });
  const loop = frame.evaluate(() => { console.log('synthetic-loop-started'); for (;;) { /* external termination required */ } }).catch(() => {});
  await started;
  const parentResponsive = await Promise.race([
    page.evaluate(() => document.querySelector('main').textContent).then(() => true).catch(() => false),
    new Promise(resolve => setTimeout(() => resolve(false), 1500)),
  ]);
  report.checks.parentResponsiveDuringLoop = parentResponsive;
  await server.kill();
  await loop;
  report.checks.loopTerminatedByExternalController = true;
} finally {
  await server.kill().catch(() => {});
  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, JSON.stringify(report, null, 2));
}
console.log(JSON.stringify(report, null, 2));
