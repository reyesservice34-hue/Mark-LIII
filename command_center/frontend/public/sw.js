// Minimal service worker — exists only so the browser considers this an
// installable app (the pinnable icon in the address bar). It deliberately
// caches nothing: this dashboard's data changes constantly and is
// session/auth-sensitive, so every request should still go to the network
// exactly as if there were no service worker at all.
self.addEventListener("install", () => { self.skipWaiting(); });
self.addEventListener("activate", (event) => { event.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", () => { /* no-op: let the browser handle every request normally */ });

self.addEventListener("message", (event) => { if (event.data?.type === "SKIP_WAITING") self.skipWaiting(); });
