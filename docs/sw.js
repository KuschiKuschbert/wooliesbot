// WooliesBot Service Worker — stale-while-revalidate cache.
// If the UI looks stale after deploy: DevTools → Application → Service Workers → Unregister,
// or hard-refresh; cache name bumps force a fresh precache on next visit.
const CACHE = 'wooliesbot-v7-boot-hardening';
const PRECACHE = [
    './',
    './index.html',
    './discovery-review.html',
    './style.css',
    './app.js',
    './js/compare_helpers.js',
    './js/store_pdp_link.js',
    './data.json',
    'https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap',
    'https://cdn.jsdelivr.net/npm/chart.js',
    'https://cdn.jsdelivr.net/npm/feather-icons/dist/feather.min.js',
];

self.addEventListener('install', event => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE).then(cache => cache.addAll(PRECACHE).catch(() => {}))
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const { request } = event;
    // Only handle GET requests
    if (request.method !== 'GET') return;
    const url = request.url || '';

    // Always bypass cache for cross-device shopping list sync.
    if (url.includes('/shopping_list')) {
        event.respondWith(fetch(request).catch(() => caches.match(request)));
        return;
    }

    // Network-first for data.json (always fresh)
    if (url.includes('data.json') || url.includes('heartbeat.json')) {
        event.respondWith(
            fetch(request)
                .then(res => {
                    const clone = res.clone();
                    caches.open(CACHE).then(c => c.put(request, clone));
                    return res;
                })
                .catch(() => caches.match(request))
        );
        return;
    }

    // Stale-while-revalidate for everything else
    event.respondWith(
        caches.open(CACHE).then(cache =>
            cache.match(request).then(cached => {
                const network = fetch(request).then(res => {
                    if (res.ok) cache.put(request, res.clone());
                    return res;
                }).catch(() => cached);
                return cached || network;
            })
        )
    );
});
