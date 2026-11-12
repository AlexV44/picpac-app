// Сервис-воркер: оболочка приложения доступна офлайн, API и картинки всегда с сервера.
const CACHE = "picpac-shell-v13";
const SHELL = ["/", "/style.css", "/app.js", "/i18n.js", "/mascots.js", "/manifest.json", "/icons/icon-192.png", "/img/logo-white.png", "/img/hero.jpg", "/img/login.jpg"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/") || u.pathname.startsWith("/mock-")) return;
  // сначала кэш (мгновенный старт на телефоне), а свежая версия подгружается в фоне и будет при следующем открытии
  e.respondWith(caches.match(e.request).then(m => {
    const net = fetch(e.request).then(r => { if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); } return r; }).catch(() => null);
    return m || net.then(r => r || caches.match("/"));
  }));
