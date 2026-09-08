// modules/audio.js

export const engines = {
    active: new Audio(),
    buffer: new Audio()
};

// تنظیمات اولیه
[engines.active, engines.buffer].forEach(audio => {
    audio.preload = "auto";
    audio.autoplay = false;
});

/**
 * جایجایی موتورها برای پخش بدون وقفه
 */
export function swapEngines() {
    // ۱. پاکسازی کامل کلیه لیسنرهای موتور قبلی قبل از توقف جهت جلوگیری از فایر شدن رویدادهای کاذب ended و error
    engines.active.ontimeupdate = null;
    engines.active.onended = null;
    engines.active.onerror = null;
    engines.active.onplay = null;
    engines.active.onpause = null;

    try {
        engines.active.pause();
        engines.active.currentTime = 0;
    } catch (e) {}
    
    // جابجایی رفرنس‌ها
    const temp = engines.active;
    engines.active = engines.buffer;
    engines.buffer = temp;
    
    // پاکسازی امن بافر قبلی
    engines.buffer.removeAttribute('src');
    try {
        engines.buffer.load();
    } catch (e) {}
}

/**
 * تنظیم لیسنرهای صوتی روی موتور فعال
 * @param {Function} onTimeUpdate - تابع آپدیت زمان
 * @param {Function} onEnded - تابع پایان آهنگ
 * @param {Function} onError - تابع خطا
 * @param {Function} onPlayState - تابع تغییر وضعیت پخش
 */
export function setupAudioListeners(onTimeUpdate, onEnded, onError, onPlayState) {
    // پاک کردن لیسنرهای قبلی حیاتی است
    engines.active.oncreate = null; 
    engines.active.ontimeupdate = onTimeUpdate;
    engines.active.onended = onEnded;
    engines.active.onerror = onError;
    
    engines.active.onplay = () => onPlayState(true);
    engines.active.onpause = () => onPlayState(false);
}