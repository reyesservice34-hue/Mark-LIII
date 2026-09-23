// Reyes Office — Darstellung offline testen (node tests/test_reyes_app.mjs). Kein Browser, kein Netz, kein Server.
import { readFileSync } from "node:fs";
import { createCore } from "../reyes-app/core.mjs";
import { jarvisFrameHTML } from "../reyes-app/render.mjs";
import { startMeter } from "../reyes-app/chat.mjs";
import { pickAudioMime, toSpeakable, waitForAnswer, createChat, createVad, STORE } from "../reyes-app/chat.mjs";
import { bubbleHTML, chatHTML } from "../reyes-app/render.mjs";
import { esc, fmtDate, fmtDateLong, fmtEuro, fmtResult, buchhaltungView, meldungenView, kalenderView, standortView, shellHTML, loginHTML, MODULES } from "../reyes-app/render.mjs";

let fails = 0;
const check = (name, cond, detail = "") => { console.log((cond ? "  ok   " : "  FAIL ") + name + (cond ? "" : `  :: ${detail}`)); if (!cond) fails++; };
const has = (html, ...parts) => parts.every((p) => html.includes(p));

const base = (over = {}) => ({
  jetzt: "2026-10-03T10:00:00", agent: { aktiv: true },
  lexware: { konfiguriert: false, hinweis: "LEXWARE_API_KEY fehlt." },
  ustva: { frist: "2026-10-12", tage_bis_frist: 9, schlusstag: "2026-10-08", zeitraum: "September 2026", quartal_moeglich: true, entwurf_angestossen: false, schluss_angestossen: false },
  postfach: { letzter_lauf: null, ergebnis: "noch nicht gelaufen", auftraege_gesamt: 0 }, abend: null, ungeprueft: null, ...over,
});

// ── Hilfsfunktionen ──
check("esc maskiert alles Gefährliche", esc(`<img src=x onerror="a('b')">&`) === "&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;");
check("esc: leer/undefined ergibt leeren Text", esc(null) === "" && esc(undefined) === "");
check("Datum: 12.10.2026 ist ein Montag", fmtDateLong("2026-10-12") === "Montag, 12.10.2026" && fmtDate("2026-10-12T00:00:00.000+02:00") === "12.10.2026");
check("Euro deutsch formatiert", /119,00\s*€/.test(fmtEuro(119)) && fmtEuro(null) === "–" && fmtEuro("x") === "–");
check("Ergebnis-Sätze", fmtResult({ status: "gestartet", neue_belege: 3 }).includes("3 neue Belege") && fmtResult({ skipped: "Nicht jetzt" }) === "Nicht jetzt" && fmtResult({ error: "kaputt" }) === "Fehler: kaputt" &&
  fmtResult({ status: "nichts Neues", ungeprueft_gesamt: 2 }).includes("Nichts Neues") && fmtResult({ started: true, phase: "schluss" }).includes("Schlussprüfung") && fmtResult(null) === "noch nicht gelaufen" && fmtResult({ first_run: true, vermerkt: 5 }).includes("5"));

// ── Buchhaltung ohne Lexware-Schlüssel ──
let h = buchhaltungView(base());
check("ohne Schlüssel: sagt ehrlich, dass Lexware nicht verbunden ist", has(h, "nicht verbunden", "Lexware nicht verbunden"));
check("ohne Schlüssel: keine Server-Befehle, Dateipfade oder Schlüssel-Anleitung in der App", !/setup-lexware|\/root\/|bash |LEXWARE_API_KEY|addons\/public-api|Lexware verbinden|Server/.test(h), h.match(/setup-lexware|\/root\/|bash |LEXWARE_API_KEY|Server/g));
check("ohne Schlüssel: alle Aktionsknöpfe sind gesperrt", ["ustva-entwurf", "ustva-schluss", "postfach", "abend"].every((a) => new RegExp(`data-act="${a}"\\s+disabled`).test(h)), h.match(/data-act="[a-z-]+"[^>]*>/g));
check("ohne Schlüssel: keine erfundenen Belege, Frist ist trotzdem da", has(h, "Lexware nicht verbunden", "Montag, 12.10.2026", "<small>Tage</small>") && !h.includes("Alles abgearbeitet"));
check("Frist-Kachel: 9 Tage = ok, 5 = warn, 2 = err", has(h, 'class="value ok"') && has(buchhaltungView(base({ ustva: { ...base().ustva, tage_bis_frist: 5 } })), 'class="value warn"') && has(buchhaltungView(base({ ustva: { ...base().ustva, tage_bis_frist: 2 } })), 'class="value err"'));
check("Abgabe steht als Menschenaufgabe da, nie als erledigt", has(h, "Abgabe: macht ein Mensch") && !/class="done"[^>]*><span class="dot"><svg[^>]*>[^<]*<path[^>]*><\/svg><\/span>Abgabe/.test(h));
check("Regeln des Agenten stehen da (Karten 6952 · 6369 · 6359, Kasse)", has(h, "6952", "6369", "6359", "per Kasse bezahlt"));
check("Quartals-Hinweis nur, wenn ein Quartalsende möglich ist", has(h, "Quartalsende") && !buchhaltungView(base({ ustva: { ...base().ustva, quartal_moeglich: false } })).includes("Quartalsende"));

// ── mit Schlüssel ──
const belege = [{ id: "1", date: "2026-09-30", contact: "IONOS SE", number: "R-1", total: 11.9 }, { id: "2", date: "2026-09-29", contact: `<img src=x onerror=alert(1)>`, number: "", total: 60 }];
h = buchhaltungView(base({ lexware: { konfiguriert: true, firma: "Reyes Service" }, ungeprueft: { anzahl: 27, belege } }));
check("mit Schlüssel: verbunden, Firma, Knöpfe frei", has(h, "verbunden", "Reyes Service") && !/data-act="ustva-entwurf"\s+disabled/.test(h) && !h.includes("Lexware verbinden"));
check("mit Schlüssel: Beleg-Tabelle mit Betrag und Datum", has(h, "IONOS SE", "30.09.2026") && /11,90\s*€/.test(h) && has(h, "R-1"));
check("Text aus Lexware wird maskiert (kein eingeschleustes HTML)", !h.includes("<img src=x") && h.includes("&lt;img src=x"));
check("Bestand größer als die Liste: nennt die Restzahl", has(h, "25 weitere nicht angezeigt"), h.match(/weitere[^<]*/));
check("Beleg ohne Partner/Nummer bekommt Platzhalter", buchhaltungView(base({ lexware: { konfiguriert: true }, ungeprueft: { anzahl: 1, belege: [{ id: "x", date: "2026-09-01", contact: "", number: "", total: 5 }] } })).includes("(ohne Partner)"));
check("keine ungeprüften Belege: 'Alles abgearbeitet'", buchhaltungView(base({ lexware: { konfiguriert: true }, ungeprueft: { anzahl: 0, belege: [] } })).includes("Alles abgearbeitet"));
h = buchhaltungView(base({ lexware: { konfiguriert: true }, ustva: { ...base().ustva, entwurf_angestossen: true }, abend: { status: "gestartet", neue_belege: 2 }, postfach: { letzter_lauf: "2026-10-03 09:00:00", ergebnis: { new: 1 }, auftraege_gesamt: 4 } }));
check("Checkliste: Entwurf erledigt, Schlussprüfung offen", /<li class="done"><span class="dot">[^]*?<\/span>Entwurf/.test(h) && /<li class=""><span class="dot"><\/span>Schlussprüfung/.test(h));
check("Automatik zeigt Postfach- und Abendergebnis", has(h, "1 neue Mail(s) mit Beleg übergeben", "2 neue Belege an den Buchhalter übergeben", "Aufträge an den Agenten seit dem Start: 4"));
check("Lexware-Fehler wird angezeigt, nicht verschluckt", buchhaltungView(base({ lexware: { konfiguriert: true, fehler: "Lexware lehnt den API-Schlüssel ab" } })).includes("lehnt den API-Schlüssel ab"));
check("Agent aus: rotes Signal", buchhaltungView(base({ agent: { aktiv: false } })).includes('badge err">aus'));
check("Serverfehler: eigene Fehlerkachel statt leerer Seite", has(buchhaltungView({ fehler: "Keine Verbindung zum Server." }), "nicht erreichbar", "Keine Verbindung"));

// ── Kalender ──
const ev = (s, e, t, extra = {}) => ({ start: s, end: e, title: t, category: "ich", location: "", ...extra });
h = kalenderView({ available: true, backend: "n8n", events: [
  ev("2026-10-05T08:00:00", "2026-10-05T17:00:00", "Baustelle – Bad", { category: "tour", location: "Bahnhofstraße 1, Neuberg" }),
  ev("2026-10-05T07:00:00", "2026-10-05T10:00:00", "Abholen <b>Jonny</b>"), ev("2026-10-06T00:00:00", "2026-10-06T23:59:00", "Geburtstag", { category: "privat" })] });
check("Kalender: nach Tagen gruppiert, nach Uhrzeit sortiert", has(h, "Montag, 05.10.2026", "Dienstag, 06.10.2026") && h.indexOf("Abholen") < h.indexOf("Baustelle – Bad"));
check("Kalender: Zeiten, Ort, Kategorie-Marken, ganztägig", has(h, "08:00–17:00", "Bahnhofstraße 1, Neuberg", "Baustelle</span>", "Ich</span>", "Privat</span>", "ganztägig"));
check("Kalender: Titel werden maskiert", !h.includes("<b>Jonny") && h.includes("&lt;b&gt;Jonny"));
check("Kalender: Quelle Google (Gmail) wird genannt", h.includes("Google Kalender (Gmail)"));
check("Kalender leer / nicht verfügbar / Fehler", kalenderView({ available: true, events: [] }).includes("Keine Termine") && kalenderView({ available: false, detail: "kaputt" }).includes("nicht verfügbar") && kalenderView({ fehler: "x" }).includes("nicht erreichbar"));

// ── Standort ──
h = standortView({ eingerichtet: true, letzte_position_vor_minuten: 3, result: { status: "alles gut" }, last_check: "2026-10-03 10:00:00" });
check("Standort: eingerichtet, frische Position, letzte Prüfung", has(h, "eingerichtet", "frisch genug", "alles gut", "OwnTracks") && has(h, ">3<small>Min. alt</small>"));
check("Standort: alte/keine Position wird als solche gezeigt", standortView({ eingerichtet: true, letzte_position_vor_minuten: 90, result: {} }).includes("zu alt oder keine") && standortView({ eingerichtet: false, letzte_position_vor_minuten: null }).includes("nicht eingerichtet"));
check("Standort: sagt ehrlich, dass Jarvis nicht anrufen kann", h.includes("Anrufen kann Mia nicht"));

// ── Rahmen ──
h = shellHTML("kalender", `<script>alert(1)</script>`, "<p>INHALT</p>", "10:00:00");
check("Rahmen: alle Module in der Navigation, aktives markiert", MODULES.every((m) => h.includes(`href="#/${m.id}"`)) && /nav-item active" href="#\/kalender"/.test(h));
check("Rahmen: Dashboard-Bauteile (Seitenleiste, Topbar, Mobil-Navigation, Kern)", has(h, 'class="sidebar"', 'class="topbar"', "mobile-nav", 'class="core"', "REYES", "INHALT"));
check("Rahmen: Benutzername wird maskiert", !h.includes("<script>alert(1)") && h.includes("&lt;script&gt;"));
check("Login: Fehlermeldung wird maskiert und angezeigt", has(loginHTML("Falsch <x>"), "Falsch &lt;x&gt;", 'role="alert"', 'type="password"', 'autocomplete="current-password"') && !loginHTML().includes('role="alert"'));



// ═══ Der Kern (goldenes Hologramm) ═══
{
  const calls = { fillRect: 0, frames: 0 };
  const ctx = new Proxy({}, { get(t, k) { if (k === "createRadialGradient") return () => ({ addColorStop() {} }); if (k in t) return t[k]; return () => { if (k === "fillRect") calls.fillRect++; if (k === "clearRect") calls.frames++; }; }, set(t, k, v) { t[k] = v; return true; } });
  const canvas = { getBoundingClientRect: () => ({ width: 380, height: 240 }), getContext: () => ctx, width: 0, height: 0 };
  let reduce = false, errors = 0;
  globalThis.window = { matchMedia: () => ({ matches: reduce }), devicePixelRatio: 2 };
  globalThis.requestAnimationFrame = (f) => setTimeout(() => { try { f(performance.now()); } catch (e) { errors++; console.log(e); } }, 4);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
  globalThis.ResizeObserver = class { constructor(cb) { this.cb = cb; } observe() { this.cb(); } disconnect() {} };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const core = createCore(canvas);
  for (const [st, lv] of [["idle", 0], ["listening", 0.6], ["thinking", 0], ["speaking", 0.9], ["speaking", 0.1], ["idle", 0]]) { core.set(st, lv); await sleep(70); }
  check("Kern: zeichnet fortlaufend in allen Zuständen ohne Fehler", calls.frames >= 15 && errors === 0, { frames: calls.frames, errors });
  check("Kern: pro Bild tausende Lichtpunkte (Gehirn aus ~3600 Punkten)", calls.fillRect / calls.frames > 3000, calls.fillRect / calls.frames);
  check("Kern: Zeichenfläche wird für scharfe Darstellung skaliert (Bildschirmdichte 2)", canvas.width === 760 && canvas.height === 480, [canvas.width, canvas.height]);
  core.set("unbekannt", 5); await sleep(20);
  check("Kern: ungültiger Zustand oder Pegel bringt ihn nicht zum Absturz", errors === 0);
  core.stop(); const f0 = calls.frames; await sleep(60);
  check("Kern: nach stop() wird nicht mehr gezeichnet", calls.frames === f0, [f0, calls.frames]);
  reduce = true; const before = calls.frames; const still = createCore(canvas); await sleep(80);
  check("Kern: bei 'reduzierte Bewegung' steht er still (nur ein Standbild, keine Animation)", calls.frames - before >= 1 && calls.frames - before <= 3, calls.frames - before);
  still.stop();
}
check("Rahmen des Jarvis-Moduls: Kern, Kennzeichnung, Untertitel und Platz für das Gespräch", has(jarvisFrameHTML(), "hero-core", "brain-core", 'aria-label="Mia Kern"', "hud-state", "core-caption", 'id="chat-panel"'));
check("Mikrofon-Pegel: ohne Web-Audio gibt es keinen Pegelmesser (kein Absturz), mit Web-Audio kommt ein Wert 0 bis 1", (() => {
  if (startMeter({}, undefined) !== null) return false;
  class AC { createMediaStreamSource() { return { connect() {}, disconnect() {} }; } createAnalyser() { return { fftSize: 0, getByteTimeDomainData(b) { b.fill(128); b[0] = 255; b[1] = 0; } }; } close() {} }
  const m = startMeter({}, AC); const l = m.level(); m.stop(); return l > 0 && l <= 1;
})());

// ═══ Jarvis: Gespräch ═══
check("Jarvis ist das erste Modul und hat eine eigene Oberfläche", MODULES[0].id === "jarvis" && MODULES[0].custom === true);
check("Aufnahmeformat: iPhone (mp4) vor webm, Android nimmt webm, sonst leer", pickAudioMime({ isTypeSupported: (t) => t === "audio/mp4" || t === "audio/webm" }) === "audio/mp4" && pickAudioMime({ isTypeSupported: (t) => t.startsWith("audio/webm") }) === "audio/webm;codecs=opus" && pickAudioMime({ isTypeSupported: () => false }) === "" && pickAudioMime(undefined) === "");
check("Vorlesetext: ohne Markdown, Links, Codeblöcke und Listenzeichen", toSpeakable("## Titel\n**Wichtig:** siehe [Doku](https://x.de/a) und https://y.de\n- eins\n- zwei `code` ```\nblock\n```") === "Titel Wichtig: siehe Doku und eins zwei code");
check("Vorlesetext: lange Antworten werden am Satzende gekürzt", (() => { const t = toSpeakable("Ein Satz. ".repeat(400), 100); return t.length <= 100 && t.endsWith("."); })());
check("Vorlesetext: leer bleibt leer", toSpeakable("") === "" && toSpeakable(null) === "");
check("Sprechblase: Text wird maskiert, Schreibcursor beim Antworten, Fehlermarke", has(bubbleHTML({ role: "assistant", content: "<b>x</b>", status: "streaming" }), "&lt;b&gt;x", "cursor") && has(bubbleHTML({ role: "user", content: "Hi", status: "complete" }), "bubble mine", "Du") && bubbleHTML({ role: "assistant", content: "", status: "error" }).includes("Fehler beim Antworten"));

const noSleep = async () => {};
{ // Warten auf die Antwort
  let n = 0; const seen = [];
  const api = async () => ({ messages: [{ id: "a1", content: ["", "Hal", "Hallo Paul"][Math.min(n++, 2)], status: n < 3 ? "streaming" : "complete" }], active_runs: [] });
  const r = await waitForAnswer(api, "c1", "a1", { onUpdate: (m) => seen.push(m.content), sleep: noSleep });
  check("Warten: Zwischenstände kommen an, Ende bei 'complete'", r.message.status === "complete" && r.message.content === "Hallo Paul" && seen.includes("Hal") && !r.timedOut, seen);
  const r2 = await waitForAnswer(async () => ({ messages: [{ id: "a1", content: "x", status: "error" }] }), "c", "a1", { sleep: noSleep });
  check("Warten: 'error' beendet das Warten", r2.message.status === "error" && !r2.timedOut);
  const r3 = await waitForAnswer(async () => ({ messages: [{ id: "a1", content: "denkt", status: "streaming" }] }), "c", "a1", { sleep: noSleep, timeout: 5000, interval: 1000 });
  check("Warten: Zeitüberschreitung wird gemeldet statt ewig zu warten", r3.timedOut === true && r3.message.content === "denkt");
  let f = 0;
  const r4 = await waitForAnswer(async () => { if (f++ < 2) throw { status: 0 }; return { messages: [{ id: "a1", content: "ok", status: "complete" }] }; }, "c", "a1", { sleep: noSleep });
  check("Warten: kurze Netzaussetzer werden überbrückt", r4.message.content === "ok");
  let threw = null; try { await waitForAnswer(async () => { throw { status: 401 }; }, "c", "a1", { sleep: noSleep }); } catch (e) { threw = e; }
  check("Warten: Abmeldung (401) bricht sofort ab", threw && threw.status === 401);
  let threw2 = null; try { await waitForAnswer(async () => { throw { status: 0 }; }, "c", "a1", { sleep: noSleep }); } catch (e) { threw2 = e; }
  check("Warten: dauerhaft kein Netz → Fehler nach fünf Versuchen", threw2 && threw2.status === 0);
}

// Ansicht
let hc = chatHTML({ messages: [], status: "bereit", speak: true, caps: { speech_to_text: { available: true }, text_to_speech: { available: true } } });
check("Ansicht: leer → Einladung, Mikrofon, Eingabefeld, Vorlesen-Schalter", has(hc, "Sag etwas zu Mia", 'data-act="mic"', 'id="chat-input"', 'data-act="toggle-speak"', "checked") && !hc.includes("nicht verfügbar"));
hc = chatHTML({ messages: [], status: "listening", speak: true, caps: null });
check("Ansicht: beim Aufnehmen roter Knopf, Stopp-Symbol und Hinweistext", has(hc, "mic rec", "Ich höre zu", "Aufnahme beenden und senden"));
hc = chatHTML({ messages: [], status: "thinking", speak: true, caps: null });
check("Ansicht: beim Denken sind Mikrofon, Eingabe und Senden gesperrt", (hc.match(/disabled/g) || []).length >= 3 && hc.includes("Mia denkt nach"));
hc = chatHTML({ messages: [], status: "bereit", speak: false, caps: { speech_to_text: { available: false, detail: "Kein Sprachdienst." }, text_to_speech: { available: false, detail: "Kein Ton." } } });
check("Ansicht: fehlende Server-Sprachfunktionen werden ehrlich genannt, Tippen bleibt möglich", has(hc, "Spracheingabe ist auf dem Server nicht verfügbar", "Kein Sprachdienst.", "Vorlesen ist auf dem Server nicht verfügbar") && /data-act="mic"[^>]*disabled/.test(hc) && !/id="chat-input"[^>]*disabled/.test(hc));
check("Ansicht: Fehlermeldung wird maskiert angezeigt", chatHTML({ messages: [], status: "bereit", error: "<x>" }).includes("&lt;x&gt;"));

// ── Ablauf mit Attrappen: Tippen, Sprechen, Vorlesen ──
function fakeEl() {
  const handlers = {}, input = { value: "", focus() {} }, box = { scrollHeight: 0, scrollTop: 0, clientHeight: 0, innerHTML: "" };
  const panel = { innerHTML: "", handlers, input, addEventListener(t, f) { handlers[t] = f; }, querySelector(sel) { return sel === "#chat-input" ? input : sel === "#msgs" ? box : null; } };
  const hud = { textContent: "" }, cap = { textContent: "" }, canvas = {};
  return { frame: "", handlers, input, panel, hud, cap, canvas,
    get innerHTML() { return panel.innerHTML; }, set innerHTML(v) { this.frame = v; },
    querySelector(sel) { return sel === "#chat-panel" ? panel : sel === "canvas" ? canvas : sel === "#hud-state" ? hud : sel === "#core-caption" ? cap : null; }, addEventListener() {} };
}
function harness({ tts = true } = {}) {
  const log = [], store = {}; let polls = 0;
  const api = async (path, opts = {}) => {
    log.push([opts.method || "GET", path, opts.body]);
    if (path === "/api/voice/capabilities") return { speech_to_text: { available: true }, text_to_speech: { available: tts } };
    if (path === "/api/chat/conversations" && opts.method === "POST") return { conversation: { id: "conv1" } };
    if (path === "/api/chat/conversations/conv1" && !opts.method) { polls++; return { messages: polls < 3 ? [] : [{ id: "u1", role: "user", content: "x", status: "complete" }, { id: "a1", role: "assistant", content: "Es ist **halb drei**.", status: "complete" }], active_runs: [] }; }
    if (path === "/api/chat/conversations/conv1/messages") return { user_message: { id: "u1", role: "user", content: opts.body.content, status: "complete" }, run: { id: "r1" }, message: { id: "a1", role: "assistant", content: "", status: "streaming" } };
    throw { status: 404, detail: "unbekannt " + path };
  };
  const spoken = [], formSent = [];
  const apiBlob = async (path, opts) => { spoken.push(opts.body.text); return new Blob(["mp3"]); };
  const apiForm = async (path, form) => { if (path === "/api/chat/attachments") { const f = form.get("file"); return { attachment: { path: "uploads/" + f.name, name: f.name, mime: f.type, size: f.size } }; } formSent.push([path, form.get("file").name, form.get("language")]); return { text: "Wie spät ist es" }; };
  class Audio { constructor() { Audio.last = this; } play() { setTimeout(() => this.onended && this.onended(), 0); return Promise.resolve(); } pause() {} dispatchEvent() {} }
  class MR { static isTypeSupported(t) { return t === "audio/mp4"; } constructor(stream, o) { this.mimeType = o?.mimeType; MR.last = this; } start() {} stop() { this.ondataavailable({ data: new Blob([new Uint8Array(3000)]) }); this.onstop(); } }
  const stopped = []; const stream = { getTracks: () => [{ stop: () => stopped.push(1) }] };
  const coreCalls = [], coreStopped = [], meterStopped = [];
  const coreFactory = () => ({ set: (...a) => coreCalls.push(a), stop: () => coreStopped.push(1) });
  const lvl = { v: 0.5 };
  const meterFactory = () => ({ level: () => lvl.v, stop: () => meterStopped.push(1) });
  const chat = createChat({ coreFactory, meterFactory, api, apiForm, apiBlob, storage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } }, mediaDevices: { getUserMedia: async () => stream }, MediaRecorderCtor: MR, AudioCtor: Audio, doc: { activeElement: null } });
  return { chat, lvl, log, store, spoken, formSent, stopped, Audio, MR, coreCalls, coreStopped, meterStopped };
}
{ const h = harness(); const el = fakeEl(); await h.chat.mount(el);
  check("Rahmen: Kern-Zeichenfläche, Kennzeichnung und Untertitel stehen da", has(el.frame, "hero-core", "brain-core", "hud-state", "core-caption", 'id="chat-panel"') && el.hud.textContent === "Bereit" && el.cap.textContent.length > 3);
  check("Start: Fähigkeiten geholt, neues Gespräch angelegt und die ID gemerkt", h.log.some((l) => l[1] === "/api/voice/capabilities") && h.log.some((l) => l[0] === "POST" && l[1] === "/api/chat/conversations") && h.store[STORE] === "conv1");
  el.input.value = "Wie spät ist es?"; el.handlers.submit({ preventDefault() {} });
  await new Promise((r) => setTimeout(r, 2500));
  const post = h.log.find((l) => l[1].endsWith("/messages"));
  check("Getippt: Nachricht geht ohne Sprachkanal und ohne Stream an Jarvis", post && post[2].content === "Wie spät ist es?" && post[2].channel === "" && post[2].stream === false, post);
  check("Antwort erscheint in der Ansicht, Markdown-Sterne bleiben (nur der Ton wird bereinigt)", el.innerHTML.includes("halb drei") && h.chat.state.status === "bereit", h.chat.state.status);
  check("Vorlesen: genau einmal, ohne Sterne", h.spoken.length === 1 && h.spoken[0] === "Es ist halb drei.", h.spoken);
  check("Eingabefeld ist nach dem Senden leer", el.input.value === "");
  h.chat.unmount(); }
{ const h = harness(); const el = fakeEl(); await h.chat.mount(el);
  el.handlers.click({ target: { closest: () => ({ dataset: { act: "mic" } }) } });
  await new Promise((r) => setTimeout(r, 260));
  check("Mikrofon: erster Tipp startet die Aufnahme im iPhone-Format (mp4)", h.chat.state.status === "listening" && h.MR.last.mimeType === "audio/mp4", h.chat.state.status);
  el.handlers.click({ target: { closest: () => ({ dataset: { act: "mic" } }) } });
  await new Promise((r) => setTimeout(r, 2800));
  check("Mikrofon: zweiter Tipp sendet die Aufnahme als .m4a mit Sprache 'de' an die Erkennung", h.formSent.length === 1 && h.formSent[0][0] === "/api/voice/transcribe" && h.formSent[0][1] === "aufnahme.m4a" && h.formSent[0][2] === "de", h.formSent);
  check("Mikrofon: erkannter Text geht mit Sprachkanal an Jarvis (kurze, gesprochene Antworten)", h.log.find((l) => l[1].endsWith("/messages"))?.[2].channel === "voice");
  check("Mikrofon wird nach der Aufnahme wirklich freigegeben (kein roter Punkt im iPhone)", h.stopped.length >= 1);
  check("Antwort wird vorgelesen", h.spoken.length === 1);
  const modes = h.coreCalls.map((c) => c[0]);
  check("Zwischen 'Denkt' und 'Spricht' blitzt kein 'Bereit' auf (der Kern bleibt an)", !(() => { const m = h.coreCalls.map((c) => c[0]); const a = m.lastIndexOf("thinking"), b = m.indexOf("speaking", a); return m.slice(a, b).includes("idle"); })(), h.coreCalls.map((c) => c[0]));
  check("Kern folgt dem Gespräch: idle → listening → thinking → speaking → idle", (() => { const seq = modes.filter((m, i) => m !== modes[i - 1]); const want = ["idle", "listening", "thinking", "speaking", "idle"]; let k = 0; for (const m of seq) if (m === want[k]) k++; return k === want.length; })(), modes);
  check("Beim Zuhören bekommt der Kern die echte Mikrofon-Lautstärke", h.coreCalls.some((c) => c[0] === "listening" && c[1] === 0.5), h.coreCalls.slice(0, 6));
  check("Beim Sprechen pulsiert der Kern (Pegel zwischen 0 und 1)", h.coreCalls.filter((c) => c[0] === "speaking" && c[1] > 0).every((c) => c[1] > 0.3 && c[1] <= 1));
  check("Der Pegelmesser wird nach der Aufnahme beendet (kein offener Audio-Kontext)", h.meterStopped.length === 1);
  check("Kennzeichnung im Kern zeigt den Zustand (zuletzt wieder 'Bereit')", el.hud.textContent === "Bereit");
  h.chat.unmount();
  check("Verlassen des Moduls stoppt den Kern (keine Animation im Hintergrund, spart Akku)", h.coreStopped.length === 1); }
{ const h = harness({ tts: false }); const el = fakeEl(); await h.chat.mount(el);
  el.input.value = "Hallo"; el.handlers.submit({ preventDefault() {} }); await new Promise((r) => setTimeout(r, 2500));
  check("Ohne Vorlese-Dienst am Server: Antwort erscheint, es wird nicht vorgelesen und nichts geht kaputt", h.spoken.length === 0 && el.innerHTML.includes("halb drei") && h.chat.state.status === "bereit");
  h.chat.unmount(); }
{ const h = harness(); const el = fakeEl(); await h.chat.mount(el);
  const noMic = createChat({ api: async () => ({ conversation: { id: "c" }, messages: [] }), apiForm: async () => ({}), apiBlob: async () => new Blob([]), storage: { getItem: () => null, setItem() {} }, mediaDevices: { getUserMedia: async () => { throw new Error("verweigert"); } }, MediaRecorderCtor: h.MR, AudioCtor: h.Audio, doc: { activeElement: null } });
  const el2 = fakeEl(); await noMic.mount(el2);
  el2.handlers.click({ target: { closest: () => ({ dataset: { act: "mic" } }) } }); await new Promise((r) => setTimeout(r, 30));
  check("Kern kaputt (keine Zeichenfläche): das Gespräch funktioniert trotzdem", noMic.state.status === "bereit" && el2.innerHTML.includes("Schreib Mia"));
  check("Mikrofon verweigert: klare Anleitung statt stillem Fehler, Tippen geht weiter", noMic.state.status === "bereit" && el2.innerHTML.includes("Das Mikrofon ist nicht freigegeben") && !el2.innerHTML.includes('id="chat-input" name="t" placeholder="Schreib Mia …" enterkeyhint="send" disabled'));
  h.chat.unmount(); noMic.unmount(); }


// ── Freisprechen: Sprechpausen-Erkennung ──
{ const v = createVad(); const ev = []; let t = 0;
  const run = (level, ms) => { for (const end = t + ms; t < end; t += 80) { const e = v.feed(level, t); if (e) ev.push(e + "@" + t); } };
  run(0.02, 1000); run(0.05, 1000);
  check("Sprechpause: Stille und leises Rauschen lösen nichts aus", ev.length === 0, ev);
  run(0.6, 100); run(0.02, 400);
  check("Sprechpause: ein kurzes Geräusch (Klopfen, Tür) startet keine Aufnahme", ev.length === 0, ev);
  run(0.5, 400);
  check("Sprechpause: anhaltende Stimme startet", ev.length === 1 && ev[0].startsWith("start"), ev);
  run(0.02, 700); run(0.5, 300);
  check("Sprechpause: kurze Lücken zwischen Wörtern beenden nichts", ev.length === 1, ev);
  run(0.02, 1500);
  check("Sprechpause: nach gut einer Sekunde Stille ist Schluss (genau einmal)", ev.length === 2 && ev[1].startsWith("end"), ev);
  const noisy = createVad(); let e2 = 0, t2 = 0;
  for (; t2 < 6000; t2 += 80) if (noisy.feed(0.16, t2)) e2++;
  check("Sprechpause: dauerhafte Baustellen-Geräusche zählen nach dem Einpegeln nicht als Sprache", e2 <= 1, e2); }

// ── Datei anhängen ──
{ const h = harness(); const el = fakeEl(); await h.chat.mount(el);
  const file = new File([new Uint8Array(12)], "angebot.pdf", { type: "application/pdf" });
  const inp = { id: "chat-file", files: [file], value: "x", closest: () => null };
  el.handlers.change({ target: inp }); await new Promise((r) => setTimeout(r, 30));
  check("Anhang: Datei wird hochgeladen und erscheint als Chip vor dem Senden", h.chat.state.pending.length === 1 && h.chat.state.pending[0].path === "uploads/angebot.pdf" && el.innerHTML.includes("angebot.pdf"), h.chat.state.pending);
  check("Anhang: Auswahlfeld wird geleert (dieselbe Datei kann erneut gewählt werden)", inp.value === "");
  el.input.value = "Prüf das bitte"; el.handlers.submit({ preventDefault() {} }); await new Promise((r) => setTimeout(r, 2500));
  const post = h.log.find((l) => l[1].endsWith("/messages"));
  check("Anhang: geht mit Pfad, Name und Typ an Jarvis, Liste danach leer", post && post[2].attachments.length === 1 && post[2].attachments[0].path === "uploads/angebot.pdf" && post[2].attachments[0].mime === "application/pdf" && h.chat.state.pending.length === 0, post);
  check("Anhang: Datei ohne Text darf allein gesendet werden", (() => { const c = { attachments: [{ name: "a.pdf" }] }; return bubbleHTML({ role: "user", content: "", status: "complete", ...c }).includes("a.pdf"); })());
  h.chat.unmount(); }

// ── Freisprechen: ganzer Ablauf ──
{ const h = harness(); const el = fakeEl(); await h.chat.mount(el);
  h.lvl.v = 0.01;
  el.handlers.click({ target: { closest: () => ({ dataset: { act: "live" } }) } }); await new Promise((r) => setTimeout(r, 300));
  check("Freisprechen: Knopf schaltet das Zuhören dauerhaft ein", h.chat.state.live && h.chat.state.status === "listening", h.chat.state.status);
  await new Promise((r) => setTimeout(r, 400));
  check("Freisprechen: Stille wird nicht verschickt", h.formSent.length === 0);
  h.lvl.v = 0.6; await new Promise((r) => setTimeout(r, 500)); h.lvl.v = 0.01;
  await new Promise((r) => setTimeout(r, 1700));
  check("Freisprechen: nach dem Sprechen geht die Aufnahme ohne Tippen zur Erkennung", h.formSent.length === 1 && h.formSent[0][1] === "aufnahme.m4a", h.formSent);
  await new Promise((r) => setTimeout(r, 3800));
  check("Freisprechen: Antwort wird vorgelesen und danach hört Jarvis von selbst wieder zu", h.spoken.length === 1 && h.chat.state.live && h.chat.state.status === "listening", [h.spoken.length, h.chat.state.status]);
  check("Freisprechen: Nachricht ging mit Sprachkanal raus", h.log.find((l) => l[1].endsWith("/messages"))?.[2].channel === "voice");
  const before = h.stopped.length;
  el.handlers.click({ target: { closest: () => ({ dataset: { act: "live" } }) } }); await new Promise((r) => setTimeout(r, 100));
  check("Freisprechen beenden: Mikrofon und Pegelmesser sind frei, Zustand wieder bereit", !h.chat.state.live && h.stopped.length > before && h.meterStopped.length >= 1 && h.chat.state.status === "bereit", h.chat.state.status);
  h.chat.unmount(); }


// ── Meldungen ──
{ const push = { konfiguriert: true, abo_link: "ntfy://ntfy.sh/geheim-thema", thema: "geheim-thema", regel: "Warnungen", anruf: { konfiguriert: false, fehlt: ["TWILIO_ACCOUNT_SID"] } };
  const v = meldungenView({ push, list: { notifications: [{ title: "<b>Kollision</b>", body: "Termin A", severity: "warning", created_at: "2026-09-20T10:00:00Z" }] }, freig: { approvals: [{ id: "ap1", action: "email.send", target: "kunde@x.de" }] } });
  check("Meldungen: ntfy-Link, Testknopf und Thema zum Abtippen", has(v, 'href="ntfy://ntfy.sh/geheim-thema"', 'data-act="push-test"', "geheim-thema"));
  check("Meldungen: Freigaben mit Knöpfen und ID, Titel maskiert", has(v, 'data-act="freigabe-ja" data-id="ap1"', 'data-act="freigabe-nein"', "email.send") && v.includes("&lt;b&gt;Kollision"));
  check("Meldungen: Anruf nicht eingerichtet → ehrlich, was fehlt, kein Testknopf", has(v, "TWILIO_ACCOUNT_SID") && !v.includes('data-act="anruf-test"'));
  const v2 = meldungenView({ push: { ...push, anruf: { konfiguriert: true, nummer: "+49…567", regel: "nur bei kritischen Fällen" } }, list: { notifications: [] }, freig: { approvals: [] } });
  check("Meldungen: Anruf eingerichtet → Testanruf, ohne Freigaben kein Freigabe-Kasten, leere Liste freundlich", has(v2, 'data-act="anruf-test"', "+49…567", "Alles ruhig") && !v2.includes("Wartet auf dich"));
  check("Meldungen: ohne eingerichtetes Thema keine Anleitung mit leerem Link", !meldungenView({ push: { konfiguriert: false }, list: {} }).includes("ntfy://"));
  check("Meldungen: Server nicht erreichbar → Fehlertext statt leerer Seite", meldungenView({ fehler: "Nope" }).includes("Nope"));
  check("Menü: Meldungen hat ein Zähler-Kästchen (Mobil und Seitenleiste), Buchhaltung ist nicht mehr im Menü", shellHTML("meldungen", "P", "").split("nbadge").length - 1 === 2 && !MODULES.some((m) => m.id === "buchhaltung")); }

// ── Auslieferung: keine Datei darf beim Deploy vergessen werden (ein fehlendes Modul = leere App) ──
import { readdirSync, existsSync } from "node:fs";
const dir = new URL("../reyes-app/", import.meta.url);
const deploy = readFileSync(new URL("deploy.sh", dir), "utf8");
const mjs = readdirSync(dir).filter((f) => f.endsWith(".mjs"));
const imports = mjs.flatMap((f) => [...readFileSync(new URL(f, dir), "utf8").matchAll(/from "\.\/([A-Za-z0-9_.-]+)"/g)].map((m) => m[1]));
check("Auslieferung: deploy.sh kopiert alle *.mjs-Module per Muster (nichts wird vergessen)", deploy.includes('"$R"/*.mjs'), deploy.match(/cp -a "\$R".*/g));
check("Auslieferung: jede importierte Datei existiert im Quellordner", imports.length >= 2 && imports.every((f) => existsSync(new URL(f, dir))), imports);
const swFiles = [...readFileSync(new URL("sw.js", dir), "utf8").matchAll(/"\.\/([A-Za-z0-9_.-]+)"/g)].map((m) => m[1]);
check("Auslieferung: alles, was der Dienst im Hintergrund vorhält, existiert (sonst schlägt die Installation fehl)", swFiles.length >= 6 && swFiles.every((f) => existsSync(new URL(f, dir))), swFiles.filter((f) => !existsSync(new URL(f, dir))));
check("Auslieferung: index.html lädt app.mjs als Modul und beide Stilblätter", (() => { const h = readFileSync(new URL("index.html", dir), "utf8"); return h.includes('type="module" src="app.mjs"') && h.includes("dashboard.css") && h.includes("app.css"); })());

// ── Sicherheit der Anbindung (statische Prüfung des Quelltexts) ──
const app = readFileSync(new URL("../reyes-app/app.mjs", import.meta.url), "utf8");
const sw = readFileSync(new URL("../reyes-app/sw.js", import.meta.url), "utf8");
check("app: CSRF-Kopfzeile nur bei schreibenden Aufrufen, Cookies nur für dieselbe Herkunft", app.includes('if (method !== "GET") headers["x-csrf-token"]') && app.includes('credentials: "same-origin"'));
check("app: keine Geheimnisse und keine fremden Adressen im Code", !/api[_-]?key|token\s*[:=]\s*["'][A-Za-z0-9]{16,}|https?:\/\/(?!fonts\.)/i.test(app.replace(/\/\/.*$/gm, "")));
check("app: Anmeldung und Abmeldung laufen über die Jarvis-Schnittstelle", app.includes("/api/auth/login") && app.includes("/api/auth/logout"));
const chatSrc = readFileSync(new URL("../reyes-app/chat.mjs", import.meta.url), "utf8");
check("Sprache: Aufnahme wird nur im Browser verarbeitet und nur an die eigene Jarvis-Schnittstelle geschickt", chatSrc.includes("/api/voice/transcribe") && chatSrc.includes("/api/voice/speak") && !/https?:\/\//.test(chatSrc.replace(/\/\/.*$/gm, "").replace(/data:audio[^"]*/g, "")));
check("Dienst im Hintergrund: /api/ wird nie zwischengespeichert", sw.includes('u.pathname.startsWith("/api/")') && sw.includes("return;"));

console.log("\n" + (fails ? `${fails} FAILED` : "ALL PASSED"));
process.exit(fails ? 1 : 0);
