// TGN Content Studio service worker (Sept).
//
// Two jobs, both required just for the site to be installable and for
// push notifications to work — this is deliberately NOT an offline-first
// cache (the app needs a live server for basically everything it does,
// so there's no real value in caching pages, and stale cached data would
// just be confusing):
//   1. Existing (even a no-op fetch handler makes Chrome/Android treat
//      the site as installable — some browsers require one to be
//      registered before showing an "Add to Home Screen" prompt).
//   2. Handle `push` events (show the notification) and `notificationclick`
//      (focus/open the app) even when no tab is open.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Pass-through fetch handler — required for installability on some
// browsers, intentionally does no caching (see note above).
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});

self.addEventListener("push", (event) => {
  let payload = { title: "TGN Content Studio", body: "You have an update.", url: "/" };
  if (event.data) {
    try {
      payload = Object.assign(payload, event.data.json());
    } catch (e) {
      payload.body = event.data.text();
    }
  }
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "/static/icons/icon-192.png?v=2026-11b",
      badge: "/static/icons/badge-96.png?v=2026-11b",
      tag: payload.tag || "tgn-notification",
      // actions is what puts real buttons ("Delay 10 min", "Reschedule")
      // on a reminder notification — only post-due reminders include
      // one (see app/push.py's send_post_reminders). Not supported on
      // iOS Safari: there, tapping the notification just opens the app,
      // same as any notification without actions.
      actions: payload.actions || [],
      data: { url: payload.url || "/", output_id: payload.output_id || null },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";
  const outputId = event.notification.data && event.notification.data.output_id;
  event.notification.close();

  const ACTION_ENDPOINTS = {
    delay: ["/api/push/delay-reminder", "Couldn't delay that reminder."],
    reschedule: ["/api/push/reschedule-output", "Couldn't reschedule that post."],
  };
  if (ACTION_ENDPOINTS[event.action] && outputId) {
    // "Delay 10 min" / "Reschedule" — no need to open the app, just tell the
    // server and show what happened.
    const [endpoint, failText] = ACTION_ENDPOINTS[event.action];
    event.waitUntil(
      fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ output_id: outputId }),
      })
        .then((res) => res.json())
        .then((result) => {
          const message = result.message || (result.ok ? "Done." : failText);
          return self.registration.showNotification("TGN Content Studio", {
            body: message,
            icon: "/static/icons/icon-192.png?v=2026-11b",
            badge: "/static/icons/badge-96.png?v=2026-11b",
            tag: event.notification.tag,
            data: { url: targetUrl },
          });
        })
        .catch(() =>
          self.registration.showNotification("TGN Content Studio", {
            body: "Couldn't reach the server — try again from the app.",
            icon: "/static/icons/icon-192.png?v=2026-11b",
            tag: event.notification.tag,
            data: { url: targetUrl },
          })
        )
    );
    return;
  }

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientsArr) => {
      for (const client of clientsArr) {
        if ("focus" in client) {
          if ("navigate" in client) {
            try { client.navigate(targetUrl); } catch (e) { /* best effort */ }
          }
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    })
  );
});
