// PWA install + push notification plumbing (Sept). Loaded on every
// logged-in page (see base.html) so the service worker registers as
// soon as possible, but the actual UI (enable/disable/test buttons,
// install button) only wires up on pages that have those elements —
// currently just Account.

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

let swRegistration = null;

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return null;
  try {
    swRegistration = await navigator.serviceWorker.register("/service-worker.js");
    return swRegistration;
  } catch (e) {
    console.warn("Service worker registration failed:", e);
    return null;
  }
}

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function refreshPushStatusUI() {
  const statusEl = document.getElementById("push-status-text");
  const enableBtn = document.getElementById("js-enable-push");
  const disableBtn = document.getElementById("js-disable-push");
  const testBtn = document.getElementById("js-test-push");
  if (!statusEl && !enableBtn) return; // this page has no notification UI

  if (!pushSupported()) {
    if (statusEl) statusEl.textContent = "This browser doesn't support push notifications.";
    if (enableBtn) enableBtn.style.display = "none";
    if (disableBtn) disableBtn.style.display = "none";
    if (testBtn) testBtn.style.display = "none";
    return;
  }
  if (Notification.permission === "denied") {
    if (statusEl) statusEl.textContent = "Notifications are blocked for this site in your browser settings — turn them back on there to enable.";
    if (enableBtn) enableBtn.style.display = "none";
    if (disableBtn) disableBtn.style.display = "none";
    if (testBtn) testBtn.style.display = "none";
    return;
  }

  let subscribed = false;
  try {
    const reg = swRegistration || (await navigator.serviceWorker.getRegistration());
    const sub = reg && (await reg.pushManager.getSubscription());
    subscribed = !!sub;
  } catch (e) { /* ignore */ }

  if (statusEl) statusEl.textContent = subscribed
    ? "Notifications are on for this device."
    : "Notifications are off for this device.";
  if (enableBtn) enableBtn.style.display = subscribed ? "none" : "";
  if (disableBtn) disableBtn.style.display = subscribed ? "" : "none";
  if (testBtn) testBtn.style.display = subscribed ? "" : "none";
}

async function enablePush() {
  const statusEl = document.getElementById("push-status-text");
  if (!pushSupported()) return;
  const publicKey = window.TGN_PUSH_PUBLIC_KEY;
  if (!publicKey) {
    if (statusEl) statusEl.textContent = "Notifications aren't fully set up on the server yet.";
    return;
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    await refreshPushStatusUI();
    return;
  }
  const reg = swRegistration || (await registerServiceWorker());
  if (!reg) return;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  });
  const json = sub.toJSON();
  await tgnFetch("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys }),
  });
  await refreshPushStatusUI();
}

async function disablePush() {
  const reg = swRegistration || (await navigator.serviceWorker.getRegistration());
  const sub = reg && (await reg.pushManager.getSubscription());
  if (sub) {
    const endpoint = sub.endpoint;
    await sub.unsubscribe();
    await tgnFetch("/api/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint }) });
  }
  await refreshPushStatusUI();
}

async function sendTestPush() {
  const statusEl = document.getElementById("push-test-result");
  try {
    await tgnFetch("/api/push/test", { method: "POST", body: "{}" });
    if (statusEl) { statusEl.textContent = "Sent — it should show up any second."; statusEl.style.display = "block"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = e.message; statusEl.style.display = "block"; }
  }
}

// -------------------------------------------------------- "install app"
let deferredInstallPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstallPrompt = e;
  const installBtn = document.getElementById("js-install-app");
  if (installBtn) installBtn.style.display = "";
});

async function promptInstall() {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  const installBtn = document.getElementById("js-install-app");
  if (installBtn) installBtn.style.display = "none";
}

document.addEventListener("DOMContentLoaded", () => {
  registerServiceWorker().then(refreshPushStatusUI);
  const enableBtn = document.getElementById("js-enable-push");
  const disableBtn = document.getElementById("js-disable-push");
  const testBtn = document.getElementById("js-test-push");
  const installBtn = document.getElementById("js-install-app");
  if (enableBtn) enableBtn.addEventListener("click", enablePush);
  if (disableBtn) disableBtn.addEventListener("click", disablePush);
  if (testBtn) testBtn.addEventListener("click", sendTestPush);
  if (installBtn) installBtn.addEventListener("click", promptInstall);
});
