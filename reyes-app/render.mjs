// Reyes Office — Darstellung. Reine Funktionen: Daten rein, HTML-Text raus. Kein Zugriff auf Seite oder Netz,
// damit sich alles ohne Browser testen lässt (tests/test_reyes_app.mjs). Jede Angabe aus Daten wird maskiert.

export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ICONS = {
  calc: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  pin: '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0116 0z"/><circle cx="12" cy="10" r="3"/>',
  refresh: '<path d="M21 12a9 9 0 11-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  play: '<path d="M6 4l14 8-14 8z"/>',
  alert: '<path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M22 7l-10 6L2 7"/>',
  moon: '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>',
  logout: '<path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0014 0M12 17v5M8 22h8"/>',
  send: '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  volume: '<path d="M11 5L6 9H2v6h4l5 4zM15.5 8.5a5 5 0 010 7M19 5a10 10 0 010 14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  clip: '<path d="M21 11.5l-8.5 8.5a5 5 0 01-7-7l9-9a3.3 3.3 0 014.7 4.7l-9 9a1.7 1.7 0 01-2.4-2.4l8.3-8.3"/>',
  wave: '<path d="M3 12h2M7 8v8M11 4v16M15 8v8M19 10v4M21 12h0"/>',
  phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 005 5L15 13l5 2v4a2 2 0 01-2 2A16 16 0 013 6a2 2 0 012-2"/>',
  bell: '<path d="M6 8a6 6 0 0112 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 003.4 0"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2M16 7l3 3"/>',
  file: '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/>',
};
export const icon = (name, size = 16) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const WOCHENTAGE = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
const isoDay = (s) => new Date(String(s).slice(0, 10) + "T12:00:00");
export const fmtDate = (s) => (s ? String(s).slice(8, 10) + "." + String(s).slice(5, 7) + "." + String(s).slice(0, 4) : "–");
export const fmtDateLong = (s) => (s ? `${WOCHENTAGE[isoDay(s).getDay()]}, ${fmtDate(s)}` : "–");
export const fmtTime = (s) => String(s).slice(11, 16);
export const fmtEuro = (n) => (n === null || n === undefined || Number.isNaN(Number(n)) ? "–" : new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(Number(n)));

const badge = (tone, text) => `<span class="badge ${tone}">${esc(text)}</span>`;
const panel = (title, ic, body, foot = "") =>
  `<section class="panel"><div class="panel-head"><h2>${icon(ic, 16)}${esc(title)}</h2></div><div class="panel-body">${body}</div>${foot ? `<div class="panel-foot">${foot}</div>` : ""}</section>`;
const stat = (label, value, sub = "", tone = "") =>
  `<div class="panel stat"><span class="label">${esc(label)}</span><span class="value ${tone}">${value}</span>${sub ? `<span class="tiny muted">${sub}</span>` : ""}</div>`;

/** Ein Ergebnis-Objekt der Automatik (Postfach, Abendprüfung, Voranmeldung) in einen kurzen deutschen Satz. */
export function fmtResult(r) {
  if (r === null || r === undefined) return "noch nicht gelaufen";
  if (typeof r === "string") return r;
  if (r.error) return `Fehler: ${r.error}`;
  if (r.skipped) return r.skipped;
  if (r.first_run) return `Erster Lauf: ${r.vermerkt ?? 0} vorhandene Mails nur vermerkt`;
  if (r.started) return `Auftrag gestartet (${r.phase === "schluss" ? "Schlussprüfung" : "Entwurf"})`;
  if (r.status === "gestartet") return `${r.neue_belege} neue Belege an den Buchhalter übergeben`;
  if (r.status === "nichts Neues") return `Nichts Neues (${r.ungeprueft_gesamt ?? 0} ungeprüft im Bestand)`;
  if (typeof r.new === "number") return r.new ? `${r.new} neue Mail(s) mit Beleg übergeben` : "Keine neuen Belege im Postfach";
  return r.status || r.hinweis || "OK";
}

const toneForDays = (d) => (d <= 3 ? "err" : d <= 7 ? "warn" : "ok");

export function buchhaltungView(d) {
  if (!d || d.fehler) return panel("Buchhaltung nicht erreichbar", "alert", `<p class="dim">${esc(d?.fehler || "Keine Antwort vom Server.")}</p>`);
  const lx = d.lexware || {}, u = d.ustva || {}, ok = !!lx.konfiguriert, aus = !ok ? "disabled" : "";
  const un = d.ungeprueft;
  const tage = Number(u.tage_bis_frist);
  const stats = `<div class="grid cols-4">
      ${stat("Frist Voranmeldung", `${Number.isFinite(tage) ? tage : "–"}<small>Tage</small>`, esc(fmtDateLong(u.frist)), Number.isFinite(tage) ? toneForDays(tage) : "")}
      ${stat("Zu prüfende Belege", un ? String(un.anzahl) : "–", ok ? "in Lexware, ungeprüft" : "Lexware nicht verbunden")}
      ${stat("Lexware", ok ? badge("ok", "verbunden") : badge("warn", "Schlüssel fehlt"), esc(lx.firma || ""))}
      ${stat("Buchhalter-Agent", d.agent?.aktiv ? badge("ok", "aktiv") : badge("err", "aus"), "Sonnet 5 über OpenRouter")}
    </div>`;
  const step = (done, text) => `<li class="${done ? "done" : ""}"><span class="dot">${done ? icon("check", 12) : ""}</span>${esc(text)}</li>`;
  const va = panel("Umsatzsteuer-Voranmeldung", "file", `
      <div class="row wrap between"><div><span class="label">Zeitraum (monatlich)</span><div>${esc(u.zeitraum || "–")}</div></div>
      <div><span class="label">Frist</span><div>${esc(fmtDateLong(u.frist))}</div></div>
      <div><span class="label">Schlussprüfung spätestens</span><div>${esc(fmtDateLong(u.schlusstag))}</div></div></div>
      <ul class="steps checklist">
        ${step(u.entwurf_angestossen, "Entwurf (ab dem 5.): Belege prüfen, rechnen, Kennzahlen")}
        ${step(u.schluss_angestossen, "Schlussprüfung: alles noch einmal, kein Beleg des Monats vergessen")}
        ${step(false, "Abgabe: macht ein Mensch in Lexware oder ELSTER")}
      </ul>
      ${u.quartal_moeglich ? '<p class="tiny muted">Dieser Monat kann auch ein Quartalsende sein: Der Agent nimmt monatlich oder vierteljährlich aus dem Gedächtnis und sagt, wenn er raten muss.</p>' : ""}
      <div class="row wrap" style="margin-top:12px">
        <button class="btn" data-act="ustva-entwurf" ${aus}>${icon("play", 14)}Entwurf jetzt</button>
        <button class="btn primary" data-act="ustva-schluss" ${aus}>${icon("check", 14)}Schlussprüfung jetzt</button>
      </div>`, "Mia gibt nie ab und behauptet das auch nie.");
  const rows = (un?.belege || []).map((b) => `<tr><td class="num">${esc(fmtDate(b.date))}</td><td class="truncate">${esc(b.contact || "(ohne Partner)")}</td><td class="num">${esc(b.number || "–")}</td><td class="num" style="text-align:right">${esc(fmtEuro(b.total))}</td></tr>`).join("");
  const belege = panel("Belege zu prüfen", "mail",
    !ok ? '<div class="empty"><strong>Lexware nicht verbunden</strong><span>Sobald Lexware verbunden ist, stehen die ungeprüften Belege hier.</span></div>'
      : un && un.belege.length ? `<div style="overflow:auto"><table class="table"><thead><tr><th>Datum</th><th>Partner</th><th>Nr.</th><th style="text-align:right">Betrag</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : '<div class="empty"><strong>Alles abgearbeitet</strong><span>Keine ungeprüften Belege in Lexware.</span></div>',
    un && un.anzahl > un.belege.length ? `${un.anzahl - un.belege.length} weitere nicht angezeigt` : "");
  const pf = d.postfach || {};
  const auto = panel("Automatik", "moon", `
      <div class="stack">
        <div class="row between"><div><span class="label">Postfach (alle 15 Min.)</span><div class="small">${esc(fmtResult(pf.ergebnis))}</div><div class="tiny muted">letzter Lauf: ${esc(pf.letzter_lauf || "–")}</div></div>
          <button class="btn sm" data-act="postfach" ${aus}>${icon("refresh", 13)}Jetzt</button></div>
        <div class="row between"><div><span class="label">Abendprüfung (nach 19 Uhr)</span><div class="small">${esc(fmtResult(d.abend))}</div></div>
          <button class="btn sm" data-act="abend" ${aus}>${icon("refresh", 13)}Jetzt</button></div>
        <div class="tiny muted">Aufträge an den Agenten seit dem Start: ${esc(pf.auftraege_gesamt ?? 0)}</div>
      </div>`);
  const regeln = panel("Regeln des Agenten", "shield", `
      <ul class="steps">
        <li>Karten <span class="num">6952 · 6369 · 6359</span> → <strong>offen</strong>, die Bank ordnet später zu.</li>
        <li>Bar oder andere Karte → <strong>per Kasse bezahlt</strong> (Arbeitsliste, die API kann das nicht setzen).</li>
        <li>Nichts löschen, nichts erfinden, keine Abgabe ans Finanzamt.</li>
      </ul>`);
  const fehler = lx.fehler ? panel("Lexware meldet", "alert", `<p class="dim">${esc(lx.fehler)}</p>`) : "";
  return `${fehler}${stats}<div class="grid cols-2">${va}${belege}</div><div class="grid cols-2">${auto}${regeln}</div>`;
}

export function kalenderView(d) {
  if (!d || d.fehler) return panel("Kalender nicht erreichbar", "alert", `<p class="dim">${esc(d?.fehler || "Keine Antwort vom Server.")}</p>`);
  if (d.available === false) return panel("Kalender nicht verfügbar", "alert", `<p class="dim">${esc(d.detail || "")}</p>`);
  const days = new Map();
  for (const e of d.events || []) {
    const k = String(e.start).slice(0, 10);
    if (!days.has(k)) days.set(k, []);
    days.get(k).push(e);
  }
  if (!days.size) return panel("Termine", "calendar", '<div class="empty"><strong>Keine Termine</strong><span>In den nächsten 14 Tagen steht nichts an.</span></div>');
  const cat = { tour: badge("info", "Baustelle"), ich: badge("ok", "Ich"), privat: badge("muted", "Privat") };
  const html = [...days.entries()].sort().map(([day, ev]) => `<div class="day"><div class="label">${esc(fmtDateLong(day))}</div>${
    ev.sort((a, b) => String(a.start).localeCompare(String(b.start))).map((e) => {
      const allDay = String(e.start).endsWith("T00:00:00") && /T23:59|T00:00:00$/.test(String(e.end));
      return `<div class="event row"><span class="num when">${allDay ? "ganztägig" : esc(fmtTime(e.start)) + "–" + esc(fmtTime(e.end))}</span><div class="grow"><div>${esc(e.title)}</div>${e.location ? `<div class="tiny muted">${esc(e.location)}</div>` : ""}</div>${cat[e.category] || ""}</div>`;
    }).join("")}</div>`).join("");
  return panel("Termine der nächsten 14 Tage", "calendar", `<div class="stack">${html}</div>`, `Quelle: ${esc(d.backend === "n8n" ? "Google Kalender (Gmail)" : d.backend || "lokal")}`);
}

export function standortView(d) {
  if (!d || d.fehler) return panel("Standort nicht erreichbar", "alert", `<p class="dim">${esc(d?.fehler || "Keine Antwort vom Server.")}</p>`);
  const r = d.result && typeof d.result === "object" ? d.result : {};
  const seit = d.letzte_position_vor_minuten;
  const frisch = typeof seit === "number" && seit <= 20;
  return `<div class="grid cols-3">
      ${stat("Empfänger", d.eingerichtet ? badge("ok", "eingerichtet") : badge("warn", "nicht eingerichtet"))}
      ${stat("Letzte Position", seit === null || seit === undefined ? "–" : `${seit}<small>Min. alt</small>`, frisch ? "frisch genug" : "zu alt oder keine")}
      ${stat("Letzte Prüfung", esc(r.status || "–"), esc(d.last_check || ""))}
    </div>
    ${panel("So funktioniert die Losfahr-Warnung", "pin", `<ul class="steps">
      <li>Das Handy meldet die Position (App <strong>OwnTracks</strong>, HTTP-Modus).</li>
      <li>Alle 5 Minuten prüft Mia den nächsten Termin mit Ort, der dich betrifft, und rechnet die Fahrzeit.</li>
      <li>Warnung aufs Handy: erst „bald losfahren“, dann „jetzt losfahren“, dann „du schaffst es nicht mehr“.</li>
      <li>Ohne frische Position, Ort oder Fahrzeit meldet er nichts und schätzt nichts.</li></ul>
      <p class="tiny muted">Anrufen kann Mia nicht: Die Warnung kommt als Push und in der Live-Konsole.</p>`)}`;
}

// Buchhaltung: buchhaltungView() bleibt erhalten, ist aber bewusst nicht in der Navigation (Wunsch: Das erledigt der Agent, nicht die App).
// Wieder einschalten: { id: "buchhaltung", title: "Buchhaltung", eyebrow: "Modul", icon: "calc", url: "/api/buchhaltung/uebersicht", view: buchhaltungView }
export const MODULES = [
  { id: "jarvis", title: "Mia", eyebrow: "Sprache & Chat", icon: "mic", custom: true },
  { id: "kalender", title: "Kalender", eyebrow: "Modul", icon: "calendar", url: "/api/calendar?days=14", view: kalenderView },
  { id: "standort", title: "Standort", eyebrow: "Modul", icon: "pin", url: "/api/standort", view: standortView },
  { id: "meldungen", title: "Meldungen", eyebrow: "Modul", icon: "bell", urls: { push: "/api/meldungen/push", list: "/api/notifications?limit=15", freig: "/api/approvals?status=pending" }, view: meldungenView },
];

export function navHTML(active) {
  return MODULES.map((m) => `<a class="nav-item ${m.id === active ? "active" : ""}" href="#/${m.id}">${icon(m.icon, 17)}<span class="truncate">${esc(m.title)}</span>${m.id === "meldungen" ? '<b class="nbadge" hidden></b>' : ""}</a>`).join("");
}
export function mobileNavHTML(active) {
  return MODULES.map((m) => `<a class="${m.id === active ? "active" : ""}" href="#/${m.id}">${icon(m.icon, 20)}<span>${esc(m.title)}</span>${m.id === "meldungen" ? '<b class="nbadge" hidden></b>' : ""}</a>`).join("");
}

export function shellHTML(active, user, content, updated) {
  const m = MODULES.find((x) => x.id === active) || MODULES[0];
  return `<div class="shell" data-module="${esc(m.id)}">
    <aside class="sidebar" aria-label="Navigation">
      <div class="brand"><span class="core" aria-hidden="true"><span class="ring"></span><span class="nucleus"></span></span>
        <span class="brand-text"><strong>REYES</strong><span class="label">Office</span></span></div>
      <nav class="nav">${navHTML(m.id)}</nav>
      <div class="sidebar-foot"><button class="nav-item" data-act="logout">${icon("logout", 17)}<span class="truncate">${esc(user || "Angemeldet")} · abmelden</span></button></div>
    </aside>
    <header class="topbar"><span class="chip hero">${esc(m.title)}</span><span class="sep"></span>
      <span class="chip"><span class="muted">Stand</span> <span class="val" id="stand">${esc(updated || "")}</span></span>
      <div class="status-group right"><button class="btn sm ghost" data-act="reload">${icon("refresh", 14)}Aktualisieren</button></div></header>
    <main class="main"><div class="page" id="page">
      <div class="page-head"><div><div class="eyebrow">${esc(m.eyebrow)}</div><h1>${esc(m.title)}</h1></div></div>
      ${content}
    </div></main>
    <nav class="mobile-nav mobile-only" aria-label="Module">${mobileNavHTML(m.id)}</nav>
  </div>`;
}

export function loginHTML(message = "") {
  return `<div class="login"><form class="panel login-card" id="login">
    <div class="brand" style="border:0"><span class="core" aria-hidden="true"><span class="ring"></span><span class="nucleus"></span></span>
      <span class="brand-text"><strong>REYES</strong><span class="label">Office</span></span></div>
    <p class="dim small">Melde dich mit deinem Konto an.</p>
    <div class="field"><label for="u">Benutzername</label><input class="input" id="u" name="u" autocomplete="username" required></div>
    <div class="field"><label for="p">Passwort</label><input class="input" id="p" name="p" type="password" autocomplete="current-password" required></div>
    ${message ? `<div class="badge err" role="alert">${esc(message)}</div>` : ""}
    <button class="btn primary" type="submit">Anmelden</button></form></div>`;
}

// ── Mia: Gespräch (Sprache und Text) ──────────────────────────────────────────────────────────────────────────
export const STATUS_TEXT = { bereit: "Bereit", listening: "Ich höre zu … Tippe erneut zum Senden", transcribing: "Ich verstehe dich …", thinking: "Mia denkt nach …", speaking: "Mia spricht … (Mikrofon tippen zum Stoppen)" };
export const statusText = (s) => (s.live && s.status === "listening" ? "Ich höre zu … sprich einfach, ich merke, wann du fertig bist" : s.live && s.status === "speaking" ? "Mia spricht … Mikrofon tippen zum Unterbrechen" : STATUS_TEXT[s.status] || STATUS_TEXT.bereit);

export const EMPTY_CHAT = '<div class="empty"><strong>Sag etwas zu Mia</strong><span>Tippe das Mikrofon und sprich, oder schreib unten. Sie kennt dein Gedächtnis, den Kalender und den Buchhalter.</span></div>';

export function bubbleHTML(m) {
  const mine = m.role === "user";
  const text = String(m.content || "");
  const state = m.status === "streaming" ? '<span class="cursor" aria-hidden="true"></span>' : m.status === "error" ? '<div class="badge err">Fehler beim Antworten</div>' : m.status === "stopped" ? '<div class="badge warn">abgebrochen</div>' : "";
  const empty = !text && m.status === "streaming" ? '<span class="muted">…</span>' : "";
  const files = (m.attachments || m.meta?.attachments || []).map((a) => `<span class="chip tiny">${icon("clip", 12)}${esc(a.name || a.path)}</span>`).join("");
  return `<div class="bubble ${mine ? "mine" : "jarvis"}"><div class="who label">${mine ? "Du" : "Mia"}</div><div class="text">${esc(text)}${empty}${state}</div>${files ? `<div class="row wrap" style="gap:6px;margin-top:6px">${files}</div>` : ""}</div>`;
}

export function chatHTML(s) {
  const caps = s.caps || {};
  const stt = caps.speech_to_text?.available !== false, tts = caps.text_to_speech?.available !== false;
  const rec = s.status === "listening", busy = ["transcribing", "thinking"].includes(s.status);
  const notes = [];
  if (s.caps && !stt) notes.push("Spracheingabe ist auf dem Server nicht verfügbar: " + (caps.speech_to_text?.detail || "unbekannt") + " Du kannst tippen.");
  if (s.caps && !tts) notes.push("Vorlesen ist auf dem Server nicht verfügbar: " + (caps.text_to_speech?.detail || "unbekannt"));
  const msgs = (s.messages || []).length ? s.messages.map(bubbleHTML).join("") : EMPTY_CHAT;
  return `<section class="panel chat">
    <div class="msgs" id="msgs" aria-live="polite">${msgs}</div>
    ${s.error ? `<div class="badge err chat-error" role="alert">${esc(s.error)}</div>` : ""}
    ${notes.map((n) => `<div class="tiny muted chat-note">${esc(n)}</div>`).join("")}
    <div class="chat-status row between"><span class="chip ${rec ? "hero" : ""}" id="chat-status">${esc(statusText(s))}</span>
      <span class="row"><label class="row tiny muted" style="gap:6px"><input type="checkbox" data-act="toggle-speak" ${s.speak ? "checked" : ""} ${tts ? "" : "disabled"}> ${icon("volume", 14)} Vorlesen</label>
      <button class="btn sm ghost" data-act="chat-new" title="Neues Gespräch">${icon("plus", 14)}Neu</button></span></div>
    ${(s.pending || []).length ? `<div class="row wrap chat-pending" style="gap:6px;margin-top:8px">${s.pending.map((a, i) => `<span class="chip">${icon("clip", 12)}${esc(a.name)}<button type="button" class="btn sm ghost" data-act="unattach" data-i="${i}" aria-label="Datei entfernen" style="padding:0 4px">×</button></span>`).join("")}</div>` : ""}
    ${s.uploading ? '<div class="tiny muted chat-note">Datei wird hochgeladen …</div>' : ""}
    <form class="chat-form row" id="chat-form" autocomplete="off">
      <input type="file" id="chat-file" multiple hidden>
      <button type="button" class="btn icon ghost" data-act="attach" aria-label="Datei anhängen" ${busy || s.uploading ? "disabled" : ""}>${icon("clip", 18)}</button>
      <button type="button" class="btn icon ${s.live ? "primary" : "ghost"}" data-act="live" aria-pressed="${s.live ? "true" : "false"}" aria-label="${s.live ? "Freisprechen beenden" : "Freisprechen starten"}" title="Freisprechen" ${!stt ? "disabled" : ""}>${icon("wave", 18)}</button>
      <button type="button" class="mic ${rec ? "rec" : ""} ${busy ? "busy" : ""}" data-act="mic" aria-label="${rec ? "Aufnahme beenden und senden" : "Sprechen"}" ${busy || !stt || (s.live && !rec && s.status !== "speaking" && s.status !== "listening") ? "disabled" : ""}>${icon(rec || s.status === "speaking" ? "stop" : "mic", 26)}</button>
      <input class="input grow" id="chat-input" name="t" placeholder="Schreib Mia …" enterkeyhint="send" ${busy ? "disabled" : ""}>
      <button class="btn primary icon" type="submit" aria-label="Senden" ${busy ? "disabled" : ""}>${icon("send", 15)}</button>
    </form></section>`;
}

export const HUD_STATE = { bereit: "Bereit", listening: "Hört zu", transcribing: "Versteht", thinking: "Denkt", speaking: "Spricht" };

/** Der feste Rahmen des Jarvis-Moduls: links der Kern (Zeichenfläche), rechts das Gespräch. Wird nur einmal gebaut, damit die Zeichenfläche nicht neu entsteht. */
export function jarvisFrameHTML() {
  return `<div class="hero-core jarvis-hero">
    <div class="hero-stage"><canvas class="brain-core" aria-label="Mia Kern" role="img"></canvas>
      <div class="hud tl"><b>Kern</b><span class="on" id="hud-state">${HUD_STATE.bereit}</span></div>
      <div class="hud tr"><b>Gehirn</b><span>Sonnet 5</span></div>
      <div class="hero-caption" id="core-caption">${esc(STATUS_TEXT.bereit)}</div></div>
    <div class="chat-side" id="chat-panel"></div></div>`;
}

// ── Meldungen aufs Handy ──
const fmtWhen = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); };
const SEV = { critical: "err", error: "err", warning: "warn", success: "ok", info: "info" };

export function meldungenView(d) {
  if (!d || d.fehler) return panel("Meldungen nicht erreichbar", "alert", `<p class="dim">${esc(d?.fehler || "Keine Antwort vom Server.")}</p>`);
  const p = d.push || {}, list = (d.list && d.list.notifications) || [];
  const setup = !p.konfiguriert
    ? panel("Meldungen aufs Handy", "bell", '<p class="dim">Der Weg aufs Handy ist noch nicht eingerichtet. Bis dahin stehen Meldungen nur hier in der Liste.</p>')
    : panel("Meldungen aufs Handy", "bell", `
      <p class="dim small">Mia schickt dir ${esc(p.regel || "wichtige Meldungen")} als Nachricht aufs Handy, auch wenn die App zu ist. Das läuft über die kostenlose App <strong>ntfy</strong>.</p>
      <ol class="steps">
        <li>Installiere <strong>ntfy</strong> aus dem App Store.</li>
        <li>Tippe hier, dann öffnet sich ntfy und abonniert Mia:<div style="margin-top:8px"><a class="btn primary" href="${esc(p.abo_link)}">${icon("bell", 14)}In ntfy abonnieren</a></div></li>
        <li>Erlaube in ntfy und in den iPhone-Einstellungen die <strong>Mitteilungen</strong>.</li>
        <li>Teste es: <div style="margin-top:8px"><button class="btn" data-act="push-test">${icon("send", 14)}Testmeldung senden</button></div></li>
      </ol>
      <p class="tiny muted">Klappt der Knopf nicht: in ntfy auf „+“ tippen und dieses Thema eintragen (es ist wie ein Passwort, gib es nicht weiter):</p>
      <pre class="code">${esc(p.thema)}</pre>`);
  const pend = (d.freig && d.freig.approvals) || [];
  const freig = pend.length ? panel(`Wartet auf dich (${pend.length})`, "alert", `<div class="stack">${pend.map((a) => `<div class="event"><div class="grow"><div>${esc(a.action)}</div><div class="tiny muted">${esc(String(a.target || "").slice(0, 200))}</div></div>
      <div class="row" style="gap:6px;margin-top:8px"><button class="btn primary sm" data-act="freigabe-ja" data-id="${esc(a.id)}">${icon("check", 14)}Freigeben</button><button class="btn sm" data-act="freigabe-nein" data-id="${esc(a.id)}">Ablehnen</button></div></div>`).join("")}</div>`) : "";
  const an = p.anruf || {};
  const anruf = panel("Anruf bei Dringendem", "phone", an.konfiguriert
    ? `<p class="dim small">Bei kritischen Fällen ruft Mia dich an und liest die Meldung vor (${esc(an.regel || "")}). Nummer: ${esc(an.nummer || "")}. Höchstens ein Anruf alle paar Minuten.</p><button class="btn" data-act="anruf-test">${icon("phone", 14)}Testanruf</button>`
    : `<p class="dim small">Damit Mia dich bei Notfällen wirklich anklingelt, braucht sie einen Telefonanbieter (Twilio). Das richtet man einmal auf dem Server ein. Bis dahin meldet sie sich per Nachricht.</p><p class="tiny muted">Es fehlt: ${esc((an.fehlt || []).join(", ") || "Einrichtung")}</p>`);
  const rows = list.map((n) => `<div class="event row"><span class="when tiny">${esc(fmtWhen(n.created_at))}</span><div class="grow"><div>${esc(n.title)}</div>${n.body ? `<div class="tiny muted">${esc(String(n.body).slice(0, 160))}</div>` : ""}</div>${badge(SEV[n.severity] || "info", n.severity || "info")}</div>`).join("");
  const letzte = panel("Letzte Meldungen", "mail", list.length ? `<div class="stack">${rows}</div>` : '<div class="empty"><strong>Keine Meldungen</strong><span>Alles ruhig.</span></div>');
  return `${freig}${setup}${anruf}${letzte}`;
}
