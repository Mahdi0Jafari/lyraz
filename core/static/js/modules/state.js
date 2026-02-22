// modules/state.js

export const CONFIG = {
    retryLimit: 3,
    preloadGap: 15
};

/**
 * 🔥 V4 ANTI-BUG LOGIC: Token Authority Hierarchy
 * 1. URL Path (/live/xxxxxxx) -> Absolute Master
 * 2. Server Injection (window.INJECTED_TOKEN) -> Context Master
 * 3. LocalStorage (fanus_session_token) -> Persistence Fallback
 */

const getUrlToken = () => {
    const pathParts = window.location.pathname.split('/');
    // پیدا کردن بخشی که بعد از /live/ یا /remote/ می‌آید
    const liveIdx = pathParts.indexOf('live');
    const remoteIdx = pathParts.indexOf('remote');
    const targetIdx = Math.max(liveIdx, remoteIdx);
    
    if (targetIdx !== -1 && pathParts[targetIdx + 1]) {
        const token = pathParts[targetIdx + 1].trim();
        // توکن‌های V4 دقیقاً 7 کاراکتر هستند
        if (token.length === 7) return token;
    }
    return null;
};

const urlToken = getUrlToken();
const storageToken = localStorage.getItem('fanus_session_token');

// تعیین توکن نهایی بر اساس سلسله مراتب قدرت
const resolvedToken = urlToken || window.INJECTED_TOKEN || storageToken;

// پایداری: اگر توکن جدیدی پیدا شده که در حافظه نیست، حافظه را بروزرسانی کن
if (resolvedToken && resolvedToken !== storageToken) {
    console.log(`[State] Migrating session to: ${resolvedToken}`);
    localStorage.setItem('fanus_session_token', resolvedToken);
}

export const state = {
    // شناسه‌های هاب
    sessionToken: resolvedToken,
    hubStatus: window.HUB_STATUS || 'waiting', 
    
    // وضعیت پخش
    tracks: [],
    currentIndex: 0,
    isPlaying: false,
    isDragging: false,
    retryCount: 0,
    
    // تنظیمات تجربه کاربری
    shuffle: false,
    repeatMode: 'all', // 'off', 'all', 'one'
    
    // 🔥 لایه‌ی کنترل همگام‌سازی (Multi-Screen Sync)
    isSyncing: false,       // وقتی سرور در حال آپدیت ماست، ریپورت به سرور را قفل می‌کند
    serverTimeOffset: 0,    // اختلاف میلی‌ثانیه‌ای ساعت کلاینت و سرور
    lastSyncTimestamp: 0,   // آخرین باری که وضعیت از دیتابیس خوانده شد
    
    // ریکاوری شبکه
    recovery: {
        active: false,
        time: 0,
        trackId: null,
        wasPlaying: false
    }
};

console.log(`🚀 Hub State Initialized | Token: ${state.sessionToken} | Status: ${state.hubStatus}`);