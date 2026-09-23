// Reyes Office — Dienst im Hintergrund: hält die Oberfläche (nicht die Daten) für den schnellen Start bereit.
// Daten aus /api/ werden nie zwischengespeichert: Buchhaltungszahlen müssen immer frisch vom Server kommen.
const CACHE = "reyes-office-v6";
const FILES = ["./", "./index.html", "./dashboard.css", "./app.css", "./render.mjs", "./app.mjs", "./chat.mjs", "./core.mjs", "./manifest.webmanifest"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((k) => Promise.all(k.filter((x) => x !== CACHE).map((x) => caches.delete(x)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; }).catch(() => caches.match(e.request)));
});
