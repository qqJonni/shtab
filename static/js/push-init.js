(function () {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

  // ── Регистрируем Service Worker ───────────────────────────────────────────
  navigator.serviceWorker.register('/sw.js', { scope: '/' })
    .then(function (reg) {
      // Если разрешение уже дано — тихо подписываемся. Баннер не показываем.
      if (Notification.permission === 'granted') {
        _ensureSubscription(reg);
      }
    })
    .catch(function (err) {
      console.warn('[SW] registration failed:', err);
    });

  // ── Получаем/обновляем push-подписку и шлём на сервер ───────────────────
  function _ensureSubscription(reg) {
    fetch('/api/push/vapid-public-key')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var appServerKey = _urlBase64ToUint8Array(data.publicKey);
        return reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: appServerKey,
        });
      })
      .then(function (sub) {
        return fetch('/api/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(sub.toJSON()),
        });
      })
      .catch(function (err) {
        console.warn('[Push] subscription failed:', err);
      });
  }

  function _urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - base64String.length % 4) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = atob(base64);
    var arr = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return arr;
  }
})();
