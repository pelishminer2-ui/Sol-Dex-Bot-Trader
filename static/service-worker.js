/* Minimal PWA service worker — network-first; API always live.
 * Brave Shields may block registration; the dashboard works without this file. */
const SW_VERSION = "solana-mover-bot-v9-smart-reentry-panel-20260708";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;

  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  // API and HTML: always fetch fresh (dashboard cache-busts via server headers).
  if (url.pathname.startsWith("/api/") || url.pathname === "/" || url.pathname.endsWith(".html")) {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
