// modules/audio.js

export const engines = {
    active: new Audio(),
    buffer: new Audio()
};

// تنظیمات اولیه موتورهای صوتی
[engines.active, engines.buffer].forEach(audio => {
    audio.preload = "auto";
    audio.autoplay = false;
});

// ==========================================
// 🎛 WEB AUDIO API & CROSSFADE CONTROLLER
// ==========================================

let audioCtx = null;
let gainActive = null;
let gainBuffer = null;
let sourceActive = null;
let sourceBuffer = null;
let isWebAudioInitialized = false;

export function getAudioContext() {
    if (!audioCtx && typeof window !== 'undefined') {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) {
            try {
                audioCtx = new AudioContextClass();
                gainActive = audioCtx.createGain();
                gainBuffer = audioCtx.createGain();

                gainActive.gain.value = 1.0;
                gainBuffer.gain.value = 1.0;

                gainActive.connect(audioCtx.destination);
                gainBuffer.connect(audioCtx.destination);

                sourceActive = audioCtx.createMediaElementSource(engines.active);
                sourceBuffer = audioCtx.createMediaElementSource(engines.buffer);

                sourceActive.connect(gainActive);
                sourceBuffer.connect(gainBuffer);
                isWebAudioInitialized = true;
                console.log("🎛 [Web Audio API] GainNode Crossfader Connected Successfully.");
            } catch (e) {
                console.warn("[Web Audio API] Fallback to HTML5 audio:", e);
            }
        }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume().catch(() => {});
    }
    return audioCtx;
}

/**
 * جابجایی نرم بین دو قطعه با تکنیک استودیویی Crossfading
 * @param {number} duration - مدت زمان محو شدن به ثانیه (پیش‌فرض ۰.۳۵ ثانیه)
 */
export async function crossfadeEngines(duration = 0.35) {
    const ctx = getAudioContext();
    
    // اگر وب‌آدیو فعال باشد، شیب ولوم پیاده می‌شود
    if (ctx && isWebAudioInitialized && gainActive && gainBuffer) {
        try {
            const now = ctx.currentTime;
            gainActive.gain.cancelScheduledValues(now);
            gainActive.gain.setValueAtTime(gainActive.gain.value, now);
            gainActive.gain.linearRampToValueAtTime(0.01, now + duration);

            gainBuffer.gain.cancelScheduledValues(now);
            gainBuffer.gain.setValueAtTime(0.01, now);
            gainBuffer.gain.linearRampToValueAtTime(1.0, now + duration);

            await new Promise(r => setTimeout(r, duration * 1000));
            
            gainActive.gain.setValueAtTime(1.0, ctx.currentTime);
            gainBuffer.gain.setValueAtTime(1.0, ctx.currentTime);
        } catch (e) {
            console.warn("[Crossfade] WebAudio ramp error:", e);
        }
    }
    swapEngines();
}

/**
 * جایجایی فیزیکی موتورها و خلع سلاح لیسنرهای موتور بازنشسته
 */
export function swapEngines() {
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
 * سنجش سلامت بافر استریم در حافظه رم مرورگر
 * @param {HTMLAudioElement} audio 
 * @returns {number} ثانیه‌های بافر شده جلوتر از زمان فعلی
 */
export function getBufferedAhead(audio) {
    if (!audio || !audio.buffered || audio.buffered.length === 0) return 0;
    const cur = audio.currentTime;
    for (let i = 0; i < audio.buffered.length; i++) {
        if (audio.buffered.start(i) <= cur && cur <= audio.buffered.end(i)) {
            return audio.buffered.end(i) - cur;
        }
    }
    return 0;
}

/**
 * تنظیم لیسنرهای صوتی روی موتور فعال
 */
export function setupAudioListeners(onTimeUpdate, onEnded, onError, onPlayState) {
    engines.active.oncreate = null; 
    engines.active.ontimeupdate = onTimeUpdate;
    engines.active.onended = onEnded;
    engines.active.onerror = onError;
    
    engines.active.onplay = () => onPlayState(true);
    engines.active.onpause = () => onPlayState(false);
}