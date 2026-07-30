/* Minimal PWA service worker — network-first; API always live.
 * Brave Shields may block registration; the dashboard works without this file.
 *
 * CRITICAL: never intercept /static/* — a failed SW fetch with an empty cache
 * can respondWith(undefined) and break wallet_connect.js load
 * (window.SolDexWalletConnect stays missing → Connect alert).
 */
const SW_VERSION = "solana-mover-bot-v115-wallet-static-bypass-20260727";

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

  // Let the browser load dashboard JS/CSS/assets directly (no SW Response).
  if (url.pathname.startsWith("/static/")) return;

  // API and HTML: always fetch fresh (dashboard cache-busts via server headers).
  if (url.pathname.startsWith("/api/") || url.pathname === "/" || url.pathname.endsWith(".html")) {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    fetch(event.request).catch(function () {
      return caches.match(event.request).then(function (cached) {
        return cached || Response.error();
      });
    })
  );
});
