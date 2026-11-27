const CACHE = "lattice-v1430";
const MANIFEST_URL = "/static/app/asset-manifest.json";

const SHELL = [
  "/app",
  MANIFEST_URL,
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
  "/static/vendor/fonts/inter.css",
  "/static/vendor/fonts/inter-latin-400-normal.woff2",
  "/static/vendor/fonts/inter-latin-500-normal.woff2",
  "/static/vendor/fonts/inter-latin-600-normal.woff2",
  "/static/vendor/fonts/inter-latin-700-normal.woff2",
  "/static/vendor/fonts/inter-latin-800-normal.woff2",
  "/static/vendor/icons/tabler-icons.min.css",
  "/static/vendor/icons/tabler-icons.woff2",
];

async function precache() {
  const cache = await caches.open(CACHE);
  let manifestPaths = [];
  try {
    const res = await fetch(MANIFEST_URL, { cache: "no-cache" });
    const manifest = await res.json();
    const entry = manifest.entrypoints || {};
    manifestPaths = [entry.app, ...Object.values(manifest.assets || {})]
      .filter(Boolean);
  } catch (err) {}
  const unique = [...new Set([...SHELL, ...manifestPaths])];
  await Promise.all(unique.map(async (path) => {
    try {
      const res = await fetch(path, { cache: "no-cache" });
      if (res.ok) await cache.put(path, res);
    } catch (err) {}
  }));
}

self.addEventListener("install", (e) => {
  e.waitUntil(precache().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;

  const isStatic =
    url.pathname.startsWith("/static/") ||
    url.pathname.startsWith("/icons/") ||
    url.pathname === "/manifest.json";

  if (isStatic) {
    e.respondWith(
      caches.match(e.request).then((hit) =>
        hit ||
        fetch(e.request).then((res) => {
          const clone = res.clone();
          if (res.ok) caches.open(CACHE).then((c) => c.put(e.request, clone));
          return res;
        })
      )
    );
    return;
  }

  if (url.pathname === "/app" || url.pathname.startsWith("/app/")) {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const clone = res.clone();
          if (res.ok) caches.open(CACHE).then((c) => c.put("/app", clone));
          return res;
        })
        .catch(() => caches.match("/app"))
    );
    return;
  }

});
