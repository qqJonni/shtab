(function () {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

  var STORAGE_KEY = 'shtab-push-dismissed';

  // ── 1. Регистрируем Service Worker ────────────────────────────────────────
  navigator.serviceWorker.register('/sw.js', { scope: '/' })
    .then(function (reg) {
      // ── 2. Проверяем статус разрешения ────────────────────────────────────
      if (Notification.permission === 'granted') {
        _ensureSubscription(reg);
      } else if (Notification.permission === 'default') {
        if (!localStorage.getItem(STORAGE_KEY)) {
          _showPermissionBanner(reg);
        }
      }
      // 'denied' — ничего не делаем
    })
    .catch(function (err) {
      console.warn('[SW] registration failed:', err);
    });

  // ── Баннер с запросом разрешения ─────────────────────────────────────────
  function _showPermissionBanner(reg) {
    // Показываем баннер только после первого взаимодействия пользователя
    var shown = false;
    function show() {
      if (shown) return;
      shown = true;
      document.removeEventListener('click', show);
      document.removeEventListener('touchend', show);

      // Помечаем сразу — чтобы не показывать снова при следующем клике/загрузке
      localStorage.setItem(STORAGE_KEY, '1');

      var bannerId = 'push-permission-banner';
      if (document.getElementById(bannerId)) return;

      var el = document.createElement('div');
      el.id = bannerId;
      Object.assign(el.style, {
        position: 'fixed', bottom: '80px', left: '50%',
        transform: 'translateX(-50%)',
        zIndex: '9998', width: 'calc(100% - 32px)', maxWidth: '480px',
        background: '#1E293B', color: '#fff', borderRadius: '12px',
        boxShadow: '0 4px 20px rgba(0,0,0,0.25)', padding: '14px 16px',
        display: 'flex', alignItems: 'center', gap: '12px',
        fontSize: '14px', lineHeight: '1.4',
        fontFamily: 'Inter, -apple-system, sans-serif',
      });
      el.innerHTML =
        '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#3B82F6" ' +
          'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0">' +
          '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>' +
          '<path d="M13.73 21a2 2 0 0 1-3.46 0"/>' +
        '</svg>' +
        '<div style="flex:1">' +
          '<div style="font-weight:600;margin-bottom:2px">Включить уведомления</div>' +
          '<div style="color:#94A3B8;font-size:12px">Получайте уведомления о замечаниях и заявках</div>' +
        '</div>' +
        '<button id="push-allow-btn" style="background:#3B82F6;color:#fff;border:none;' +
          'border-radius:8px;padding:8px 14px;font-size:13px;font-weight:500;cursor:pointer;' +
          'white-space:nowrap;font-family:inherit">Включить</button>' +
        '<button onclick="' +
          'document.getElementById(\'' + bannerId + '\').remove();' +
          'localStorage.setItem(\'' + STORAGE_KEY + '\',\'1\');' +
        '" style="margin-left:4px;background:none;border:none;color:#94A3B8;' +
          'font-size:20px;cursor:pointer;line-height:1;padding:0 4px;flex-shrink:0"' +
          ' aria-label="Закрыть">&times;</button>';

      document.body.appendChild(el);

      document.getElementById('push-allow-btn').addEventListener('click', function () {
        el.remove();
        _requestAndSubscribe(reg);
      });
    }

    // Ждём первого действия пользователя
    document.addEventListener('click', show, { once: true });
    document.addEventListener('touchend', show, { once: true });
  }

  // ── Запрос разрешения + подписка ─────────────────────────────────────────
  function _requestAndSubscribe(reg) {
    Notification.requestPermission().then(function (perm) {
      if (perm === 'granted') {
        _ensureSubscription(reg);
      } else {
        localStorage.setItem(STORAGE_KEY, '1');
      }
    });
  }

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
