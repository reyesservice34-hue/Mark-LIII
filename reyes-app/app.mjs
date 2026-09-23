// Reyes Office — Anbindung: Anmeldung, Daten holen, Knöpfe, Aktualisieren. Die Darstellung steckt in render.mjs, das Gespräch mit Mia in chat.mjs.
import { MODULES, shellHTML, loginHTML, fmtResult } from "./render.mjs";
import { createChat } from "./chat.mjs";

const root = document.getElementById("root");
const state = { user: "", active: "jarvis", busy: false, timer: null };

const csrf = () => decodeURIComponent((document.cookie.split("; ").find((c) => c.startsWith("jcc_csrf=")) || "").slice(9));

async function request(path, { method = "GET", body, form } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["x-csrf-token"] = csrf();
  let r;
  try {
    r = await fetch(path, { method, headers, credentials: "same-origin", body: form ?? (body === undefined ? undefined : JSON.stringify(body)) });
  } catch {
    throw { status: 0, detail: "Keine Verbindung zum Server." };
  }
  if (!r.ok) {
    let data = null;
    try { data = await r.json(); } catch { /* leer */ }
    throw { status: r.status, detail: (data && (data.detail || data.message)) || `HTTP ${r.status}` };
  }
  return r;
}
async function api(path, opts) {
  const r = await request(path, opts);
  try { return await r.json(); } catch { return null; }
}
const apiForm = (path, form) => api(path, { method: "POST", form });
const apiBlob = async (path, opts) => (await request(path, opts)).blob();

const chat = createChat({ api, apiForm, apiBlob });

function toast(text, tone = "") {
  let box = document.getElementById("toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  const t = document.createElement("div");
  t.className = `toast ${tone}`;
  t.textContent = text;
  box.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}

const routeId = () => { const id = location.hash.replace(/^#\//, ""); return MODULES.some((m) => m.id === id) ? id : "jarvis"; };
const now = () => new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

async function show() {
  const previous = state.active;
  state.active = routeId();
  const mod = MODULES.find((m) => m.id === state.active);
  if (mod.custom) {                                      // Mia: eigene Oberfläche, wird nicht neu gezeichnet, solange man spricht
    if (previous === mod.id && document.getElementById("chat-root")?.children.length) { try { await chat.reload(); } catch (e) { if (e.status === 401) return login(); } return; }
    chat.unmount();
    root.innerHTML = shellHTML(state.active, state.user, '<div id="chat-root"></div>', now());
    try { await chat.mount(document.getElementById("chat-root")); } catch (e) { if (e.status === 401) return login(); toast(e.detail || "Mia ist gerade nicht erreichbar.", "err"); }
    return;
  }
  chat.unmount();
  let content;
  try {
    const data = mod.urls ? Object.fromEntries(await Promise.all(Object.entries(mod.urls).map(async ([k, u]) => [k, await api(u)]))) : await api(mod.url);
    content = mod.view(data);
  } catch (e) {
    if (e.status === 401) return login();
    content = mod.view({ fehler: e.detail || "Unbekannter Fehler" });
  }
  const scroll = document.querySelector(".main")?.scrollTop || 0;
  root.innerHTML = shellHTML(state.active, state.user, content, now());
  const main = document.querySelector(".main");
  if (main) main.scrollTop = scroll;
  refreshBadge();
}

function login(message = "") {
  clearInterval(state.timer);
  chat.unmount();
  state.active = "";
  root.innerHTML = loginHTML(message);
  document.getElementById("login").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      await api("/api/auth/login", { method: "POST", body: { username: f.get("u"), password: f.get("p") } });
      await boot();
    } catch (e) {
      login(e.status === 401 ? "Benutzername oder Passwort stimmt nicht." : e.detail);
    }
  });
}
window.addEventListener("reyes-logout", () => login("Bitte melde dich neu an."));

const ACTIONS = {
  "ustva-entwurf": () => api("/api/buchhaltung/ustva/start", { method: "POST", body: { phase: "entwurf" } }),
  "ustva-schluss": () => api("/api/buchhaltung/ustva/start", { method: "POST", body: { phase: "schluss" } }),
  postfach: () => api("/api/buchhaltung/run", { method: "POST" }),
  abend: () => api("/api/buchhaltung/abend/run", { method: "POST" }),
  "push-test": () => api("/api/meldungen/test", { method: "POST" }),
  "anruf-test": () => api("/api/meldungen/anruf-test", { method: "POST" }),
  "freigabe-ja": async (el) => { await api(`/api/approvals/${encodeURIComponent(el.dataset.id)}/approve`, { method: "POST", body: { note: "aus der App" } }); return { hinweis: "Freigegeben." }; },
  "freigabe-nein": async (el) => { await api(`/api/approvals/${encodeURIComponent(el.dataset.id)}/reject`, { method: "POST", body: { note: "aus der App" } }); return { hinweis: "Abgelehnt." }; },
};

/** Zahl am Menüpunkt „Meldungen“: ungelesene Meldungen plus offene Freigaben. */
async function refreshBadge() {
  let n = 0;
  try { const [a, b] = await Promise.all([api("/api/notifications?unread=true&limit=1"), api("/api/approvals?status=pending&limit=1")]); n = Number(a?.unread || 0) + Number(b?.pending || 0); } catch { return; }
  document.querySelectorAll(".nbadge").forEach((el) => { el.textContent = n > 99 ? "99+" : String(n); el.hidden = n === 0; });
  try { navigator.setAppBadge && (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()); } catch { /* nicht überall verfügbar */ }
}

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-act]");
  if (!el || el.disabled) return;
  const act = el.dataset.act;
  if (act === "reload") return show();
  if (act === "logout") { try { await api("/api/auth/logout", { method: "POST" }); } catch { /* egal */ } return login(); }
  if (!ACTIONS[act] || state.busy) return;
  state.busy = true; el.disabled = true;
  try {
    const r = await ACTIONS[act](el);
    toast(r && r.hinweis ? r.hinweis : fmtResult(r), r && (r.error || r.gesendet === false) ? "err" : r && r.skipped ? "warn" : "ok");
  } catch (e) {
    toast(e.detail || "Fehlgeschlagen", "err");
  } finally {
    state.busy = false;
    await show();
  }
});

window.addEventListener("hashchange", show);

async function boot() {
  try {
    const me = await api("/api/auth/me");
    state.user = me?.name || me?.user?.name || me?.username || me?.user?.username || "";
  } catch (e) {
    if (e.status === 401) return login();
  }
  state.active = "";
  await show();
  clearInterval(state.timer); clearInterval(state.badgeTimer);
  state.badgeTimer = setInterval(() => { if (!document.hidden) refreshBadge(); }, 60000);
  state.timer = setInterval(() => { if (!document.hidden && !state.busy && state.active && !MODULES.find((m) => m.id === state.active)?.custom) show(); }, 60000);
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("./sw.js").catch(() => {});
boot();
