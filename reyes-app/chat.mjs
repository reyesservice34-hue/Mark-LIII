// Reyes Office — Gespräch mit Mia. Der Kern (Formate, Warten auf die Antwort, Sprechtext) besteht aus reinen Funktionen
// und wird ohne Browser getestet (tests/test_reyes_app.mjs). Nur createChat() greift auf Mikrofon, Lautsprecher und Seite zu.
import { chatHTML, bubbleHTML, EMPTY_CHAT, jarvisFrameHTML, HUD_STATE, STATUS_TEXT, statusText } from "./render.mjs";
import { createCore } from "./core.mjs";

export const STORE = "reyes.conversation";
export const MAX_RECORD_MS = 60_000;

/** Das Aufnahmeformat, das dieses Gerät kann: Safari/iPhone nimmt mp4 auf, Chrome/Android webm. */
export function pickAudioMime(MR) {
  const candidates = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"];
  return candidates.find((t) => MR && typeof MR.isTypeSupported === "function" && MR.isTypeSupported(t)) || "";
}

/** Antworttext → Text zum Vorlesen: ohne Markdown-Zeichen, Links und Adressen; höchstens ~1500 Zeichen, am Satzende gekürzt. */
export function toSpeakable(text, max = 1500) {
  let t = String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*•]\s+/gm, "")
    .replace(/(\*\*|__|\*|_)/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (t.length > max) {
    const cut = t.slice(0, max);
    const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
    t = (end > max / 2 ? cut.slice(0, end + 1) : cut).trim();
  }
  return t;
}

const FINAL = new Set(["complete", "stopped", "error"]);
const defaultSleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Wartet, bis Mia mit seiner Antwort fertig ist. Fragt das Gespräch ab, bis die Antwort-Nachricht einen Endzustand hat
 * (complete, stopped, error). Zwischenstände gehen an onUpdate, damit der Text beim Schreiben mitwächst.
 * Gibt {message, timedOut} zurück; bei Zeitüberschreitung ist message der letzte bekannte Stand.
 */
export async function waitForAnswer(api, convId, messageId, { onUpdate = () => {}, onActivity = () => {}, interval = 900, timeout = 180_000, sleep = defaultSleep } = {}) {
  let waited = 0, last = null, failures = 0;
  while (waited <= timeout) {
    try {
      const d = await api(`/api/chat/conversations/${convId}`);
      failures = 0;
      const m = (d.messages || []).find((x) => x.id === messageId);
      if (d.active_runs && d.active_runs[0]) onActivity(d.active_runs[0]);
      if (m) {
        if (!last || last.content !== m.content || last.status !== m.status) onUpdate(m);
        last = m;
        if (FINAL.has(m.status)) return { message: m, timedOut: false };
      }
    } catch (e) {
      if (e && e.status === 401) throw e;              // abgemeldet: nicht weiter fragen
      if (++failures >= 5) throw e;                     // fünfmal hintereinander kein Netz: aufgeben
    }
    await sleep(interval);
    waited += interval;
  }
  return { message: last, timedOut: true };
}

const CORE_MODE = { bereit: "idle", listening: "listening", transcribing: "thinking", thinking: "thinking", speaking: "speaking" };

/** Die echte Lautstärke des Mikrofons (0..1), damit der Kern beim Zuhören mit deiner Stimme atmet. Ohne Web-Audio: null, der Kern läuft dann mit festem Pegel. */
export function startMeter(stream, AC = globalThis.AudioContext || globalThis.webkitAudioContext) {
  if (!AC) return null;
  try {
    const ctx = new AC(), src = ctx.createMediaStreamSource(stream), an = ctx.createAnalyser();
    if (ctx.state === "suspended" && ctx.resume) ctx.resume().catch(() => {});   // iPhone startet Web-Audio angehalten
    an.fftSize = 512; src.connect(an);
    const buf = new Uint8Array(an.fftSize);
    return {
      level() { an.getByteTimeDomainData(buf); let sum = 0; for (const v of buf) { const x = (v - 128) / 128; sum += x * x; } return Math.min(1, Math.sqrt(sum / buf.length) * 4); },
      stop() { try { src.disconnect(); ctx.close(); } catch { /* schon zu */ } },
    };
  } catch { return null; }
}

/**
 * Erkennt Sprechanfang und Sprechende aus dem Mikrofon-Pegel (0..1). feed(pegel, jetztMs) → "start" | "end" | null.
 * Die Schwelle passt sich dem Umgebungslärm an (langsam mitlaufender Grundpegel), damit Auto, Baustelle oder Lüfter nicht als Sprache zählen.
 * Kurze Lücken zwischen Wörtern (unter silenceMs) beenden nichts; ein kurzes Geräusch (unter minSpeechMs) startet nichts.
 */
export function createVad({ base = 0.12, silenceMs = 1300, minSpeechMs = 250, gapMs = 200 } = {}) {
  let floor = 0.02, speaking = false, since = null, lastLoud = null;
  return {
    feed(level, t) {
      const thr = Math.max(base, floor * 3);
      if (level >= thr) {
        if (since === null) since = t;
        lastLoud = t;
        if (!speaking && t - since >= minSpeechMs) { speaking = true; return "start"; }
        return null;
      }
      if (!speaking) { floor = floor * 0.97 + level * 0.03; if (lastLoud === null || t - lastLoud > gapMs) since = null; return null; }
      if (t - lastLoud >= silenceMs) { speaking = false; since = null; lastLoud = null; return "end"; }
      return null;
    },
    reset() { speaking = false; since = null; lastLoud = null; },
    get speaking() { return speaking; },
  };
}

const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

export function createChat({ api, apiForm, apiBlob, storage = globalThis.localStorage, mediaDevices = globalThis.navigator?.mediaDevices, MediaRecorderCtor = globalThis.MediaRecorder, AudioCtor = globalThis.Audio, doc = globalThis.document, coreFactory = createCore, meterFactory = startMeter }) {
  const s = { convId: "", messages: [], status: "bereit", speak: true, caps: null, error: "", recording: null, activity: "", pending: [], uploading: false, live: null };
  let el = null, root = null, core = null, ticker = null, meter = null, audio = null, alive = true;

  const get = (k) => { try { return storage.getItem(k); } catch { return null; } };
  const set = (k, v) => { try { storage.setItem(k, v); } catch { /* privater Modus */ } };
  const q = (sel) => el && el.querySelector(sel);

  function paintMessages(stick = true) {
    const box = q("#msgs"); if (!box) return;
    const nearEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.innerHTML = s.messages.length ? s.messages.map(bubbleHTML).join("") : EMPTY_CHAT;
    if (stick && nearEnd) box.scrollTop = box.scrollHeight;
  }
  function paintStatus() {
    if (!el) return;
    const keep = q("#chat-input")?.value || "";
    const focused = doc && doc.activeElement === q("#chat-input");
    el.innerHTML = chatHTML(s);
    const inp = q("#chat-input"); if (inp) { inp.value = keep; if (focused) inp.focus(); }
    const box = q("#msgs"); if (box) box.scrollTop = box.scrollHeight;
  }
  function syncCore() {                                  // Kern, Kennzeichnung und Untertitel folgen dem Zustand
    if (core) core.set(CORE_MODE[s.status] || "idle", 0);
    const hud = root && root.querySelector("#hud-state"), cap = root && root.querySelector("#core-caption");
    if (hud) hud.textContent = HUD_STATE[s.status] || HUD_STATE.bereit;
    if (cap) cap.textContent = statusText(s);
  }
  function startTicker() {                               // Pegel für den Kern: beim Zuhören echt (Mikrofon), beim Sprechen ein gleichmäßiges Pulsieren
    if (ticker) return;
    ticker = setInterval(() => {
      if (!core) return;
      const t = Date.now() / 1000;
      if (s.status === "listening") core.set("listening", meter ? meter.level() : 0.3);
      else if (s.status === "speaking") core.set("speaking", 0.38 + 0.3 * Math.abs(Math.sin(t * 6.3)) + 0.12 * Math.abs(Math.sin(t * 11.7)));
    }, 90);
  }
  function setStatus(status, error = "") {
    if (status === "bereit" && s.live && alive) { status = "listening"; setTimeout(() => beginSegment(), 0); }   // Freisprechen: nach jeder Antwort wieder zuhören
    s.status = status; s.error = error; paintStatus(); syncCore();
  }

  function unlockAudio() {                               // iPhone erlaubt Ton erst nach einer Berührung: einmal still abspielen
    if (!audio) audio = new AudioCtor();
    audio.src = SILENCE; audio.play().catch(() => {});
  }

  async function ensureConversation() {
    const id = get(STORE);
    if (id) {
      try { const d = await api(`/api/chat/conversations/${id}`); s.convId = id; s.messages = d.messages || []; return; } catch (e) { if (e && e.status === 401) throw e; }
    }
    const c = await api("/api/chat/conversations", { method: "POST", body: { title: "Reyes Office" } });
    s.convId = c.conversation.id; s.messages = []; set(STORE, s.convId);
  }

  async function speakText(text) {
    const t = toSpeakable(text);
    if (!t || !s.speak || !(s.caps?.text_to_speech?.available !== false)) return setStatus("bereit");
    setStatus("speaking");
    try {
      const blob = await apiBlob("/api/voice/speak", { method: "POST", body: { text: t } });
      if (!audio) audio = new AudioCtor();
      const url = URL.createObjectURL(blob);
      audio.src = url;
      let blocked = false;
      await new Promise((resolve) => { audio.onended = resolve; audio.onerror = resolve; audio.play().catch(() => { blocked = true; resolve(); }); });
      URL.revokeObjectURL(url);
      if (blocked) s.error = "Das Gerät hat den Ton blockiert. Tippe einmal auf das Mikrofon oder den Bildschirm und versuche es erneut.";
    } catch (e) {
      if (e && e.status === 401) throw e;
      s.error = "Vorlesen ging nicht: " + (e?.detail || "Fehler");
    }
    if (alive && s.status === "speaking") setStatus("bereit", s.error);
  }

  async function send(text, spoken = false) {
    const content = String(text || "").trim();
    const files = s.pending.splice(0);
    if (!content && !files.length) return;
    s.error = ""; s.status = "thinking";
    const temp = { id: "tmp-" + Date.now(), role: "user", content, status: "complete", attachments: files };
    s.messages.push(temp, { id: "tmp-a", role: "assistant", content: "", status: "streaming" });
    paintStatus();
    let res;
    try {
      res = await api(`/api/chat/conversations/${s.convId}/messages`, { method: "POST", body: { content, stream: false, channel: spoken ? "voice" : "", attachments: files.map((a) => ({ path: a.path, name: a.name, mime: a.mime, size: a.size })) } });
    } catch (e) {
      s.messages = s.messages.filter((m) => !String(m.id).startsWith("tmp-"));
      s.pending.push(...files);                          // nichts geht verloren: die Anhänge bleiben für den nächsten Versuch
      if (e && e.status === 401) throw e;
      return setStatus("bereit", "Senden ging nicht: " + (e?.detail || "Fehler"));
    }
    const mine = s.messages.findIndex((m) => m.id === temp.id);
    if (mine >= 0 && res.user_message) s.messages[mine] = res.user_message;
    const ph = s.messages.findIndex((m) => m.id === "tmp-a");
    const aid = res.message?.id;
    if (ph >= 0 && res.message) s.messages[ph] = res.message;
    paintMessages();
    let final;
    try {
      final = await waitForAnswer(api, s.convId, aid, {
        onUpdate: (m) => { const i = s.messages.findIndex((x) => x.id === aid); if (i >= 0) s.messages[i] = m; paintMessages(); },
        onActivity: (r) => { s.activity = r.activity || r.status || ""; },
      });
    } catch (e) {
      if (e && e.status === 401) throw e;
      return setStatus("bereit", "Die Verbindung ist abgebrochen. Die Antwort steht später im Verlauf.");
    }
    if (final.timedOut) return setStatus("bereit", "Mia braucht länger als drei Minuten. Sieh später noch einmal nach.");
    if (final.message?.status === "error") return setStatus("bereit", "Mia konnte nicht antworten.");
    // Vorlesen (Schalter gilt für jede Antwort): direkt von „Denkt“ zu „Spricht“, ohne kurz „Bereit“ dazwischen (der Kern würde aufblitzen und ausgehen)
    if (final.message?.status === "complete" && s.speak) return speakText(final.message.content);
    return setStatus("bereit");
  }

  async function startRecording() {
    unlockAudio();
    if (!mediaDevices?.getUserMedia || !MediaRecorderCtor) return setStatus("bereit", "Dieses Gerät kann hier nicht aufnehmen. Du kannst tippen.");
    let stream;
    try { stream = await mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch { return setStatus("bereit", "Das Mikrofon ist nicht freigegeben. Erlaube es in den Einstellungen (Safari → Mikrofon) und versuche es noch einmal."); }
    const mime = pickAudioMime(MediaRecorderCtor);
    const rec = new MediaRecorderCtor(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    const done = new Promise((resolve) => { rec.onstop = () => resolve(new Blob(chunks, { type: rec.mimeType || mime || "audio/mp4" })); });
    meter = meterFactory(stream);
    s.recording = { rec, stream, done, timer: setTimeout(() => stopRecording(), MAX_RECORD_MS) };
    rec.start();
    setStatus("listening");
  }

  async function stopRecording() {
    const r = s.recording; if (!r) return;
    s.recording = null; clearTimeout(r.timer);
    try { r.rec.stop(); } catch { /* schon gestoppt */ }
    r.stream.getTracks().forEach((t) => t.stop());
    if (meter) { meter.stop(); meter = null; }
    const blob = await r.done;
    if (blob.size < 1200) return setStatus("bereit", "Ich habe nichts gehört. Sprich etwas länger und tippe dann erneut.");
    await transcribeAndSend(blob);
  }

  /** Aufnahme → Text → an Mia. quiet: im Freisprechen keine Fehlermeldung bei Stille oder Rauschen, es hört einfach weiter zu. */
  async function transcribeAndSend(blob, quiet = false) {
    setStatus("transcribing");
    const form = new FormData();
    form.append("file", blob, "aufnahme." + (blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm"));
    form.append("language", "de");
    let text = "";
    try { text = ((await apiForm("/api/voice/transcribe", form)) || {}).text || ""; }
    catch (e) { if (e && e.status === 401) throw e; return setStatus("bereit", "Sprache erkennen ging nicht: " + (e?.detail || "Fehler")); }
    if (!text.trim() || (quiet && text.replace(/[^\p{L}]/gu, "").length < 2)) return setStatus("bereit", quiet ? "" : "Ich habe dich nicht verstanden. Versuch es noch einmal.");
    await send(text, true);
  }

  // ── Freisprechen: das Mikrofon bleibt offen, Sprechpausen erkennt der Pegel, nach jeder Antwort hört Mia wieder zu ──
  function beginSegment() {
    const L = s.live; if (!L || L.rec || s.status !== "listening") return;
    const chunks = [];
    const rec = new MediaRecorderCtor(L.stream, L.mime ? { mimeType: L.mime } : undefined);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    L.done = new Promise((resolve) => { rec.onstop = () => resolve(new Blob(chunks, { type: rec.mimeType || L.mime || "audio/mp4" })); });
    L.rec = rec; L.heard = false; L.segStart = Date.now(); L.vad.reset();
    rec.start();
  }
  async function endSegment(force = false) {
    const L = s.live; if (!L || !L.rec) return;
    const heard = L.heard || force, rec = L.rec, done = L.done; L.rec = null;
    s.status = "transcribing";
    try { rec.stop(); } catch { /* schon gestoppt */ }
    const blob = await done;
    if (!alive || !s.live) return;
    if (!heard || blob.size < 1200) return setStatus("bereit");   // nichts Brauchbares: weiter zuhören
    await transcribeAndSend(blob, true);
  }
  function liveTick() {
    const L = s.live; if (!L || s.status !== "listening" || !L.rec || !meter) return;
    const ev = L.vad.feed(meter.level(), Date.now());
    if (ev === "start") L.heard = true;
    else if (ev === "end") onErr(endSegment());
    else if (!L.heard && Date.now() - L.segStart > 25_000) { const rec = L.rec; L.rec = null; try { rec.stop(); } catch { /* egal */ } beginSegmentLater(); }
  }
  function beginSegmentLater() { setTimeout(() => beginSegment(), 0); }   // Stille wird nach 25 s verworfen, damit die Aufnahme klein bleibt
  async function startLive() {
    unlockAudio();
    if (!mediaDevices?.getUserMedia || !MediaRecorderCtor) return setStatus("bereit", "Dieses Gerät kann hier nicht aufnehmen. Du kannst tippen.");
    let stream;
    try { stream = await mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
    catch { return setStatus("bereit", "Das Mikrofon ist nicht freigegeben. Erlaube es in den Einstellungen (Safari → Mikrofon) und versuche es noch einmal."); }
    meter = meterFactory(stream);
    if (!meter) { stream.getTracks().forEach((t) => t.stop()); return setStatus("bereit", "Freisprechen geht auf diesem Gerät nicht (keine Pegelmessung). Nutze das Mikrofon zum Antippen."); }
    s.live = { stream, mime: pickAudioMime(MediaRecorderCtor), vad: createVad(), rec: null, done: null, heard: false, segStart: 0, timer: setInterval(liveTick, 80) };
    setStatus("listening"); beginSegment();
  }
  function stopLive(msg = "") {
    const L = s.live; if (!L) return;
    s.live = null; clearInterval(L.timer);
    try { L.rec && L.rec.stop(); } catch { /* egal */ }
    L.stream.getTracks().forEach((t) => t.stop());
    if (meter) { meter.stop(); meter = null; }
    if (audio && s.status === "speaking") { audio.pause(); audio.dispatchEvent?.(new Event("ended")); }
    if (s.status === "listening") setStatus("bereit", msg); else paintStatus();
  }

  async function attachFiles(list) {
    const files = Array.from(list || []); if (!files.length) return;
    s.uploading = true; s.error = ""; paintStatus();
    for (const f of files) {
      try {
        const form = new FormData(); form.append("file", f, f.name);
        const r = await apiForm("/api/chat/attachments", form);
        const a = r.attachment || {};
        s.pending.push({ path: a.path, name: a.name || f.name, mime: a.mime || f.type || "", size: a.size || f.size || 0 });
      } catch (e) {
        if (e && e.status === 401) throw e;
        s.error = `„${f.name}“ konnte nicht hochgeladen werden: ` + (e?.detail || "Fehler");
      }
    }
    s.uploading = false; paintStatus();
  }

  async function onAction(act, target) {
    if (act === "mic") {
      if (s.status === "speaking") { if (audio) { audio.pause(); audio.dispatchEvent?.(new Event("ended")); } return setStatus("bereit"); }
      if (s.live) return s.status === "listening" ? endSegment(true) : undefined;   // Freisprechen: Tippen sendet sofort
      return s.recording ? stopRecording() : s.status === "bereit" ? startRecording() : undefined;
    }
    if (act === "live") return s.live ? stopLive() : s.status === "bereit" ? startLive() : undefined;
    if (act === "attach") return q("#chat-file")?.click();
    if (act === "unattach") { s.pending.splice(Number(target.dataset.i), 1); return paintStatus(); }
    if (act === "toggle-speak") { s.speak = !!target.checked; return; }
    if (act === "chat-new") {
      if (s.recording) { try { s.recording.rec.stop(); s.recording.stream.getTracks().forEach((t) => t.stop()); } catch { /* egal */ } s.recording = null; }
      const c = await api("/api/chat/conversations", { method: "POST", body: { title: "Reyes Office" } });
      s.convId = c.conversation.id; s.messages = []; set(STORE, s.convId); return setStatus("bereit");
    }
  }

  return {
    state: s,
    async mount(rootEl) {
      root = rootEl; alive = true;
      root.innerHTML = jarvisFrameHTML();
      el = root.querySelector("#chat-panel");
      const canvas = root.querySelector("canvas");
      try { core = canvas ? coreFactory(canvas) : null; } catch { core = null; }   // ohne Zeichenfläche geht das Gespräch trotzdem
      startTicker(); syncCore();
      el.innerHTML = chatHTML(s);
      el.addEventListener("submit", (ev) => { ev.preventDefault(); unlockAudio(); const i = q("#chat-input"); const t = i.value; i.value = ""; onErr(send(t, false)); });
      el.addEventListener("click", (ev) => { const b = ev.target.closest("[data-act]"); if (b && ["mic", "chat-new", "live", "attach", "unattach"].includes(b.dataset.act)) onErr(onAction(b.dataset.act, b)); });
      el.addEventListener("change", (ev) => {
        const b = ev.target.closest("[data-act=toggle-speak]"); if (b) onAction("toggle-speak", b);
        if (ev.target.id === "chat-file") { const list = Array.from(ev.target.files || []); ev.target.value = ""; onErr(attachFiles(list)); }
      });
      try { s.caps = await api("/api/voice/capabilities"); } catch (e) { if (e && e.status === 401) throw e; }
      await ensureConversation();
      paintStatus();
    },
    unmount() { stopLive(); alive = false; if (ticker) { clearInterval(ticker); ticker = null; } if (core) { try { core.stop(); } catch { /* egal */ } core = null; } if (meter) { meter.stop(); meter = null; } root = null; if (s.recording) { try { s.recording.stream.getTracks().forEach((t) => t.stop()); } catch { /* egal */ } s.recording = null; } if (audio) audio.pause(); el = null; },
    async reload() { if (s.convId) { const d = await api(`/api/chat/conversations/${s.convId}`); s.messages = d.messages || []; paintStatus(); } },
  };

  function onErr(p) { p.catch((e) => { if (e && e.status === 401) globalThis.dispatchEvent?.(new Event("reyes-logout")); else setStatus("bereit", "Fehler: " + (e?.detail || e?.message || "unbekannt")); }); }
}
