/**
 * Naqoos Service Worker (Enterprise Edition v2)
 * Optimized for Flaky Networks & Smart TVs
 */

const CACHE_NAME = 'fanus-player-v2';
// فقط فایل‌های استاتیک که برای لود شدن ظاهر پلیر حیاتی هستند
const ASSETS_TO_CACHE = [
    '/',
    '/static/css/style.css',
    '/static/js/player.js',
    '/static/js/lyrics.js',
    '/static/js/modules/audio.js',
    '/static/js/modules/network.js',
    '/static/js/modules/state.js',
    '/static/js/modules/ui.js',
    '/static/manifest.json'
];

// 1. Install Phase
self.addEventListener('install', (evt) => {
    self.skipWaiting(); // اجبار به فعال‌سازی فوری نسخه جدید
    evt.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW] Caching UI shell');
            return cache.addAll(ASSETS_TO_CACHE);
        })
    );
});

// 2. Activate & Clean up
self.addEventListener('activate', (evt) => {
    evt.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(keys
                .filter(key => key !== CACHE_NAME)
                .map(key => {
                    console.log('[SW] Removing old cache', key);
                    return caches.delete(key);
                })
            );
        })
    );
    self.clients.claim(); // در دست گرفتن کنترل تمام تب‌های باز
});

// 3. Fetch Routing Logic (The Core Fix)
self.addEventListener('fetch', (evt) => {
    const url = new URL(evt.request.url);

    // ⛔ Rule A: BYPASS CRITICAL DATA & STREAMS
    // هرگز نباید رسانه‌ها، APIها و تونل‌های SSE از Service Worker عبور کنند یا کش شوند.
    // این کار باعث می‌شود مرورگر از مکانیزم بومی خود برای خطایابی شبکه قطع‌و‌وصلی استفاده کند.
    if (
        url.pathname.startsWith('/stream/') || 
        url.pathname.startsWith('/api/') || 
        url.pathname.startsWith('/events')
    ) {
        return; // خروج: اجازه بده درخواست مستقیماً به شبکه برود
    }

    // ⛔ Rule B: IGNORE NON-HTTP(S) SCHEMES (e.g., chrome-extension://)
    if (!evt.request.url.startsWith('http')) return;

    // ✅ Rule C: STALE-WHILE-REVALIDATE FOR UI ASSETS
    // برای فایل‌های استاتیک، ابتدا از کش سریع بخوان تا پلیر لود شود، 
    // سپس در پس‌زمینه از شبکه آپدیت کن تا همیشه آخرین نسخه JS/CSS را داشته باشیم.
    evt.respondWith(
        caches.match(evt.request).then((cachedResponse) => {
            const networkFetch = fetch(evt.request).then((networkResponse) => {
                // آپدیت کردن کش به صورت بی‌صدا
                if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
                    const responseToCache = networkResponse.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(evt.request, responseToCache));
                }
                return networkResponse;
            }).catch(() => {
                // مدیریت قطعی شبکه در زمان فچ کردن فایل استاتیک
                console.warn('[SW] Fetch failed; returning offline cache.');
            });

            // اگر در کش بود فورا بده، در غیر این صورت منتظر شبکه بمان
            return cachedResponse || networkFetch;
        })
    );
});