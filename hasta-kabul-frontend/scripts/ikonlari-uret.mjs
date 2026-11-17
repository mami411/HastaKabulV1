// Kurum işaretinden PWA/ana ekran PNG ikonlarını üretir (yeniden markalamada logo değişince çalıştırın):
//   npm run ikonlar
// Kaynak: public/brand/logo.svg (yuvarlak köşeli işaret) ve public/brand/logo-tam.svg (köşesiz, tam zemin;
// maskable ve Apple ikonu için, işaret ortadaki güvenli alanda). Yerel Chrome/Edge'i başsız çalıştırır;
// ağa çıkmaz. Tarayıcı yolu CHROME ortam değişkeniyle verilebilir.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(import.meta.dirname, "..");
const pub = join(root, "public");

const candidates = [
  process.env.CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);
const chrome = candidates.find((path) => existsSync(path));
if (!chrome) {
  console.error("Chrome/Edge bulunamadı; CHROME ortam değişkeniyle yolunu verin.");
  process.exit(1);
}

const jobs = [
  ["brand/logo.svg", 192, "icon-192.png"],
  ["brand/logo.svg", 512, "icon-512.png"],
  ["brand/logo-tam.svg", 512, "icon-maskable-512.png"],
  ["brand/logo-tam.svg", 180, "apple-touch-icon.png"],
];

const work = mkdtempSync(join(tmpdir(), "ikon-"));
try {
  for (const [source, size, target] of jobs) {
    copyFileSync(join(pub, source), join(work, "kaynak.svg"));
    const html = join(work, "sayfa.html");
    writeFileSync(
      html,
      `<!doctype html><style>html,body{margin:0;background:transparent}img{display:block;width:${size}px;height:${size}px}</style><img src="kaynak.svg">`,
    );
    const out = join(work, target);
    execFileSync(chrome, [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--default-background-color=00000000",
      "--force-device-scale-factor=1",
      `--window-size=${size},${size}`,
      `--screenshot=${out}`,
      pathToFileURL(html).href,
    ], { stdio: "ignore" });
    copyFileSync(out, join(pub, target));
    console.log(`public/${target} (${size}×${size}) ← ${source}`);
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}
