// WooliesBot Service Worker — data + app shell are network-first; other assets stale-while-revalidate.
// If the UI looks stale after deploy: DevTools → Application → Service Workers → Unregister,
// or hard-refresh; cache name bumps force a fresh precache on next visit.

const SHELL_VERSION = '2038-pairing-page';
const CACHE = `wooliesbot-${SHELL_VERSION}`;

const PRECACHE = [
    './',
    './index.html',
    './discovery-review.html',
    './pairing.html',
    './style.css',
    './env.js',
    './js/household_sync.js',
    './app.js',
    './js/compare_helpers.js',
    './js/store_pdp_link.js',
    './data.json',
    'https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap',
    'https://cdn.jsdelivr.net/npm/chart.js',
    'https://cdn.jsdelivr.net/npm/feather-icons/dist/feather.min.js',
];

function isAppShellRequest(url) {
    try {
        if (!url.startsWith('http')) return false;
        const u = new URL(url);
        if (u.origin !== self.location.origin) return false;
        const p = u.pathname;
        if (p.endsWith('/data.json') || p.endsWith('/heartbeat.json') || p.endsWith('/receipt_sync_status.json')) return false;
        return /\/(app|sw|env)\.js$|\/index\.html$|\/style\.css$|\/js\/household_sync\.js$|\/js\/compare_helpers\.js$|\/js\/store_pdp_link\.js$|\/manifest\.webmanifest$|\/discovery-review\.html$|\/pairing\.html$/.test(
            p
        );
    } catch {
        return false;
    }
}

function networkFirstWithCacheUpdate(request) {
    return fetch(request)
        .then(res => {
            const clone = res.clone();
            caches.open(CACHE).then(c => c.put(request, clone));
            return res;
        })
        .catch(() => caches.match(request));
}

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE).then(cache => cache.addAll(PRECACHE).catch(() => {})).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches
            .keys()
            .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const { request } = event;
    if (request.method !== 'GET') return;
    const url = request.url || '';

    if (url.includes('/shopping_list')) {
        event.respondWith(fetch(request).catch(() => caches.match(request)));
        return;
    }

    if (url.includes('data.json') || url.includes('heartbeat.json') || url.includes('receipt_sync_status.json') || isAppShellRequest(url)) {
        event.respondWith(networkFirstWithCacheUpdate(request));
        return;
    }

    event.respondWith(
        caches.open(CACHE).then(cache =>
            cache.match(request).then(cached => {
                const network = fetch(request)
                    .then(res => {
                        if (res.ok) cache.put(request, res.clone());
                        return res;
                    })
                    .catch(() => cached);
                return cached || network;
            })
        )
    );
});
