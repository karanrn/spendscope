// Screenshots of the real dashboard (demo DB, after the walkthrough's edits) and the video thumbnail.
// usage: node shots.mjs <demo-dir> <build-dir>
// Reads walkthrough.mp4 and timing.json from <build-dir>; writes <build-dir>/screenshots/.
import { chromium } from "playwright";
import { execFileSync } from "child_process";
import fs from "fs";
import path from "path";
import { freePort, startServer } from "./common.mjs";

const [demo, build] = process.argv.slice(2);
const today = JSON.parse(fs.readFileSync(path.join(demo, "demo.json"), "utf8")).today;
const video = path.join(build, "walkthrough.mp4");
const seconds = +execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video]).toString();
const thumbAt = JSON.parse(fs.readFileSync(path.join(build, "timing.json"), "utf8")).tourStart + 2;
const out = path.join(build, "screenshots");
fs.mkdirSync(out, { recursive: true });

const port = await freePort();
const server = await startServer(demo, port);
const browser = await chromium.launch();
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "light" });
  await context.clock.setFixedTime(new Date(today + "T10:00:00"));
  const page = await context.newPage();
  const settle = () => page.waitForTimeout(700);
  const card = sel => page.locator(sel).locator("xpath=ancestor::div[contains(@class,'card')][1]");
  await page.goto(`http://127.0.0.1:${port}/`);
  await settle();

  // overview: filters, over-budget banner, KPIs, budgets and the monthly charts
  const bottom = await page.evaluate(() => { const g = document.querySelector(".grid"); return g.getBoundingClientRect().bottom + scrollY + 16; });
  await page.screenshot({ path: path.join(out, "dashboard-overview.png"), fullPage: true, clip: { x: 0, y: 0, width: 1280, height: Math.ceil(bottom) } });
  await card("#budget-body").screenshot({ path: path.join(out, "budgets.png") });
  await card("#c-trend").screenshot({ path: path.join(out, "trend-by-label.png") });

  await page.locator("#tmonth").selectOption("2026-09");
  await page.locator("#q").fill("swiggy");
  await settle();
  await card("#tbody").screenshot({ path: path.join(out, "transactions.png") });

  await page.locator("#q").fill("");
  await page.locator("#tmonth").selectOption("");
  await page.locator('#chips .chip[data-c="Unlabeled"]').click();
  await settle();
  await card("#tbody").screenshot({ path: path.join(out, "unlabeled.png") });

  // thumbnail: a frame from the start of the dashboard tour, with a play button
  const frame = path.join(build, "thumb-frame.png");
  execFileSync("ffmpeg", ["-v", "error", "-y", "-ss", thumbAt.toFixed(2), "-i", video, "-frames:v", "1", frame]);
  const thumb = await browser.newPage({ viewport: { width: 960, height: 600 } });
  const img = fs.readFileSync(frame).toString("base64");
  await thumb.setContent(`<style>body{margin:0;width:960px;height:600px;position:relative;overflow:hidden;font-family:system-ui,"Segoe UI",sans-serif}
img{width:960px;height:600px;display:block;filter:brightness(.82)}
.play{position:absolute;left:50%;top:46%;width:112px;height:112px;margin:-56px 0 0 -56px;border-radius:50%;background:#fff;box-shadow:0 8px 30px rgba(0,0,0,.35)}
.play::after{content:"";position:absolute;left:44px;top:31px;border-style:solid;border-width:25px 0 25px 40px;border-color:transparent transparent transparent #2a78d6}
.t{position:absolute;left:50%;top:62%;transform:translateX(-50%);background:rgba(17,17,17,.85);color:#fff;font-weight:700;font-size:21px;padding:8px 16px;border-radius:8px;white-space:nowrap}</style>
<img src="data:image/png;base64,${img}"><div class="play"></div><div class="t">Watch the ${Math.round(seconds)}-second walkthrough</div>`);
  await thumb.screenshot({ path: path.join(out, "walkthrough-thumbnail.jpg"), type: "jpeg", quality: 85 });
} finally {
  await browser.close();
  server.kill();
}
console.log(`Screenshots in ${out}; the video is ${Math.round(seconds)} seconds`);
