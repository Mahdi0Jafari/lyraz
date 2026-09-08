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
let isWebAudioInitialized = false;

function initEngineGain(audio) {
    if (audio._gainNode) return audio._gainNode;
    if (!audioCtx) return null;
    try {
        const source = audioCtx.createMediaElementSource(audio);
        const gain = audioCtx.createGain();
        gain.gain.value = 1.0;
        source.connect(gain);
        gain.connect(audioCtx.destination);
        audio._gainNode = gain;
        return gain;
    } catch (e) {
        console.warn("[Web Audio API] Node creation fallback:", e);
        return null;
    }
}

export function getAudioContext() {
    if (!audioCtx && typeof window !== 'undefined') {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) {
            try {
                audioCtx = new AudioContextClass();
                initEngineGain(engines.active);
                initEngineGain(engines.buffer);
                isWebAudioInitialized = true;
                console.log("🎛 [Web Audio API] Studio GainNodes Connected Successfully.");
            } catch (e) {
                console.warn("[Web Audio API] Fallback to HTML5 audio:", e);
            }
        }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume().catch(() => {});
    }
    if (audioCtx && isWebAudioInitialized) {
        if (!engines.active._gainNode) initEngineGain(engines.active);
        if (!engines.buffer._gainNode) initEngineGain(engines.buffer);
    }
    return audioCtx;
}

/**
 * جابجایی نرم بین دو قطعه با تکنیک استودیویی Crossfading
 * @param {number} duration - مدت زمان محو شدن به ثانیه (پیش‌فرض ۰.۳۵ ثانیه)
 */
export async function crossfadeEngines(duration = 0.35) {
    const ctx = getAudioContext();
    const oldEngine = engines.active;
    const newEngine = engines.buffer;

    const oldGain = oldEngine._gainNode;
    const newGain = newEngine._gainNode;

    // شروع فوری پخش قطعه جدید
    const playPromise = newEngine.play();
    if (playPromise !== undefined) {
        playPromise.catch(e => console.warn("[Crossfade] Buffer play blocked:", e));
    }

    if (ctx && isWebAudioInitialized && oldGain && newGain) {
        try {
            const now = ctx.currentTime;
            
            // محو کردن نرم صدای قطعه فعلی
            oldGain.gain.cancelScheduledValues(now);
            oldGain.gain.setValueAtTime(oldGain.gain.value, now);
            oldGain.gain.linearRampToValueAtTime(0.001, now + duration);

            // افزایش نرم صدای قطعه جدید
            newGain.gain.cancelScheduledValues(now);
            newGain.gain.setValueAtTime(0.001, now);
            newGain.gain.linearRampToValueAtTime(1.0, now + duration);

            await new Promise(r => setTimeout(r, duration * 1000));
            
            // بازنشانی سطح ولوم
            oldGain.gain.setValueAtTime(1.0, ctx.currentTime);
            newGain.gain.setValueAtTime(1.0, ctx.currentTime);
        } catch (e) {
            console.warn("[Crossfade] WebAudio ramp error:", e);
            await new Promise(r => setTimeout(r, duration * 1000));
        }
    } else {
        // فالبک مبتنی بر تایمر بدون وب‌آدیو
        await new Promise(r => setTimeout(r, duration * 1000));
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