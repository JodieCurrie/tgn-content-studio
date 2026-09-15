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
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/badge-96.png",
      tag: payload.tag || "tgn-notification",
      data: { url: payload.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";
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
