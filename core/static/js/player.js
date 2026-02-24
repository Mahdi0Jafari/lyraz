/**
 * Fanus Player - Main Controller (Live Hubs V4.1)
 * Features: PTP Sync, Idempotent Execution, Dual Engine
 */
import { state, CONFIG } from './modules/state.js';
import { engines, swapEngines, setupAudioListeners } from './modules/audio.js';
import * as UI from './modules/ui.js';
import * as Network from './modules/network.js';

let lastReportedSecond = -1;
let lastReportTimestamp = 0;
// تاریخچه اکشن‌های اجرا شده برای جلوگیری از اجرای تکراری
const executedActions = new Set(); 

// ==========================================
// 🚀 INITIALIZATION (V4: State-Driven)
// ==========================================

document.addEventListener('DOMContentLoaded', async () => {
    console.log("🚀 Fanus Live Hub Initializing...");
    
    setupAudioListeners(
        onTimeUpdate,
        onTrackEnded,
        onAudioError,
        (playing) => {
            state.isPlaying = playing;
            UI.updatePlayBtn(playing);
            // فقط زمانی ریپورت کن که سیستم در حال سینک خودکار نباشد
            if (!state.isSyncing) reportStatus(true); 
        }
    );

    setupUIControls();
    setupNetworkRecovery();
    UI.updateControlButtons();

    if (state.sessionToken) {
        if (state.hubStatus === 'active') {
            await recoverHubState();
        } else {
            Network.validateSession({ onLogin: unlockPlayer, onQRReady: UI.showLoginQR });
        }
    } else {
        Network.initAuth({ onLogin: unlockPlayer, onQRReady: UI.showLoginQR });
    }
});

async function recoverHubState() {
    console.log("🔄 Recovering Live Hub State...");
    UI.unlockInterface({ name: 'Hub', device_display_name: 'Live Sync' }); 
    
    const liveState = await Network.fetchHubState();
    await syncTracks(false);
    
    Network.initControlSSE({
        onQueueUpdate: () => syncTracks(),
        onCommand: processRemoteCommand
    });

    if (liveState && liveState.status === 'active' && liveState.file_unique_id) {
        const idx = state.tracks.findIndex(t => t.file_unique_id === liveState.file_unique_id);
        
        if (idx !== -1) {
            console.log(`⏱ Syncing to track index ${idx} at second ${liveState.seek_position}`);
            state.isSyncing = true; 
            loadTrack(idx, liveState.is_playing, liveState.seek_position);
            setTimeout(() => { state.isSyncing = false; }, 1000);
        }
    } else {
        if (state.tracks.length > 0) loadTrack(0, false);
    }
}

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
    
    if ('mediaSession' in navigator) {
        navigator.mediaSession.metadata = new MediaMetadata({
            title: track.title,
            artist: track.performer,
            artwork: [{ src: `/cover/${track.file_unique_id}`, sizes: '512x512', type: 'image/jpeg' }]
        });
    }
    
    if(window.fetchLyrics) window.fetchLyrics(track.file_unique_id);

    if (!isSameTrack) {
        if (engines.buffer.src.includes(track.file_unique_id) && engines.buffer.readyState >= 3) {
            swapEngines();
            setupAudioListeners(onTimeUpdate, onTrackEnded, onAudioError, (p) => { 
                state.isPlaying = p; 
                UI.updatePlayBtn(p); 
                if(!state.isSyncing) reportStatus(true);
            });
        } else {
            engines.active.src = `/stream/${track.file_unique_id}`;
            engines.active.load();
        }
    }

    if (startPos > 0) {
        engines.active.currentTime = startPos;
    }

    if (autoPlay) {
        const playPromise = engines.active.play();
        if (playPromise !== undefined) {
            playPromise.then(() => {
                state.isPlaying = true;
                UI.updatePlayBtn(true);
                preloadNextTrack();
                if(!state.isSyncing) reportStatus(true); 
            }).catch(e => console.warn("Auto-play prevented by browser:", e));
        }
    }
}

function preloadNextTrack() {
    const nextIdx = getNextIndex();
    
    if (nextIdx !== -1 && state.tracks[nextIdx]) {
        const nextTrack = state.tracks[nextIdx];
        const nextUrl = `/stream/${nextTrack.file_unique_id}`;
        const shouldPreloadAudio = Network.preloadAssets(nextTrack.file_unique_id);
        
        if (!shouldPreloadAudio) return;
        if (!engines.buffer.src.includes(nextUrl)) {
            engines.buffer.src = nextUrl;
            engines.buffer.load(); 
        }
    }
}

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
// 🎮 CONTROLS, COMMANDS & NTP SYNC
// ==========================================

function togglePlay() {
    if (state.tracks.length === 0) return;
    state.isPlaying ? engines.active.pause() : engines.active.play();
}

function seekToTime(seconds) {
    if (isFinite(seconds) && engines.active.duration) {
        engines.active.currentTime = seconds;
        if(!state.isSyncing) reportStatus(true);
    }
}

function processRemoteCommand(cmd) {
    // 🔥 Idempotency: جلوگیری از اجرای فرمان تکراری (مثل دابل Next)
    if (cmd.action_id) {
        if (executedActions.has(cmd.action_id)) {
            console.log("Skipping duplicate action:", cmd.action_id);
            return;
        }
        executedActions.add(cmd.action_id);
        // تمیز کردن تاریخچه برای جلوگیری از نشت حافظه
        if (executedActions.size > 50) {
            const firstItem = executedActions.values().next().value;
            executedActions.delete(firstItem);
        }
    }

    // 🔥 Time-Shifted Execution (NTP Sync)
    if (cmd.scheduled_at) {
        const localNow = Date.now() / 1000;
        // ساعت واقعی سرور با احتساب آفست کالیبره شده
        const synchronizedNow = localNow + state.serverTimeOffset; 
        
        // چقدر باید صبر کنیم تا به زمان مقرر برسیم؟ (به میلی‌ثانیه)
        const waitTimeMs = (cmd.scheduled_at - synchronizedNow) * 1000;
        
        // اگر زمان گذشته بود یا خیلی نزدیک بود، بلافاصله اجرا کن
        const delay = Math.max(0, waitTimeMs);
        
        console.log(`Command ${cmd.action} scheduled in ${delay}ms`);
        setTimeout(() => executeCommand(cmd), delay);
    } else {
        // Fallback برای دستورات بدون زمان‌بندی
        executeCommand(cmd);
    }
}

function executeCommand(cmd) {
    state.isSyncing = true; // مسدود کردن ریپورت به سرور حین اجرای ریموت
    
    let targetTime = cmd.payload;
    if (cmd.action === 'seek' && cmd.sync_timestamp) {
         // جبران تاخیری که ممکن است از لحظه ارسال تا زمان مقرر اتفاق افتاده باشد
        const latency = (Date.now() / 1000) - cmd.sync_timestamp + state.serverTimeOffset;
        if (latency > 0 && latency < 5 && state.isPlaying) {
             targetTime += latency;
        }
    }

    switch(cmd.action) {
        case 'play': if(!state.isPlaying) togglePlay(); break;
        case 'pause': if(state.isPlaying) togglePlay(); break;
        case 'toggle': togglePlay(); break;
        case 'next': nextTrack(); break;
        case 'prev': loadTrack((state.currentIndex - 1 + state.tracks.length) % state.tracks.length); break;
        case 'seek': seekToTime(targetTime); break;
        case 'volume': engines.active.volume = cmd.payload / 100; break;
        case 'jump': 
            const idx = state.tracks.findIndex(t => t.file_unique_id === cmd.payload);
            if(idx !== -1) loadTrack(idx);
            break;
    }
    
    setTimeout(() => { state.isSyncing = false; }, 500);
}

// ==========================================
// 📊 REPORTING
// ==========================================

function onTimeUpdate() {
    UI.updateProgress(engines.active.currentTime, engines.active.duration);
    if(window.syncLyrics) window.syncLyrics(engines.active.currentTime);
    
    const currentSec = Math.floor(engines.active.currentTime);
    const now = Date.now();
    
    if (currentSec !== lastReportedSecond && currentSec % 5 === 0 && (now - lastReportTimestamp > 4000)) {
        reportStatus();
        lastReportedSecond = currentSec;
        lastReportTimestamp = now;
    }
}

function reportStatus(force = false) {
    if(!state.tracks[state.currentIndex] || state.isSyncing) return;
    
    const currentSec = Math.floor(engines.active.currentTime);
    const now = Date.now();
    
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
// 🛡️ RECOVERY
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
            engines.active.play().catch(e => console.warn("Recovery play blocked", e));
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
        recoverHubState(); 
    });
}

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