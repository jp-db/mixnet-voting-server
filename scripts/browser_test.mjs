// Browser end-to-end test: headless Chromium, driven over the DevTools
// protocol, votes through the real pages (WebAssembly in the worker), then a
// manager closes, counts and publishes from the admin page, and the results
// page verifies the counts in the browser.
//
//   node scripts/browser_test.mjs ELECTION_ID OUT_DIR [after-vote]
//
// Needs a running stack behind the Vite dev server on :5173 (or E2E_BASE_URL), the WASM module
// built (make wasm), Chromium, and an open election with the two questions
// and logins that scripts/browser_setup.py creates. Screenshots go to OUT_DIR.
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';

const [, , electionId, outDir] = process.argv;
const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:5173';
const PW = 'browser test password';
mkdirSync(outDir, { recursive: true });

const chrome = spawn(process.env.CHROMIUM ?? 'chromium', ['--headless=new', '--no-sandbox', '--remote-debugging-port=9333',
  `--user-data-dir=${outDir}/profile`, '--window-size=1100,1400', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());

let version;
for (let i = 0; i < 50 && !version; i++) {
  try { version = await (await fetch('http://127.0.0.1:9333/json/version')).json(); } catch { await new Promise((r) => setTimeout(r, 200)); }
}
const ws = new WebSocket(version.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let nextId = 1;
const waiting = new Map();
const problems = [];
ws.addEventListener('message', (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && waiting.has(msg.id)) {
    const { resolve, reject } = waiting.get(msg.id);
    waiting.delete(msg.id);
    msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
  } else if (msg.method === 'Page.javascriptDialogOpening') {
    send('Page.handleJavaScriptDialog', { accept: true }, msg.sessionId);
  } else if (msg.method === 'Runtime.exceptionThrown') {
    problems.push(`exception: ${msg.params.exceptionDetails.exception?.description ?? msg.params.exceptionDetails.text}`);
  } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
    problems.push(`console.error: ${msg.params.args.map((a) => a.value ?? a.description).join(' ')}`);
  }
});
function send(method, params = {}, sessionId) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return new Promise((resolve, reject) => waiting.set(id, { resolve, reject }));
}

async function newPage() {
  const { browserContextId } = await send('Target.createBrowserContext');  // own cookies
  const { targetId } = await send('Target.createTarget', { url: 'about:blank', browserContextId });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  for (const d of ['Page', 'Runtime']) await send(`${d}.enable`, {}, sessionId);
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
    return r.result.value;
  };
  const page = {
    evaluate,
    async goto(path) {
      await send('Page.navigate', { url: BASE + path }, sessionId);
      await page.waitFor('document.readyState === "complete"');
    },
    async waitFor(expr, timeout = 30000, what = expr) {
      const end = Date.now() + timeout;
      for (;;) {
        try { const v = await evaluate(expr); if (v) return v; } catch { /* page changing */ }
        if (Date.now() > end) {
          await page.shot('timeout');
          throw new Error(`timed out waiting for ${what}; page says: ${(await evaluate('document.body.innerText')).slice(0, 600)}`);
        }
        await new Promise((r) => setTimeout(r, 150));
      }
    },
    waitText: (text, timeout) => page.waitFor(`document.body.innerText.includes(${JSON.stringify(text)})`, timeout, `"${text}"`),
    /** Clicks the element whose whole text is ``text``. */
    async clickExact(selector, text) {
      const find = `[...document.querySelectorAll(${JSON.stringify(selector)})].find((el) => el.textContent.trim() === ${JSON.stringify(text)} && !el.disabled)`;
      await page.waitFor(find, 30000, `${selector} "${text}"`);
      await evaluate(`${find}.click()`);
    },
    async click(selector, text) {
      await page.waitFor(`[...document.querySelectorAll(${JSON.stringify(selector)})].some((el) => el.textContent.includes(${JSON.stringify(text)}) && !el.disabled)`, 30000, `${selector} "${text}"`);
      await evaluate(`[...document.querySelectorAll(${JSON.stringify(selector)})].find((el) => el.textContent.includes(${JSON.stringify(text)}) && !el.disabled).click()`);
    },
    async fill(selector, value) {
      await page.waitFor(`document.querySelector(${JSON.stringify(selector)})`);
      await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    },
    async shot(name) {
      const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }, sessionId);
      writeFileSync(`${outDir}/${name}.png`, Buffer.from(data, 'base64'));
    },
  };
  return page;
}

async function login(page, user, next) {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.fill('input[autocomplete="username"]', user);
  await page.fill('input[type="password"]', PW);
  await page.click('button', 'Log in');
}

const step = (s) => console.log(`- ${s}`);
const t0 = Date.now();

// Manager: a draft made in the admin pages, its answers added one by one, the roll
// pasted as CSV, the booth simulated before freezing, and a clone
if (process.argv[4] !== 'after-vote') {
  const page = await newPage();
  await login(page, 'manager1', '/admin');
  await page.click('button', 'New election');
  await page.fill('form input[required]', 'UI election');
  await page.click('button', 'Create draft');
  await page.waitText('No questions yet');
  await page.click('button', 'Add a question');
  await page.fill('fieldset input[required]', 'Lunch');
  for (const answer of ['Soup', 'Salad', 'Stew']) {
    await page.fill('input[placeholder="New answer"]', answer);
    await page.click('button', 'Add answer');
    await page.waitFor(`[...document.querySelectorAll('ol.answers li')].some((li) => li.textContent.includes(${JSON.stringify(answer)}))`);
  }
  await page.clickExact('button', 'Save');
  await page.waitText('Saved.');
  await page.fill('textarea.mono', 'password:voter0\npassword:voter1,v1@example.org,"One, Voter"');
  await page.click('button', 'Add to the roll');
  await page.waitText('2 rows read, 2 voters added');
  await page.shot('0-draft');
  await page.click('a', 'Try the booth');
  await page.waitText('the simulation checks the answers');
  await page.click('label.choice', 'Salad');
  await page.click('button', 'Review');
  await page.click('button', 'Check and encode');
  await page.waitText('The answers are valid');
  if (!(await page.evaluate(`document.body.innerText.includes('[0, 1, 0]')`))) throw new Error('wrong encoding shown');
  step('manager made a draft in the UI (answers one by one, roll as CSV) and simulated the booth before freezing');
  await page.click('a', 'Back to the election');
  await page.click('button', 'Clone');
  await page.waitText('Copy of UI election');
  step('manager cloned it');

  // After freezing, the simulation runs evmlr: on the open election, before anyone votes
  await page.goto(`/admin/elections/${electionId}/booth`);
  await page.waitText('encrypted with evmlr on the real parameters');
  await page.click('label.choice', 'Carla');
  await page.click('label.choice', 'Ezra');
  await page.click('button', 'Review');
  await page.click('button', 'Simulate casting');
  await page.waitText('The ballots would be accepted', 60000);
  if (!(await page.evaluate(`document.body.innerText.split('ballot box ✓').length === 3`))) throw new Error('simulation checks missing');
  await page.shot('0-simulation');
  step('manager simulated casting on the open election: WASM cast and check, ballot box check, nothing kept');
}

// Voters, each in a fresh browser context
const plans = [
  { user: 'voter0', chair: 'Bruno', board: ['Dora', 'Gil'] },
  { user: 'voter1', chair: 'Bruno', skipBoard: true },
  { user: 'voter2', chair: 'Ana', board: [] },
];
const trackers = {};
for (const plan of process.argv[4] === 'after-vote' ? [] : plans) {
  const page = await newPage();
  await login(page, plan.user, `/elections/${electionId}/vote`);
  await page.waitText('Review');
  await page.click('label.choice', plan.chair);
  if (plan.skipBoard) await page.evaluate(`[...document.querySelectorAll('.card')][1].querySelector('label.choice.muted').click()`);
  for (const b of plan.board ?? []) await page.click('label.choice', b);
  if (plan.user === 'voter0') await page.shot('1-booth');
  await page.click('button', 'Review');
  await page.click('button', 'Cast my ballot');
  await page.waitText('Your ballot was cast', 60000);
  trackers[plan.user] = await page.evaluate(`[...document.querySelectorAll('td.mono')].map((td) => td.textContent.trim())`);
  if (plan.user === 'voter0') await page.shot('2-cast');
  step(`${plan.user} voted in the browser: trackers ${trackers[plan.user].map((t) => t.slice(0, 12)).join(', ')}`);
}

// The voter's election page lists the same trackers; the tracker check finds them
{
  const page = await newPage();
  await login(page, 'voter0', `/elections/${electionId}`);
  await page.waitText('Your ballots');
  const shown = await page.evaluate(`[...[...document.querySelectorAll('.card')].find((c) => c.innerText.includes('Your ballots')).querySelectorAll('td.mono')].map((td) => td.textContent.trim())`);
  trackers.voter0 ??= shown;
  if (JSON.stringify(shown) !== JSON.stringify(trackers.voter0)) throw new Error(`election page shows ${shown}`);
  await page.fill('input.mono', trackers.voter0[0]);
  await page.click('button', 'Check');
  await page.waitText("It is the voter's latest ballot");
  step('voter0 sees their trackers, and the tracker check finds them');
  await page.goto(`/elections/${electionId}/board`);
  await page.waitFor(`document.querySelectorAll('td.mono').length >= 6`);
  await page.shot('3-board');
  step('board lists the ballots');
}

// Manager: close, count, publish from the admin page
{
  const page = await newPage();
  await login(page, 'manager1', `/admin/elections/${electionId}`);
  await page.click('button', 'Close voting');
  await page.click('button', 'Count');
  await page.waitFor(`document.body.innerText.split('Counted and verified').length === 3`, 90000, 'both questions counted');
  await page.shot('4-admin-counted');
  await page.click('button', 'Publish the results');
  await page.waitText('Results published');
  step('manager closed, counted (Celery, 3 mixnet nodes) and published from the admin page');
}

// Anyone: results and in-browser verification
{
  const page = await newPage();
  await page.goto(`/elections/${electionId}/results`);
  await page.waitText('Verify this count in my browser');
  await page.evaluate(`[...document.querySelectorAll('button')].filter((b) => b.textContent.includes('Verify this count')).forEach((b) => b.click())`);
  await page.waitFor(`document.querySelectorAll('p.success').length === 2 || document.querySelector('ul.error, p.error')`, 120000, 'verification');
  const text = await page.evaluate('document.body.innerText');
  await page.shot('5-results-verified');
  if (!/Verified:[^]*Verified:/.test(text)) throw new Error(`verification failed: ${text}`);
  const rows = await page.evaluate(`[...document.querySelectorAll('.card')].map((c) => [...c.querySelectorAll('tr')].slice(1).map((r) => [...r.cells].slice(0, 2).map((x) => x.textContent.trim()).join('=')).filter((s) => !s.includes(':')).join(' '))`);
  step(`results page: ${rows.join(' | ')}`);
  step('both counts verified in the browser (WASM verify + TypeScript recount)');
}

console.log(`done in ${((Date.now() - t0) / 1000).toFixed(1)} s; page errors: ${problems.length ? problems.join('\n  ') : 'none'}`);
process.exit(0);
