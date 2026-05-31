// Lattice AI Service Worker — enables PWA install on Android/iOS
// Strategy: network-first for API, cache-first for static assets.
const CACHE = "ltcai-v110";
const STATIC = [
  "/",
  "/workspace",
  "/static/lattice-reference.css",
  "/static/workspace.css",
  "/static/css/tokens.css",
  "/static/scripts/chat.js",
  "/static/scripts/admin.js",
  "/static/scripts/graph.js",
  "/static/scripts/workspace.js",
  "/static/scripts/account.js",
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);

  // API calls → always network (never cache)
  if (url.pathname.startsWith("/chat") ||
      url.pathname.startsWith("/agent") ||
      url.pathname.startsWith("/models") ||
      url.pathname.startsWith("/admin/") ||
      url.pathname.startsWith("/auth/") ||
      url.pathname.startsWith("/account/") ||
      url.pathname.startsWith("/vpc/") ||
      url.pathname.startsWith("/health") ||
      url.pathname.startsWith("/runtime_features") ||
      url.pathname.startsWith("/local") ||
      url.pathname.startsWith("/tools") ||
      url.pathname.startsWith("/knowledge") ||
      url.pathname.startsWith("/workspace/") ||
      url.pathname.startsWith("/history")) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static HTML → network-first, fall back to cache
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
