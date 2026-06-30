(function () {
  // Уже запущено как PWA — ничего не показываем
  var isStandalone =
    window.navigator.standalone === true ||
    window.matchMedia('(display-mode: standalone)').matches;
  if (isStandalone) return;

  // ─── Определение платформы ──────────────────────────────────────────────
  var ua = navigator.userAgent;
  var isIOS = /iPhone|iPad|iPod/i.test(ua) && !/CriOS|FxiOS|OPiOS/i.test(ua);
  // Safari на iOS: есть window.safari или нет Chrome/Firefox/Opera
  var isSafariIOS = isIOS && /Safari/i.test(ua);

  // ─── Создаём элемент баннера ─────────────────────────────────────────────
  function createBanner(html, id) {
    var el = document.createElement('div');
    el.id = id;
    el.innerHTML = html;
    Object.assign(el.style, {
      position: 'fixed',
      bottom: '16px',
      left: '50%',
      transform: 'translateX(-50%)',
      zIndex: '9999',
      width: 'calc(100% - 32px)',
      maxWidth: '480px',
      background: '#1E293B',
      color: '#fff',
      borderRadius: '12px',
      boxShadow: '0 4px 20px rgba(0,0,0,0.25)',
      padding: '14px 16px',
      display: 'flex',
      alignItems: 'center',
      gap: '12px',
      fontSize: '14px',
      lineHeight: '1.4',
      fontFamily: 'Inter, -apple-system, sans-serif',
    });
    return el;
  }

  function closeBtn(bannerId, storageKey) {
    return '<button onclick="' +
      'document.getElementById(\'' + bannerId + '\').remove();' +
      (storageKey ? 'localStorage.setItem(\'' + storageKey + '\',\'1\');' : '') +
      '" style="margin-left:auto;background:none;border:none;color:#94A3B8;' +
      'font-size:20px;cursor:pointer;line-height:1;padding:0 4px;flex-shrink:0"' +
      ' aria-label="Закрыть">&times;</button>';
  }

  // ─── Android / Desktop: beforeinstallprompt ──────────────────────────────
  var deferredPrompt = null;

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;

    var bannerId = 'pwa-install-banner';
    if (document.getElementById(bannerId)) return;

    var banner = createBanner(
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" ' +
        'stroke="#3B82F6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
        'style="flex-shrink:0">' +
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
        '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>' +
      '</svg>' +
      '<div style="flex:1">' +
        '<div style="font-weight:600;margin-bottom:2px">Установить ШТАБ</div>' +
        '<div style="color:#94A3B8;font-size:12px">Работает быстрее как приложение</div>' +
      '</div>' +
      '<button id="pwa-install-btn" style="background:#3B82F6;color:#fff;border:none;' +
        'border-radius:8px;padding:8px 16px;font-size:13px;font-weight:500;cursor:pointer;' +
        'white-space:nowrap;font-family:inherit">Установить</button>' +
      closeBtn(bannerId, null),
      bannerId
    );

    document.body.appendChild(banner);

    document.getElementById('pwa-install-btn').addEventListener('click', function () {
      banner.remove();
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(function () { deferredPrompt = null; });
    });
  });

  window.addEventListener('appinstalled', function () {
    var b = document.getElementById('pwa-install-banner');
    if (b) b.remove();
    deferredPrompt = null;
  });

  // ─── iOS Safari: разовая подсказка ───────────────────────────────────────
  if (isSafariIOS) {
    var STORAGE_KEY = 'shtab-ios-hint-dismissed';
    if (localStorage.getItem(STORAGE_KEY)) return;

    // Небольшая задержка, чтобы не мешать загрузке страницы
    setTimeout(function () {
      var bannerId = 'pwa-ios-banner';
      if (document.getElementById(bannerId)) return;

      var banner = createBanner(
        '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" ' +
          'stroke="#3B82F6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
          'style="flex-shrink:0">' +
          '<circle cx="12" cy="12" r="10"/>' +
          '<line x1="12" y1="8" x2="12" y2="12"/>' +
          '<line x1="12" y1="16" x2="12.01" y2="16"/>' +
        '</svg>' +
        '<div style="flex:1">' +
          '<div style="font-weight:600;margin-bottom:4px">Установить ШТАБ</div>' +
          '<div style="color:#94A3B8;font-size:12px;line-height:1.5">' +
            'Нажмите ' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" ' +
              'stroke-width="2" style="vertical-align:-2px">' +
              '<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/>' +
              '<polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/>' +
            '</svg>' +
            ' <b style="color:#CBD5E1">«Поделиться»</b>, затем ' +
            '<b style="color:#CBD5E1">«На экран "Домой"»</b>' +
          '</div>' +
        '</div>' +
        closeBtn(bannerId, STORAGE_KEY),
        bannerId
      );

      document.body.appendChild(banner);
    }, 3000);
  }
})();
