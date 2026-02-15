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
    engines.active.pause();
    engines.active.currentTime = 0;
    
    // جابجایی رفرنس‌ها
    const temp = engines.active;
    engines.active = engines.buffer;
    engines.buffer = temp;
    
    // پاکسازی بافر
    engines.buffer.src = "";
    engines.buffer.load();
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