/**
 * Bleibt die Leitung beim Seitenwechsel offen?
 *
 * Das ist keine erfundene Frage: Sie hing am Bauteil der Startseite, und jeder
 * Klick auf eine andere Seite riss sie mitten im Satz ab. Dieser Test geht
 * genau den Weg — Leitung öffnen, woanders hingehen, zurückkommen, beenden.
 *
 * Mikrofon und WebSocket sind ersetzt, damit kein Gerät und kein Schlüssel
 * nötig ist und kein Byte nach außen geht. Alles andere — Speicher, Konsole,
 * Streifen, Router — ist der echte Code.
 *
 * Aufruf über tests/test_live_line_ui.py, das den Server dazu startet.
 */
const BASE = process.env.JARVIS_TEST_BASE || "http://127.0.0.1:8799";
const PW = process.env.JARVIS_TEST_PLAYWRIGHT || "playwright";
const { chromium } = await import(PW);

const fails = [];
const check = (label, cond, detail = "") => {
  console.log((cond ? "  ok   " : "  FAIL ") + label + (cond ? "" : `  :: ${detail}`));
  if (!cond) fails.push(label);
};

const stub = () => {
  // Mikrofon: eine echte MediaStream aus dem Audiograph, ohne Gerät.
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  ctx.createOscillator().connect(dest);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: async () => dest.stream },
  });
  // WebSocket: öffnet sofort, merkt sich jedes close().
  window.__wsClosed = 0;
  window.__wsOpened = 0;
  class FakeWS {
    static OPEN = 1;
    constructor() {
      this.readyState = 1;
      window.__wsOpened++;
      setTimeout(() => this.onopen && this.onopen(), 0);
    }
    send() {}
    close() { window.__wsClosed++; this.readyState = 3; if (this.onclose) this.onclose(); }
  }
  window.WebSocket = FakeWS;
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
await page.addInitScript(stub);

await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.fill("#u", "admin");
await page.fill("#p", "adminpass123");
await page.click('button[type="submit"]');
await page.waitForTimeout(1500);
check("Anmeldung führt ins Dashboard", !page.url().includes("/login"), page.url());

await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".voice-console", { timeout: 15000 });
await page.locator(".voice-console .vc-actions button").click();
await page.waitForTimeout(800);
check("Leitung offen (Streifen sichtbar)", (await page.locator(".live-bar").count()) === 1);
check("genau ein WebSocket geöffnet", (await page.evaluate(() => window.__wsOpened)) === 1);

// Der eigentliche Punkt: woanders hingehen.
for (const path of ["/tasks", "/server", "/settings"]) {
  await page.click(`a[href="${path}"]`).catch(async () => { await page.goto(`${BASE}${path}`); });
  await page.waitForTimeout(500);
  const bar = await page.locator(".live-bar").count();
  const closed = await page.evaluate(() => window.__wsClosed);
  check(`Leitung überlebt den Wechsel nach ${path}`, bar === 1 && closed === 0,
        `Streifen ${bar}, close() ${closed}`);
}

// Zurück auf die Startseite: die Konsole kennt die laufende Leitung.
await page.click('a[href="/"]').catch(async () => { await page.goto(`${BASE}/`); });
await page.waitForTimeout(500);
const label = (await page.locator(".voice-console .vc-actions button").innerText()).trim();
check("die Konsole kennt die laufende Leitung wieder", label.includes("schließen"), label);

// Und sie endet, wenn der Nutzer es sagt — nur dann.
await page.locator(".live-bar .btn").click();
await page.waitForTimeout(500);
check("Beenden schließt die Leitung", (await page.locator(".live-bar").count()) === 0
  && (await page.evaluate(() => window.__wsClosed)) >= 1);

await browser.close();
check("keine Ausnahme in der Seite", pageErrors.length === 0, pageErrors.join(" | "));
console.log(fails.length ? `\n${fails.length} FEHLGESCHLAGEN: ${fails}` : "\nALL PASSED");
process.exit(fails.length ? 1 : 0);
