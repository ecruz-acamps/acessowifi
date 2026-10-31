// Service worker do painel Acesso Wi-Fi (servido em /sw.js com a versão no lugar de __VERSION__).
// Guarda só a "casca" do app (CSS, JS, ícones, página offline). Dados do painel (/api/*) e as
// páginas com login NUNCA são guardados: vêm sempre do servidor.
const VERSION = "__VERSION__";
const CACHE = `acessowifi-${VERSION}`;
const SHELL = [
  "/static/style.css", "/static/app.js", "/static/offline.html", "/static/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  // Versão nova do painel: apaga a casca antiga
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("acessowifi-") && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;   // dados: sempre da rede, nunca do cache

  if (req.mode === "navigate") {
    // Páginas: o navegador busca sozinho, para mostrar os avisos dele (ex.: certificado HTTPS não
    // confiável depois de uma troca de certificado). A página offline só aparece quando o aparelho
    // está de fato sem rede.
    if (!self.navigator.onLine) event.respondWith(caches.match("/static/offline.html"));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    // Casca: responde do cache e atualiza em segundo plano
    event.respondWith(caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(req);
      const fresh = fetch(req).then((res) => { if (res.ok) cache.put(req, res.clone()); return res; }).catch(() => cached);
      return cached || fresh;
    }));
  }
});
