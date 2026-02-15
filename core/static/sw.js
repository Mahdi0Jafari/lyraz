const CACHE_NAME = 'fanus-player-v1';
const ASSETS_TO_CACHE = [
    '/',
    '/static/css/style.css',
    '/static/js/player.js',
    '/static/js/lyrics.js',
    '/static/manifest.json'
];

// 1. Install Service Worker
self.addEventListener('install', (evt) => {
    evt.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('✅ Caching shell assets');
            return cache.addAll(ASSETS_TO_CACHE);
        })
    );
});

// 2. Activate & Clean up old caches
self.addEventListener('activate', (evt) => {
    evt.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(keys
                .filter(key => key !== CACHE_NAME)
                .map(key => caches.delete(key))
            );
        })
    );
});

// 3. Fetch Events (Network First, Fallback to Cache)
// ما از استراتژی "اول شبکه" استفاده می‌کنیم تا همیشه آخرین نسخه آهنگ‌ها و داده‌ها بیاید.
self.addEventListener('fetch', (evt) => {
    // فقط درخواست‌های http/https را هندل کن (نه chrome-extension و غیره)
    if (evt.request.url.indexOf('http') === 0) {
        evt.respondWith(
            fetch(evt.request)
                .then((fetchRes) => {
                    return caches.open(CACHE_NAME).then((cache) => {
                        // آپدیت کردن کش با نسخه جدید
                        cache.put(evt.request.url, fetchRes.clone());
                        return fetchRes;
                    });
                })
                .catch(() => {
                    // اگر نت قطع بود، از کش بخوان
                    return caches.match(evt.request);
                })
        );
    }
});