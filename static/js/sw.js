const CACHE_NAME = 'shtab-v1';

// Статика, которую прекэшируем при установке
const PRECACHE_URLS = [
  '/offline.html',
  '/static/css/crm.css',
  '/static/favicon.ico',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
];

// Пути Flask, которые НИКОГДА не кэшируем (авторизация, данные, файлы)
const NEVER_CACHE_PATHS = [
  '/login', '/logout', '/register',
  '/api/', '/export/', '/supply/',
  '/defects/', '/objects/', '/packages/',
  '/admin/', '/profile', '/dashboard',
  '/notifications',
];

// Расширения файлов — кэшируем cache-first
const STATIC_EXT_RE = /\.(css|woff2?|ttf|eot|otf|png|jpg|jpeg|gif|svg|ico|webp|webmanifest)$/i;

// CDN-хосты — кэшируем cache-first
const CDN_HOSTS = [
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'cdn.jsdelivr.net',
];

// ─── Install: прекэш статики ───────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())   // новый SW сразу берёт управление
  );
});

// ─── Activate: удаляем старые версии кэша ─────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())  // берём контроль над всеми вкладками
  );
});

// ─── Fetch ─────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // 1. Не-GET (POST, PUT, DELETE, ...) — всегда в сеть, без перехвата
  if (request.method !== 'GET') return;

  // 2. Пути, которые НИКОГДА не кэшируем — network-only
  if (NEVER_CACHE_PATHS.some(p => url.pathname.startsWith(p))) return;

  // 3. CDN-ресурсы — cache-first (шрифты, Bootstrap CSS/JS)
  if (CDN_HOSTS.some(h => url.hostname.includes(h))) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // 4. Статические файлы нашего домена — cache-first
  if (url.origin === self.location.origin && STATIC_EXT_RE.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // 5. Навигационные запросы (HTML-страницы) — network-only + offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/offline.html'))
    );
    return;
  }

  // 6. Всё остальное (XHR, fetch с данными) — network-only, без перехвата
});

// ─── Helpers ───────────────────────────────────────────────────────────────
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok && response.status < 400) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch (e) {
    // статика недоступна — просто отдаём ошибку, не фолбэчим на offline.html
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}
