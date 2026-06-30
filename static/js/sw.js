const CACHE = 'shtab-static-v1';
const STATIC_ASSETS = [
  '/static/css/crm.css',
  '/static/icons/icon-192.png',
  '/offline.html',
];

// ── Установка: кэшируем статику ────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

// ── Активация: чистим старые кэши ─────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ── Fetch: статика из кэша, остальное через сеть ───────────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Только GET, только наш origin
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;

  // HTML и API — только сеть, при ошибке — offline.html для навигации
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/offline.html'))
    );
    return;
  }

  // Статические файлы: сначала кэш, потом сеть
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached => cached || fetch(event.request))
    );
    return;
  }
});

// ── Push: получаем уведомление от сервера ─────────────────────────────────
self.addEventListener('push', event => {
  let payload = { title: 'ШТАБ', body: '', link: '/' };
  try {
    if (event.data) payload = { ...payload, ...event.data.json() };
  } catch (e) {
    if (event.data) payload.body = event.data.text();
  }

  const options = {
    body: payload.body,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { link: payload.link || '/' },
    vibrate: [200, 100, 200],
    requireInteraction: false,
  };

  event.waitUntil(
    self.registration.showNotification(payload.title, options)
  );
});

// ── Клик по уведомлению: открываем нужную страницу ────────────────────────
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const link = (event.notification.data || {}).link || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      // Если вкладка уже открыта — фокусируем её
      for (const client of list) {
        if (client.url.includes(self.location.origin)) {
          client.focus();
          client.navigate(link);
          return;
        }
      }
      return clients.openWindow(link);
    })
  );
});
