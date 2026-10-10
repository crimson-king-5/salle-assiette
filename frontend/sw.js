// Service worker: lets the app open without network (SPEC: PWA, offline).
// App files (/app/…) and fonts: "network first, with a 3 s limit" → fresh when
// the PC answers, the saved copy otherwise. API calls are not touched here:
// app.js handles them (timeout + snapshot saved on the phone).

const CACHE = "salle-v16";  // bump whenever a cached file changes
const SHELL = [
  "./", "style.css", "logic.js", "app.js", "workout.js", "assiette.js", "assiette-data.js",
  "progres.js", "coach.js", "exercices.js", "reglages.js", "programme.js",
  "manifest.webmanifest",
  "icon.svg", "icon-192.png", "icon-512.png", "icon-maskable-192.png", "icon-maskable-512.png",
];
const NETWORK_TIMEOUT_MS = 3000;

// Exercise media (/media/ex/…, served by the PC) lives in its OWN caches, so it survives app version bumps:
//   salle-media-v1         everything that was looked at, trimmed to the last MEDIA_MAX entries;
//   salle-media-pinned-v1  the current program's media, written by the page (exercices.js), never trimmed.
const MEDIA_CACHE = "salle-media-v1";
const MEDIA_PREFIX = "salle-media";
const MEDIA_MAX = 300;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();  // use the new version right away
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE && !key.startsWith(MEDIA_PREFIX)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function networkFirst(request) {
  return new Promise((resolve) => {
    let done = false;
    const answer = (response) => { if (!done && response) { done = true; resolve(response); } };

    // Too slow? Serve the saved copy (if there is one) and let the network finish in the background.
    const timer = setTimeout(() => caches.match(request).then(answer), NETWORK_TIMEOUT_MS);

    fetch(request)
      .then((response) => {
        clearTimeout(timer);
        // Keep good answers only (never an error page). Fonts come back "opaque":
        // the browser hides their status, but they're fine to keep.
        if (response.ok || response.type === "opaque") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        answer(response);
      })
      .catch(async () => {
        clearTimeout(timer);
        answer((await caches.match(request)) || Response.error());
      });
  });
}

// Media never changes under the same name: cache first, then the network (and keep what comes back).
async function mediaFirst(event) {
  const hit = await caches.match(event.request);
  if (hit) return hit;
  try {
    const response = await fetch(event.request);
    if (response.ok) {
      const cache = await caches.open(MEDIA_CACHE);
      await cache.put(event.request, response.clone());
      event.waitUntil(trimMediaCache(cache));
    }
    return response;
  } catch {
    return Response.error();  // offline and never seen: the page shows its placeholder
  }
}

async function trimMediaCache(cache) {
  const keys = await cache.keys();  // oldest first
  for (const key of keys.slice(0, Math.max(0, keys.length - MEDIA_MAX))) await cache.delete(key);
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "GET" && url.origin === self.location.origin && url.pathname.startsWith("/media/ex/")) {
    event.respondWith(mediaFirst(event));
    return;
  }
  const isAppFile = url.origin === self.location.origin && url.pathname.startsWith("/app/");
  const isFont = url.hostname.endsWith("fonts.googleapis.com") || url.hostname.endsWith("fonts.gstatic.com");
  if (event.request.method !== "GET" || !(isAppFile || isFont)) return;  // API: straight to the network
  event.respondWith(networkFirst(event.request));
});
