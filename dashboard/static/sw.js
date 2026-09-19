// JARVIS Remote — service worker.
// Only job: receive Web Push messages and turn a "call" payload into a loud,
// hard-to-miss notification, then hand it to the page (call.html-style UI
// lives inline in app.html) when the person taps it.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let payload = { type: 'notify', title: 'JARVIS', body: '' };
  try { payload = event.data.json(); } catch (_) {}

  const isCall = payload.type === 'call';

  event.waitUntil((async () => {
    // If the app is already open, hand it the payload directly so it can show
    // the in-page call overlay instantly — no need to wait on a tap.
    const clientsList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of clientsList) {
      c.postMessage({ source: 'jarvis-push', payload });
    }

    await self.registration.showNotification(payload.title || 'JARVIS', {
      body: payload.body || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      tag: isCall ? 'jarvis-call' : 'jarvis-notify',
      renotify: true,
      requireInteraction: !!isCall,
      vibrate: isCall ? [400, 200, 400, 200, 400] : [200],
      data: payload,
      actions: isCall
        ? [{ action: 'answer', title: '📞 Annehmen' }, { action: 'dismiss', title: 'Ablehnen' }]
        : [],
    });
  })());
});

self.addEventListener('notificationclick', (event) => {
  const payload = event.notification.data || {};
  event.notification.close();
  if (event.action === 'dismiss') return;

  event.waitUntil((async () => {
    const clientsList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of clientsList) {
      c.postMessage({ source: 'jarvis-push', payload });
      if ('focus' in c) return c.focus();
    }
    const url = '/?call=' + encodeURIComponent(JSON.stringify(payload));
    return self.clients.openWindow(url);
  })());
});
