// Service worker: lets the app open without network (SPEC: PWA, offline).
// App files (/app/…) and fonts: "network first, with a 3 s limit" → fresh when
// the PC answers, the saved copy otherwise. API calls are not touched here:
// app.js handles them (timeout + snapshot saved on the phone).

const CACHE = "salle-v12";  // bump whenever a cached file changes
const SHELL = [
  "./", "style.css", "logic.js", "app.js", "workout.js", "assiette.js", "assiette-data.js",
  "progres.js", "reglages.js", "programme.js",
  "manifest.webmanifest",
  "icon.svg", "icon-192.png", "icon-512.png", "icon-maskable-192.png", "icon-maskable-512.png",
];
const NETWORK_TIMEOUT_MS = 3000;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();  // use the new version right away
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
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

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isAppFile = url.origin === self.location.origin && url.pathname.startsWith("/app/");
  const isFont = url.hostname.endsWith("fonts.googleapis.com") || url.hostname.endsWith("fonts.gstatic.com");
  if (event.request.method !== "GET" || !(isAppFile || isFont)) return;  // API: straight to the network
  event.respondWith(networkFirst(event.request));
});
