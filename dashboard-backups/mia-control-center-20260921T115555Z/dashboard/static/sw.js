// JARVIS Dashboard — Service Worker
// Purpose: makes the dashboard installable on iOS/Android (a service worker is
// required for a reliable "Add to Home Screen" install) and caches the static
// app shell so the icon still opens to *something* without a network.
//
// Not implemented here: Web Push. A push event handler would need a server-side
// VAPID subscription endpoint that does not exist yet — see readme note in the
// PR description. Do not add a 'push' listener until that backend exists.

const CACHE_NAME = 'jarvis-shell-v1';
const SHELL_ASSETS = [
  '/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Live app pages, the API and the WebSocket must always hit the network —
// caching an auth'd dashboard response or a socket upgrade would break login.
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/ws') return;
  if (!SHELL_ASSETS.includes(url.pathname)) return;

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
