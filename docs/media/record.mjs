// Records the walkthrough: terminal scene, then a tour of the real dashboard served from the demo DB.
// usage: node record.mjs <demo-dir> <build-dir>
// Reads <build-dir>/terminal.json (from capture.py); writes walkthrough.webm and timing.json there.
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { freePort, startServer } from "./common.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const [demo, build] = process.argv.slice(2);
const today = JSON.parse(fs.readFileSync(path.join(demo, "demo.json"), "utf8")).today;
const steps = JSON.parse(fs.readFileSync(path.join(build, "terminal.json"), "utf8"));
// how long each command's output stays up before the next one is typed (default 1s)
const holds = [[/^make dry-run/, 1500], [/^make statement/, 2000], [/budgets list/, 2800], [/^make serve/, 1200], [/budgets set (Grocery|Misc|Petrol)/, 250]];
for (const s of steps) for (const [re, ms] of holds) if (re.test(s.cmd)) s.hold = ms;
const termHtml = fs.readFileSync(path.join(here, "terminal.html"), "utf8").replace("/*__STEPS__*/[]", JSON.stringify(steps));
fs.writeFileSync(path.join(build, "terminal-built.html"), termHtml);

// caption pill and a fake mouse pointer, on every page
const OVERLAY = () => {
  const setup = () => {
    if (document.getElementById("__cap")) return;
    const st = document.createElement("style");
    st.textContent = `#__cap{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:rgba(17,17,17,.88);color:#fff;font:600 15px/1.3 system-ui,"Segoe UI",sans-serif;padding:9px 16px;border-radius:9px;z-index:2147483647;opacity:0;transition:opacity .25s;pointer-events:none;white-space:nowrap;box-shadow:0 6px 20px rgba(0,0,0,.25)}
#__cur{position:fixed;left:0;top:0;width:20px;height:20px;z-index:2147483647;pointer-events:none;transition:transform .6s cubic-bezier(.4,.1,.2,1);transform:translate(640px,420px)}
#__cur.click::after{content:"";position:absolute;left:-12px;top:-12px;width:24px;height:24px;border-radius:50%;background:rgba(42,120,214,.35);animation:__rip .45s ease-out}
@keyframes __rip{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}`;
    document.head.appendChild(st);
    const cap = document.createElement("div"); cap.id = "__cap"; document.body.appendChild(cap);
    const cur = document.createElement("div"); cur.id = "__cur";
    cur.innerHTML = '<svg width="20" height="20" viewBox="0 0 20 20"><path d="M2 1l14 8.5-6.2 1.3L13.5 18l-2.6 1.2-3.6-7.2L2 16z" fill="#111" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    document.body.appendChild(cur);
    if (window.__lastCursor) cur.style.transform = window.__lastCursor;
  };
  window.__caption = text => { setup(); const c = document.getElementById("__cap"); c.style.opacity = text ? 1 : 0; if (text) c.textContent = text; };
  window.__cursor = (x, y) => { setup(); const c = document.getElementById("__cur"); c.style.transform = `translate(${x}px,${y}px)`; };
  window.__click = () => { const c = document.getElementById("__cur"); c.classList.remove("click"); void c.offsetWidth; c.classList.add("click"); };
  document.addEventListener("DOMContentLoaded", setup);
};

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "light", recordVideo: { dir: path.join(build, "raw"), size: { width: 1280, height: 800 } } });
const recordingStart = Date.now();
await context.clock.setFixedTime(new Date(today + "T10:00:00"));  // the dashboard's "this month" and today tick
await context.addInitScript(OVERLAY);
const page = await context.newPage();
const wait = ms => page.waitForTimeout(ms);
const caption = text => page.evaluate(t => window.__caption(t), text);
async function scrollTo(selector, offset = 16) {
  await page.evaluate(([sel, off]) => {
    const el = document.querySelector(sel);
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - off, behavior: "smooth" });
  }, [selector, offset]);
  await wait(900);
}
async function point(loc, { dx = 0, dy = 0, at = "center" } = {}) {
  const b = await loc.boundingBox();
  const x = (at === "left" ? b.x + 14 : b.x + b.width / 2) + dx, y = b.y + b.height / 2 + dy;
  await page.evaluate(([x, y]) => window.__cursor(x, y), [x, y]);
  await wait(700);
}
async function click(loc, opts) { await point(loc, opts); await page.evaluate(() => window.__click()); await loc.click(); await wait(250); }
async function type(loc, text) { await click(loc); await loc.pressSequentially(text, { delay: 85 }); }

// ---- terminal scene; the server starts when its "make serve" line appears
await page.goto("file://" + path.join(build, "terminal-built.html"));
await wait(600);
await page.evaluate(() => window.play(window.__caption));
// a free port rather than 8765, so a dashboard you are running yourself is never recorded
const port = await freePort();
const server = await startServer(demo, port);
try {
  await wait(400);

  // ---- dashboard tour; the thumbnail is taken from just after it starts
  const tourStart = (Date.now() - recordingStart) / 1000;
  fs.writeFileSync(path.join(build, "timing.json"), JSON.stringify({ tourStart }));
  await page.goto(`http://127.0.0.1:${port}/`);
  await wait(1200);
  await caption("Labels over budget are flagged at the top");
  await point(page.locator("#budget-alert"), { at: "left", dx: 260 });
  await wait(1800);

  await scrollTo("#budget-body", 150);
  await caption("Budget vs spend per Label, with the difference and % over or under");
  await point(page.locator("#budget-body tr").first(), { dx: 200 });
  await wait(2100);
  await caption("The tick on each bar marks how far through the month we are");
  await point(page.locator("#budget-body tr").nth(3).locator(".bar"));
  await wait(1900);

  await caption("Step back through the months");
  await click(page.locator("#bprev"));
  await wait(1800);
  await click(page.locator("#bnext"));
  await wait(900);

  await caption("Set or change budgets right in the dashboard");
  const misc = page.locator("#budget-body tr", { hasText: "Misc" });
  await click(misc.locator("[data-budget-edit]"));
  await wait(400);
  const amount = page.locator('#budget-form input[name="amount"]');
  await amount.fill("");
  await amount.pressSequentially("10000", { delay: 110 });
  await caption("A budget for just this month, say for Diwali");
  await point(page.locator('#budget-form select[name="scope"]'));
  await page.locator('#budget-form select[name="scope"]').selectOption("month");
  await wait(900);
  await click(page.locator("#budget-form button"));
  await wait(2100);

  await caption("Pick a date range");
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await wait(800);
  await click(page.locator('#range button[data-r="6"]'));
  await wait(1500);
  await caption("Click a Label chip to focus on it");
  await click(page.locator('#chips .chip[data-c="Food"]'));
  await wait(1800);
  await click(page.locator("#chips .chip.clear"));
  await click(page.locator('#range button[data-r="12"]'));
  await wait(600);

  await caption("Monthly spend by Label, straight from SQLite");
  await scrollTo("#c-monthly", 120);
  await point(page.locator("#c-monthly"), { dx: 250, dy: 40 });
  await wait(1800);

  await caption("Monthly trend per Label, with last month vs the average");
  await scrollTo("#c-trend", 90);
  await wait(2000);
  await caption("… or for your additional labels");
  await click(page.locator('#trend-by button[data-by="tag"]'));
  await wait(1900);
  await click(page.locator('#trend-by button[data-by="label"]'));

  await caption("Running total: this month vs last");
  await scrollTo("#c-cum", 90);
  await point(page.locator("#c-cum"), { dx: 60 });
  await wait(1800);

  await caption("Every transaction, searchable, with a month picker");
  await scrollTo("#tmonth", 70);
  await point(page.locator("#tmonth"));
  await page.locator("#tmonth").selectOption("2026-09");
  await wait(900);
  await type(page.locator("#q"), "swiggy");
  await wait(1400);
  await caption("Tag a transaction: saved to SQLite immediately");
  await type(page.locator("#tbody .tag-form input").first(), "Weekend");
  await click(page.locator("#tbody .tag-form button").first());
  await wait(1800);
  await page.locator("#q").fill("");
  await page.locator("#tmonth").selectOption("");

  await caption("Bank-statement rows arrive Unlabeled");
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await wait(800);
  await click(page.locator('#chips .chip[data-c="Unlabeled"]'));
  await wait(800);
  await scrollTo("#tmonth", 70);
  await wait(1200);
  await caption("Give them a Label inline: it counts toward that Label’s budget");
  const zepto = page.locator("#tbody tr", { hasText: "ZEPTO" });
  await type(zepto.locator(".primary-form input"), "Grocery");
  await click(zepto.locator(".primary-form button"));
  await wait(1800);

  await caption("Filter by any additional label");
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await wait(800);
  await click(page.locator("#chips .chip.clear"));
  await point(page.locator("#tag"));
  await page.locator("#tag").selectOption("Work");
  await wait(2100);
  await page.locator("#tag").selectOption("");
  await caption("make run → sync, then open this dashboard");
  await page.evaluate(() => window.__cursor(1300, 900));
  await wait(2800);
} finally {
  server.kill();
}
const video = page.video();
await context.close();
await video.saveAs(path.join(build, "walkthrough.webm"));
await browser.close();
console.log("Recorded", path.join(build, "walkthrough.webm"));
