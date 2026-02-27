(function () {
  'use strict';

  var btn = document.getElementById('pushToggle');
  if (!btn) return;

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    btn.textContent = 'Push not supported';
    btn.disabled = true;
    return;
  }

  var swReg = null;

  navigator.serviceWorker.ready.then(function (reg) {
    swReg = reg;
    return reg.pushManager.getSubscription();
  }).then(function (sub) {
    updateButton(sub !== null);
  });

  btn.addEventListener('click', async function () {
    if (!swReg) return;

    var existing = await swReg.pushManager.getSubscription();
    if (existing) {
      await existing.unsubscribe();
      await fetch('/dinaro/push/unsubscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: existing.endpoint }),
      });
      updateButton(false);
    } else {
      try {
        var resp = await fetch('/dinaro/push/vapid-public-key');
        var data = await resp.json();
        if (!data.publicKey) return;

        var sub = await swReg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(data.publicKey),
        });

        var subJson = sub.toJSON();
        await fetch('/dinaro/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: subJson.endpoint, keys: subJson.keys }),
        });

        updateButton(true);
      } catch (err) {
        if (Notification.permission === 'denied') {
          btn.textContent = 'Notifications blocked';
          btn.disabled = true;
        } else {
          console.error('Push subscription failed:', err);
        }
      }
    }
  });

  function updateButton(isSubscribed) {
    if (isSubscribed) {
      btn.textContent = 'Notifications on';
      btn.classList.add('btn-active');
    } else {
      btn.textContent = 'Enable notifications';
      btn.classList.remove('btn-active');
    }
  }

  function urlBase64ToUint8Array(base64String) {
    var padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    var rawData = atob(base64);
    return Uint8Array.from([...rawData].map(function (c) { return c.charCodeAt(0); }));
  }
})();
