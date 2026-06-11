const CACHE_NAME = 'timecost-cache-v5';
// Precache only genuinely static, versioned assets. NOTE: do not list '/' here —
// the old fetch handler matched it against every URL (every URL contains '/'),
// which served the entire site stale-from-cache and hid fresh deploys.
const ASSETS_TO_CACHE = [
  '/static/favicon.svg',
  'https://unpkg.com/lucide@latest'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  // Page navigations (HTML) are ALWAYS network-first so deploys show up
  // immediately; fall back to cache only when offline.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
    return;
  }

  // Only the explicitly precached static assets use stale-while-revalidate,
  // matched by exact URL (not substring) so '/' can't swallow everything.
  const isPrecached = ASSETS_TO_CACHE.some(asset => event.request.url === asset
    || event.request.url === new URL(asset, self.location.origin).href);

  if (isPrecached) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        const fetchPromise = fetch(event.request).then((networkResponse) => {
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, networkResponse.clone());
          });
          return networkResponse;
        });
        return cachedResponse || fetchPromise;
      })
    );
  } else {
    // Everything else (CSS/JS with ?v= params, blueprint static, etc.):
    // network-first, cache only as an offline fallback.
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  }
});

// --- Push Notifications ---
self.addEventListener('push', (event) => {
  if (!event.data) return;

  let data;
  try {
    data = event.data.json();
  } catch (e) {
    data = { title: 'Dinaro', body: event.data.text() };
  }

  const options = {
    body: data.body || '',
    icon: data.icon || '/static/favicon.svg',
    badge: '/static/favicon.svg',
    tag: data.tag || 'dinaro-notification',
    data: { url: data.url || '/dinaro' },
    vibrate: [200, 100, 200],
  };

  event.waitUntil(self.registration.showNotification(data.title || 'Dinaro', options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/dinaro';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.focus();
          client.navigate(url);
          return;
        }
      }
      return clients.openWindow(url);
    })
  );
});
