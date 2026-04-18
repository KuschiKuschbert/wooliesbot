// WooliesBot Service Worker — stale-while-revalidate cache
const CACHE = 'wooliesbot-v3-2036';
const PRECACHE = [
    './',
    './index.html',
    './discovery-review.html',
    './style.css',
    './app.js',
    './data.json',
    'https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap',
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

    // Network-first for data.json (always fresh)
    if (request.url.includes('data.json') || request.url.includes('heartbeat.json')) {
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
