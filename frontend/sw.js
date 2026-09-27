// Service worker: lets the app open without network (SPEC: PWA, offline).
// Strategy "network first": fresh data when online, last saved copy when offline.

const CACHE = "salle-v3";  // bump when the file list changes
const SHELL = [
  "./", "style.css", "app.js", "workout.js", "manifest.webmanifest",
  "icon.svg", "icon-192.png", "icon-512.png", "icon-maskable-192.png", "icon-maskable-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))
    )
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {  // never keep an error page as the "last good copy"
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
