const CACHE_NAME = 'timecost-cache-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/static/timecost.css',
  '/static/timecost.js',
  '/static/favicon.svg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
});

self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      // Use cached response or fetch from network
      return cachedResponse || fetch(event.request);
    })
  );
});
