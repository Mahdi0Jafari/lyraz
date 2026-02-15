/**
 * Fanus Player - Main Controller (Modular v10)
 */
import { state, CONFIG } from './modules/state.js';
import { engines, swapEngines, setupAudioListeners } from './modules/audio.js';
import * as UI from './modules/ui.js';
import * as Network from './modules/network.js';

// ==========================================
// 🚀 INITIALIZATION
// ==========================================

document.addEventListener('DOMContentLoaded', () => {
    console.log("🚀 Fanus Player Modular Initializing...");
    
    // اتصال لیسنرهای صوتی به لاجیک کنترلر
    setupAudioListeners(
        onTimeUpdate,
        onTrackEnded,
        onAudioError,
        (playing) => {
            state.isPlaying = playing;
            UI.updatePlayBtn(playing);
        }
    );

    setupUIControls();
    setupNetworkRecovery();
    UI.updateControlButtons();

    // شروع پروسه احراز هویت
    if (state.sessionToken) {
        Network.validateSession({ onLogin: unlockPlayer, onQRReady: UI.showLoginQR });
    } else {
        Network.initAuth({ onLogin: unlockPlayer, onQRReady: UI.showLoginQR });
    }
});

function unlockPlayer(admin) {
    UI.unlockInterface(admin);
    // syncTracks(true); // true = autoPlay if queue was empty
    syncTracks(false); // avoid play() failed because the user didn't interact with the document first error
    Network.initControlSSE({
        onQueueUpdate: () => syncTracks(),
        onCommand: processRemoteCommand
    });
}

// ==========================================
// 🎵 PLAYBACK LOGIC
// ==========================================

async function syncTracks(autoStart = false) {
    const newTracks = await Network.fetchQueue();
    if (!newTracks.length) return;
    
    const wasEmpty = state.tracks.length === 0;
    state.tracks = newTracks;
    
    UI.renderPlaylist(state.tracks, loadTrack);
    
    if (wasEmpty && autoStart) loadTrack(0, true);
    if (state.isPlaying) preloadNextTrack();
}

function loadTrack(index, autoPlay = true, startPos = 0) {
    if (!state.tracks[index]) return;
    
    const isSameTrack = state.currentIndex === index && engines.active.src.includes(state.tracks[index].file_unique_id);
    state.currentIndex = index;
    const track = state.tracks[index];
    
    UI.updatePlayerInfo(track);
    UI.updateActiveItem(index);
    
    // Media Session API
    if ('mediaSession' in navigator) {
        navigator.mediaSession.metadata = new MediaMetadata({
            title: track.title,
            artist: track.performer,
            artwork: [{ src: `/cover/${track.file_unique_id}`, sizes: '512x512', type: 'image/jpeg' }]
        });
    }
    
    // Lyrics
    if(window.fetchLyrics) window.fetchLyrics(track.file_unique_id);

    // Dual Engine Logic
    if (!isSameTrack) {
        if (engines.buffer.src.includes(track.file_unique_id) && engines.buffer.readyState >= 3) {
            console.log("⚡ Buffer Swap");
            swapEngines();
            // Re-bind listeners after swap
            setupAudioListeners(onTimeUpdate, onTrackEnded, onAudioError, (p) => { state.isPlaying = p; UI.updatePlayBtn(p); });
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
            preloadNextTrack();
        }).catch(console.error);
    }
    
    reportStatus();
}

// --- Next / Prev / Shuffle Logic ---

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
    if (next >= state.tracks.length) {
        return state.repeatMode === 'off' ? -1 : 0;
    }
    return next;
}

function nextTrack() {
    Network.markAsPlayed(state.tracks[state.currentIndex]?.file_unique_id);
    const nextIdx = getNextIndex();
    
    if (nextIdx === -1) {
        state.isPlaying = false;
        UI.updatePlayBtn(false);
        engines.active.pause();
        return;
    }
    loadTrack(nextIdx);
}

function prevTrack() {
    if (engines.active.currentTime > 3) {
        engines.active.currentTime = 0;
        return;
    }
    loadTrack((state.currentIndex - 1 + state.tracks.length) % state.tracks.length);
}

function preloadNextTrack() {
    const nextIdx = getNextIndex();
    if (nextIdx !== -1 && state.tracks[nextIdx]) {
        const nextUrl = `/stream/${state.tracks[nextIdx].file_unique_id}`;
        if (!engines.buffer.src.includes(nextUrl)) {
            engines.buffer.src = nextUrl;
            engines.buffer.load();
        }
    }
}

// ==========================================
// 🎮 CONTROLS & EVENTS
// ==========================================

function togglePlay() {
    state.isPlaying ? engines.active.pause() : engines.active.play();
}

function toggleShuffle() {
    state.shuffle = !state.shuffle;
    UI.updateControlButtons();
    if (state.shuffle) preloadNextTrack();
}

function toggleRepeat() {
    if (state.repeatMode === 'off') state.repeatMode = 'all';
    else if (state.repeatMode === 'all') state.repeatMode = 'one';
    else state.repeatMode = 'off';
    UI.updateControlButtons();
}

function processRemoteCommand(cmd) {
    switch(cmd.action) {
        case 'play': if(!state.isPlaying) togglePlay(); break;
        case 'pause': if(state.isPlaying) togglePlay(); break;
        case 'toggle': togglePlay(); break;
        case 'next': nextTrack(); break;
        case 'prev': prevTrack(); break;
        case 'seek': if(isFinite(cmd.payload)) engines.active.currentTime = cmd.payload; break;
        case 'volume': engines.active.volume = cmd.payload / 100; break;
        case 'jump': 
            const idx = state.tracks.findIndex(t => t.file_unique_id === cmd.payload);
            if(idx !== -1) loadTrack(idx);
            break;
    }
}

// --- Listeners ---

function onTimeUpdate() {
    UI.updateProgress(engines.active.currentTime, engines.active.duration);
    if(window.syncLyrics) window.syncLyrics(engines.active.currentTime);
    if (Math.floor(engines.active.currentTime) % 2 === 0) reportStatus();
}

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

function reportStatus() {
    if(!state.tracks[state.currentIndex]) return;
    Network.reportStatus(
        state.tracks[state.currentIndex].file_unique_id,
        state.isPlaying,
        engines.active.currentTime,
        engines.active.duration
    );
}

function setupUIControls() {
    // Slider
    UI.elements.slider.addEventListener('input', (e) => {
        state.isDragging = true;
        // UI update live handled by CSS usually, but we can force it
        // Note: Logic moved here to keep UI module dumb
        const val = e.target.value;
        UI.elements.progressFill.style.width = `${val}%`;
        UI.elements.thumb.style.left = `${val}%`;
    });
    
    UI.elements.slider.addEventListener('change', (e) => {
        state.isDragging = false;
        engines.active.currentTime = (e.target.value / 100) * engines.active.duration;
    });

    // Keyboard
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT') return;
        if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
        if (e.key === 'ArrowRight') engines.active.currentTime += 5;
        if (e.key === 'ArrowLeft') engines.active.currentTime -= 5;
    });
}

function setupNetworkRecovery() {
    window.addEventListener('offline', () => {
        state.recovery.wasPlaying = !engines.active.paused;
        state.recovery.time = engines.active.currentTime;
        if(state.tracks[state.currentIndex]) state.recovery.trackId = state.tracks[state.currentIndex].file_unique_id;
    });
    
    window.addEventListener('online', () => {
        Network.initControlSSE({
            onQueueUpdate: () => syncTracks(),
            onCommand: processRemoteCommand
        });
        syncTracks().then(() => {
            if (state.recovery.trackId) {
                const idx = state.tracks.findIndex(t => t.file_unique_id === state.recovery.trackId);
                if (idx !== -1) loadTrack(idx, state.recovery.wasPlaying, state.recovery.time);
            }
        });
    });
}

// Global Exports (For HTML Buttons)
window.playPause = togglePlay;
window.nextTrack = nextTrack;
window.prevTrack = prevTrack;
window.toggleShuffle = toggleShuffle;
window.toggleRepeat = toggleRepeat;
