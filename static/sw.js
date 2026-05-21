// Lattice AI Service Worker — enables PWA install on Android/iOS
// Strategy: network-first for API, cache-first for static assets.
const CACHE = "ltcai-v1";
const STATIC = [
  "/",
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
      url.pathname.startsWith("/local") ||
      url.pathname.startsWith("/tools") ||
      url.pathname.startsWith("/knowledge") ||
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
