const CACHE_NAME = 'shtab-static-v1';

// Только статические ассеты — никогда не кэшируем HTML и данные
const PRECACHE_URLS = [
  '/offline.html',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
  '/static/favicon.ico',
];

// Расширения файлов, которые кэшируем cache-first
const STATIC_EXTS = /\.(css|woff2?|ttf|eot|png|jpg|jpeg|svg|ico|webmanifest)$/i;

// Хосты CDN, которые кэшируем cache-first
const CDN_HOSTS = [
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'cdn.jsdelivr.net',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Никогда не перехватываем POST и не-GET
  if (request.method !== 'GET') return;

  // CDN-ресурсы — cache-first
  if (CDN_HOSTS.some(h => url.hostname.includes(h))) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Статические файлы нашего домена — cache-first
  if (url.origin === self.location.origin && STATIC_EXTS.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Всё остальное (HTML, API, авторизованные ответы) — network-only
  // с фолбэком на offline.html для навигационных запросов
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/offline.html'))
    );
    return;
  }

  // Прочие запросы — network-only без перехвата
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(CACHE_NAME);
    cache.put(request, response.clone());
  }
  return response;
}
