/**
 * Fanus Player - Main Controller (Enterprise v12)
 * Features: Smart Preload, Adaptive Network, Zero-Flood SSE
 */
import { state, CONFIG } from './modules/state.js';
import { engines, swapEngines, setupAudioListeners } from './modules/audio.js';
import * as UI from './modules/ui.js';
import * as Network from './modules/network.js';

// --- Throttling State ---
let lastReportedSecond = -1;
let lastReportTimestamp = 0;

// ==========================================
// 🚀 INITIALIZATION
// ==========================================

document.addEventListener('DOMContentLoaded', () => {
    console.log("🚀 Fanus Player Enterprise Initializing...");
    
    setupAudioListeners(
        onTimeUpdate,
        onTrackEnded,
        onAudioError,
        (playing) => {
            state.isPlaying = playing;
            UI.updatePlayBtn(playing);
            // وضعیت پخش تغییر کرد -> Force Report برای آپدیت آنی ریموت
            reportStatus(true); 
        }
    );

    setupUIControls();
    setupNetworkRecovery();
    UI.updateControlButtons();

    if (state.sessionToken) {
        Network.validateSession({ onLogin: unlockPlayer, onQRReady: UI.showLoginQR });
    } else {
        Network.initAuth({ onLogin: unlockPlayer, onQRReady: UI.showLoginQR });
    }
});

function unlockPlayer(admin) {
    UI.unlockInterface(admin);
    syncTracks(false); 
    Network.initControlSSE({
        onQueueUpdate: () => syncTracks(),
        onCommand: processRemoteCommand
    });
}

// ==========================================
// 🎵 PLAYBACK LOGIC & SMART BUFFERING
// ==========================================

async function syncTracks(autoStart = false) {
    const newTracks = await Network.fetchQueue();
    state.tracks = newTracks || [];
    
    UI.renderPlaylist(state.tracks, loadTrack);
    
    if (state.tracks.length > 0) {
        if (state.tracks.length === newTracks.length && autoStart) loadTrack(0, true);
        if (state.isPlaying) preloadNextTrack();
    }
}

function loadTrack(index, autoPlay = true, startPos = 0) {
    if (!state.tracks[index]) return;
    
    const isSameTrack = state.currentIndex === index && engines.active.src.includes(state.tracks[index].file_unique_id);
    state.currentIndex = index;
    const track = state.tracks[index];
    
    UI.updatePlayerInfo(track);
    UI.updateActiveItem(index);
    
    // Media Session API for Hardware Control
    if ('mediaSession' in navigator) {
        navigator.mediaSession.metadata = new MediaMetadata({
            title: track.title,
            artist: track.performer,
            artwork: [{ src: `/cover/${track.file_unique_id}`, sizes: '512x512', type: 'image/jpeg' }]
        });
    }
    
    if(window.fetchLyrics) window.fetchLyrics(track.file_unique_id);

    // Dual Engine Logic
    if (!isSameTrack) {
        if (engines.buffer.src.includes(track.file_unique_id) && engines.buffer.readyState >= 3) {
            swapEngines();
            setupAudioListeners(onTimeUpdate, onTrackEnded, onAudioError, (p) => { 
                state.isPlaying = p; 
                UI.updatePlayBtn(p); 
                reportStatus(true);
            });
        } else {
            engines.active.src = `/stream/${track.file_unique_id}`;
            engines.active.load();
        }
    }

    if (startPos > 0) engines.active.currentTime = startPos;

    if (autoPlay) {
        engines.active.play().then(() => {
            state.isPlaying = true;
            UI.updatePlayBtn(true);
            preloadNextTrack(); // آغاز بافر هوشمند آهنگ بعدی
            reportStatus(true); 
        }).catch(console.error);
    }
}

// --- Smart Pre-fetching (Network Aware) ---
function preloadNextTrack() {
    const nextIdx = getNextIndex();
    
    if (nextIdx !== -1 && state.tracks[nextIdx]) {
        const nextTrack = state.tracks[nextIdx];
        const nextUrl = `/stream/${nextTrack.file_unique_id}`;
        
        // 1. بررسی وضعیت شبکه و پیش‌بارگذاری استتیک‌ها (کاور/لیریک)
        const shouldPreloadAudio = Network.preloadAssets(nextTrack.file_unique_id);
        
        // 2. اگر شبکه ضعیف است، بافر کردن فایل صوتی را متوقف کن تا آهنگ فعلی گیر نکند
        if (!shouldPreloadAudio) {
            console.warn("⚠️ [Adaptive Network] Audio preloading skipped to save bandwidth.");
            return;
        }

        // 3. اگر شبکه قوی است، فایل صوتی را در پس‌زمینه بافر کن
        if (!engines.buffer.src.includes(nextUrl)) {
            engines.buffer.src = nextUrl;
            engines.buffer.load(); 
        }
    }
}

// --- Queue Navigation ---
function getNextIndex() {
    if (state.tracks.length === 0) return 0;
    if (state.repeatMode === 'one') return state.currentIndex;

    if (state.shuffle) {
        let next;
        do { next = Math.floor(Math.random() * state.tracks.length); } 
        while (next === state.currentIndex && state.tracks.length > 1);
        return next;
    }

    let next = state.currentIndex + 1;
    return next >= state.tracks.length ? (state.repeatMode === 'off' ? -1 : 0) : next;
}

function nextTrack() {
    if (state.tracks[state.currentIndex]) {
        Network.markAsPlayed(state.tracks[state.currentIndex].file_unique_id);
    }
    const nextIdx = getNextIndex();
    if (nextIdx === -1) {
        state.isPlaying = false;
        UI.updatePlayBtn(false);
        engines.active.pause();
        reportStatus(true);
        return;
    }
    loadTrack(nextIdx);
}

// ==========================================
// 🎮 CONTROLS & COMMANDS
// ==========================================

function togglePlay() {
    if (state.tracks.length === 0) return;
    state.isPlaying ? engines.active.pause() : engines.active.play();
}

function seekToTime(seconds) {
    if (isFinite(seconds) && engines.active.duration) {
        engines.active.currentTime = seconds;
        reportStatus(true); // Force update to sync remotes instantly
    }
}

function processRemoteCommand(cmd) {
    switch(cmd.action) {
        case 'play': if(!state.isPlaying) togglePlay(); break;
        case 'pause': if(state.isPlaying) togglePlay(); break;
        case 'toggle': togglePlay(); break;
        case 'next': nextTrack(); break;
        case 'prev': loadTrack((state.currentIndex - 1 + state.tracks.length) % state.tracks.length); break;
        case 'seek': seekToTime(cmd.payload); break;
        case 'volume': engines.active.volume = cmd.payload / 100; break;
        case 'jump': 
            const idx = state.tracks.findIndex(t => t.file_unique_id === cmd.payload);
            if(idx !== -1) loadTrack(idx);
            break;
    }
}

// ==========================================
// 📊 REPORTING & SSE OPTIMIZATION
// ==========================================

function onTimeUpdate() {
    UI.updateProgress(engines.active.currentTime, engines.active.duration);
    if(window.syncLyrics) window.syncLyrics(engines.active.currentTime);
    
    // 🔥 Throttling Logic: ارسال وضعیت فقط هر 3 ثانیه یک‌بار
    const currentSec = Math.floor(engines.active.currentTime);
    const now = Date.now();
    
    if (currentSec !== lastReportedSecond && currentSec % 3 === 0 && (now - lastReportTimestamp > 2000)) {
        reportStatus();
        lastReportedSecond = currentSec;
        lastReportTimestamp = now;
    }
}

/**
 * ارسال وضعیت به سرور برای Sync کردن ریموت‌ها
 * @param {boolean} force - اگر true باشد، محدودیت‌های زمانی نادیده گرفته می‌شود
 */
function reportStatus(force = false) {
    if(!state.tracks[state.currentIndex]) return;
    
    const currentSec = Math.floor(engines.active.currentTime);
    const now = Date.now();
    
    // در حالت عادی، از ارسال‌های تکراری در یک ثانیه جلوگیری کن
    if (!force && currentSec === lastReportedSecond) return;

    Network.reportStatus(
        state.tracks[state.currentIndex].file_unique_id,
        state.isPlaying,
        engines.active.currentTime,
        engines.active.duration
    );
    
    if (!force) {
        lastReportedSecond = currentSec;
        lastReportTimestamp = now;
    }
}

// ==========================================
// 🛡️ ERROR RECOVERY & LISTENERS
// ==========================================

function onTrackEnded() {
    state.retryCount = 0;
    nextTrack();
}

function onAudioError() {
    if (state.retryCount < CONFIG.retryLimit && navigator.onLine) {
        state.retryCount++;
        setTimeout(() => {
            const t = engines.active.currentTime;
            engines.active.load();
            engines.active.currentTime = t;
            engines.active.play();
        }, 1000);
    } else {
        nextTrack();
    }
}

function setupUIControls() {
    UI.elements.slider.addEventListener('input', (e) => {
        state.isDragging = true;
        const val = e.target.value;
        UI.elements.progressFill.style.width = `${val}%`;
        UI.elements.thumb.style.left = `${val}%`;
    });
    
    UI.elements.slider.addEventListener('change', (e) => {
        state.isDragging = false;
        seekToTime((e.target.value / 100) * engines.active.duration);
    });

    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT') return;
        if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
        if (e.key === 'ArrowRight') seekToTime(engines.active.currentTime + 5);
        if (e.key === 'ArrowLeft') seekToTime(engines.active.currentTime - 5);
    });
}

function setupNetworkRecovery() {
    window.addEventListener('online', () => {
        Network.initControlSSE({
            onQueueUpdate: () => syncTracks(),
            onCommand: processRemoteCommand
        });
        syncTracks();
    });
}

// Global Exports
window.playPause = togglePlay;
window.nextTrack = nextTrack;
window.prevTrack = () => loadTrack((state.currentIndex - 1 + state.tracks.length) % state.tracks.length);
window.toggleShuffle = () => { state.shuffle = !state.shuffle; UI.updateControlButtons(); };
window.toggleRepeat = () => {
    const modes = ['off', 'all', 'one'];
    state.repeatMode = modes[(modes.indexOf(state.repeatMode) + 1) % modes.length];
    UI.updateControlButtons();
};
window.seekToTime = seekToTime;