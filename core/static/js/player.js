/**
 * Lyraz Player - Main Controller (Live Hubs V4.4)
 * Features: True PTP Sync (Auto-Correction), Idempotent Execution, Dual Engine
 */
import { state, CONFIG } from './modules/state.js?v=4.8';
import { 
    engines, swapEngines, setupAudioListeners, 
    crossfadeEngines, getBufferedAhead, getAudioContext,
    primeAudioEngines, configureAudioSession
} from './modules/audio.js?v=4.8';
import * as UI from './modules/ui.js?v=4.8';
import * as Network from './modules/network.js?v=4.8';

let lastReportedSecond = -1;
let lastReportTimestamp = 0;
// History of executed actions to prevent duplicate execution (Idempotency)
const executedActions = new Set(); 

// Critical variables for network delay compensation
let pendingSyncCommand = null;
let hasUserJoined = false;

// ==========================================
// 🚀 INITIALIZATION (V4: State-Driven)
// ==========================================

document.addEventListener('DOMContentLoaded', async () => {
    console.log("🚀 Lyraz Live Hub Initializing...");
    
    setupAudioListeners(
        onTimeUpdate,
        onTrackEnded,
        onAudioError,
        (playing) => {
            state.isPlaying = playing;
            UI.updatePlayBtn(playing);
            
            // 🔥 True PTP Sync: calibrate time immediately upon playback start
            if (playing && pendingSyncCommand) {
                applyPreciseSync(pendingSyncCommand);
                pendingSyncCommand = null;
            }
            
            // Only report when system is not currently in auto-sync
            if (!state.isSyncing) reportStatus(true); 
        }
    );

    setupUIControls();
    setupNetworkRecovery();
    setupMediaSession();
    UI.updateControlButtons();

    if (state.sessionToken) {
        if (state.hubStatus === 'active' || window.location.pathname.startsWith('/live/')) {
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
    UI.unlockInterface({ name: window.HUB_NAME || 'Live Hub', device_display_name: 'Live Sync' }); 
    
    const liveState = await Network.fetchHubState();
    await syncTracks(false);
    
    Network.initControlSSE({
        onQueueUpdate: () => syncTracks(),
        onCommand: processRemoteCommand,
        onStatusUpdate: handleLiveStatusUpdate
    });

    // Client nodes joining a hub session
    // (Master host player is the source of truth, not a listener)

    if (liveState && liveState.status === 'active' && liveState.file_unique_id) {
        const idx = state.tracks.findIndex(t => t.file_unique_id === liveState.file_unique_id);
        const track = idx !== -1 ? state.tracks[idx] : null;

        if (idx !== -1) {
            state.currentIndex = idx;
            if (track) UI.updatePlayerInfo(track);
        }

        // Show Join Live Hub gateway modal on every device / screen visiting /live/<token>
        if (!hasUserJoined) {
            showJoinModal(track, liveState);
            return;
        }

        if (idx !== -1) {
            console.log(`⏱ Syncing to track index ${idx} at second ${liveState.seek_position}`);
            state.isSyncing = true; 
            
            // Simulate sync command for recovery
            pendingSyncCommand = {
                base_seek: liveState.seek_position,
                server_now: liveState.server_time
            };
            
            loadTrack(idx, liveState.is_playing, liveState.seek_position);
            setTimeout(() => { state.isSyncing = false; }, 1000);
        }
    } else {
        if (state.tracks.length > 0) {
            loadTrack(0, false);
            UI.updatePlayerInfo(state.tracks[0]);
        }
        if (!hasUserJoined) {
            showJoinModal(state.tracks[0] || null, liveState);
        }
    }
}

function unlockPlayer(admin) {
    UI.unlockInterface(admin);
    syncTracks(false); 
    Network.initControlSSE({
        onQueueUpdate: () => syncTracks(),
        onCommand: processRemoteCommand,
        onStatusUpdate: handleLiveStatusUpdate
    });
}

// ==========================================
// 🎵 PLAYBACK LOGIC & SMART BUFFERING
// ==========================================

async function syncTracks(autoStart = false) {
    const prevCount = state.tracks.length;
    const newTracks = await Network.fetchQueue();
    state.tracks = newTracks || [];
    
    UI.renderPlaylist(state.tracks, loadTrack);
    
    if (state.tracks.length > 0) {
        // 🔥 If queue was previously empty, start playing the first added track!
        if ((prevCount === 0 || autoStart) && !state.isPlaying) {
            loadTrack(0, true);
        }
        if (state.isPlaying) preloadNextTrack();
    }
}

async function loadTrack(index, autoPlay = true, startPos = 0) {
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
        if (engines.buffer.src.includes(track.file_unique_id) && engines.buffer.readyState >= 2) {
            await crossfadeEngines(0.35);
            setupAudioListeners(onTimeUpdate, onTrackEnded, onAudioError, (p) => { 
                state.isPlaying = p; 
                UI.updatePlayBtn(p);
                
                if (p && pendingSyncCommand) {
                    applyPreciseSync(pendingSyncCommand);
                    pendingSyncCommand = null;
                }
                if(!state.isSyncing) reportStatus(true);
            });
        } else {
            engines.active.src = `/stream/${track.file_unique_id}`;
            engines.active.load();
        }
    }

    if (startPos > 0) {
        if (engines.active.readyState >= 1) {
            engines.active.currentTime = startPos;
        } else {
            const onLoadedMeta = () => {
                engines.active.currentTime = startPos;
                engines.active.removeEventListener('loadedmetadata', onLoadedMeta);
            };
            engines.active.addEventListener('loadedmetadata', onLoadedMeta, { once: true });
        }
    }

    if (autoPlay && (!engines.active.src.includes(track.file_unique_id) || engines.active.paused)) {
        const playPromise = engines.active.play();
        if (playPromise !== undefined) {
            playPromise.then(() => {
                state.isPlaying = true;
                UI.updatePlayBtn(true);
                preloadNextTrack();
                requestWakeLock();
                if(!state.isSyncing) reportStatus(true); 
            }).catch(e => {
                console.warn("Auto-play prevented by browser:", e);
                if (!hasUserJoined) {
                    showJoinModal(track, { is_playing: true });
                }
            });
        }
    } else if (state.isPlaying) {
        preloadNextTrack();
        if(!state.isSyncing) reportStatus(true);
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

// ==========================================
// 🛡️ ACCURATE PLAYBACK STATE CONTROLLERS
// ==========================================
function handleRemotePause() {
    state.isPlaying = false;
    UI.updatePlayBtn(false);
    if (driftTimer) clearTimeout(driftTimer);
    try { engines.active.playbackRate = 1.0; } catch(e) {}
    
    // Stop audio immediately
    engines.active.pause();
    engines.buffer.pause();
    
    // Ensure gain and volume are normalized
    if (engines.active._gainNode) {
        engines.active._gainNode.gain.value = 1.0;
    }
    engines.active.volume = 1.0;

    if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'paused';
    }

    pendingSyncCommand = null;
    reportStatus(true);
}

function handleRemoteResume() {
    state.isPlaying = true;
    UI.updatePlayBtn(true);
    requestWakeLock();

    if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'playing';
    }

    // Ensure gain and volume are 1.0
    if (engines.active._gainNode) {
        engines.active._gainNode.gain.value = 1.0;
    }
    engines.active.volume = 1.0;

    const ctx = getAudioContext();
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
    
    engines.active.play().then(() => {
        if (pendingSyncCommand) {
            applyPreciseSync(pendingSyncCommand);
            pendingSyncCommand = null;
        }
        reportStatus(true);
    }).catch(e => {
        console.warn("Play blocked:", e);
        if (!hasUserJoined) {
            showJoinModal(state.tracks[state.currentIndex], { is_playing: true });
        }
    });
}

function togglePlay() {
    if (state.tracks.length === 0) return;
    if (state.isPlaying) {
        handleRemotePause();
    } else {
        handleRemoteResume();
    }
}

function seekToTime(seconds) {
    if (isFinite(seconds) && engines.active.duration) {
        state.isSyncing = true;
        engines.active.currentTime = seconds;
        
        const onLocalSeeked = () => {
            engines.active.removeEventListener('seeked', onLocalSeeked);
            setTimeout(() => {
                state.isSyncing = false;
                reportStatus(true);
            }, 120);
        };
        engines.active.addEventListener('seeked', onLocalSeeked, { once: true });
    }
}

function processRemoteCommand(cmd) {
    // Prevent duplicate action execution (Idempotency)
    if (cmd.action_id) {
        if (executedActions.has(cmd.action_id)) return;
        executedActions.add(cmd.action_id);
        if (executedActions.size > 50) {
            const firstItem = executedActions.values().next().value;
            executedActions.delete(firstItem);
        }
    }

    // Temporary sync state storage for post-playback latency compensation
    // Only queue sync compensation for explicit time seek, never for simple resume
    if (cmd.action === 'seek') {
        pendingSyncCommand = cmd;
    }

    executeCommand(cmd);
}

function executeCommand(cmd) {
    state.isSyncing = true; // Block status reporting until sync complete
    
    switch(cmd.action) {
        case 'play': 
            handleRemoteResume();
            break;
        case 'pause': 
            handleRemotePause();
            break;
        case 'toggle': 
            if (state.isPlaying) {
                handleRemotePause();
            } else {
                handleRemoteResume();
            }
            break;
        case 'next': 
            nextTrack(); 
            break;
        case 'prev': 
            loadTrack((state.currentIndex - 1 + state.tracks.length) % state.tracks.length); 
            break;
        case 'seek': 
            const targetSec = Number(cmd.payload);
            if (isFinite(targetSec)) {
                state.isSyncing = true;
                engines.active.currentTime = targetSec;
                
                const onRemoteSeeked = () => {
                    engines.active.removeEventListener('seeked', onRemoteSeeked);
                    setTimeout(() => {
                        state.isSyncing = false;
                    }, 120);
                };
                engines.active.addEventListener('seeked', onRemoteSeeked, { once: true });
            }
            break;
        case 'volume': 
            engines.active.volume = cmd.payload / 100; 
            break;
        case 'jump': 
            const idx = state.tracks.findIndex(t => t.file_unique_id === cmd.payload);
            if(idx !== -1) loadTrack(idx);
            break;
        case 'remove':
            syncTracks(false);
            break;
    }
    
    setTimeout(() => { state.isSyncing = false; }, 500);
}

// 🔥 Studio Precision Sync (True NTP + Sub-pitch Drift Correction)
let driftTimer = null;

function applyPreciseSync(cmdData) {
    if (!cmdData || !cmdData.server_now || cmdData.base_seek === undefined) return;
    
    const localNow = Date.now() / 1000;
    // Calculate elapsed time from command issuance using NTP-calibrated clock
    const timePassedSinceCommand = localNow - cmdData.server_now + (state.serverTimeOffset || 0);
    
    if (timePassedSinceCommand >= 0 && timePassedSinceCommand < 15) {
        const idealTime = cmdData.base_seek + timePassedSinceCommand;
        const diff = idealTime - engines.active.currentTime;
        const absDiff = Math.abs(diff);

        // 1. Minor drift (30ms - 150ms): subtle playback rate nudging (Spotify Jam style)
        if (absDiff > 0.03 && absDiff <= 0.15) {
            if (driftTimer) clearTimeout(driftTimer);
            engines.active.playbackRate = diff > 0 ? 1.025 : 0.975;
            driftTimer = setTimeout(() => {
                try { engines.active.playbackRate = 1.0; } catch(e) {}
            }, 1200);
        }
        // 2. Significant drift (> 150ms): precise time seek
        else if (absDiff > 0.15) {
            console.log(`⏱ [NTP PTP Sync] Realigning head: Current ${engines.active.currentTime.toFixed(2)}s -> Target ${idealTime.toFixed(2)}s`);
            engines.active.currentTime = idealTime;
            try { engines.active.playbackRate = 1.0; } catch(e) {}
        }
    }
}

// ==========================================
// 🎯 SPEED-TO-SYNC: CONTINUOUS DRIFT CORRECTION
// ==========================================

function handleLiveStatusUpdate(data) {
    const payload = data.payload;
    if (!payload || payload.client_id === state.clientId) return;
    if (!state.isPlaying || state.isSyncing || !payload.is_playing) return;

    
    const currentTrack = state.tracks[state.currentIndex];
    if (!currentTrack || currentTrack.file_unique_id !== payload.file_unique_id) return;
    
    const localNow = Date.now() / 1000;
    const serverNow = data.server_now || (localNow + (state.serverTimeOffset || 0));
    const elapsed = Math.max(0, (localNow + (state.serverTimeOffset || 0)) - serverNow);
    const targetHostTime = payload.current_time + elapsed;
    
    const drift = targetHostTime - engines.active.currentTime;
    const absDrift = Math.abs(drift);
    
    // 1. Under 50ms: Imperceptible to human ear
    if (absDrift <= 0.05) return;
    
    // 2. Micro-drift (50ms - 1.5s): Speed-to-Sync (seamless ±2% playback rate nudge - ZERO audio cutout)
    if (absDrift <= 1.5) {
        if (driftTimer) clearTimeout(driftTimer);
        engines.active.playbackRate = drift > 0 ? 1.02 : 0.98;
        driftTimer = setTimeout(() => {
            try { engines.active.playbackRate = 1.0; } catch(e) {}
        }, 1500);
    } 
    // 3. Significant desync (> 3.0s): Seek-to-Sync (hard realign only for huge lag)
    else if (absDrift > 3.0 && absDrift < 30) {
        console.log(`⏱ [Periodic Sync] Large drift realignment: ${engines.active.currentTime.toFixed(2)}s -> ${targetHostTime.toFixed(2)}s (diff: ${(drift * 1000).toFixed(0)}ms)`);
        engines.active.currentTime = targetHostTime;
        try { engines.active.playbackRate = 1.0; } catch(e) {}
    }
}

// ==========================================
// 📊 REPORTING
// ==========================================

function onTimeUpdate() {
    UI.updateProgress(engines.active.currentTime, engines.active.duration);
    if(window.syncLyrics) window.syncLyrics(engines.active.currentTime);
    
    const currentSec = Math.floor(engines.active.currentTime);
    const now = Date.now();
    
    // Report status to server every 5 seconds
    if (currentSec !== lastReportedSecond && currentSec % 5 === 0 && (now - lastReportTimestamp > 4000)) {
        reportStatus();
        lastReportedSecond = currentSec;
        lastReportTimestamp = now;
    }
}

function reportStatus(force = false) {
    if(!state.tracks[state.currentIndex] || state.isSyncing) return;
    
    if (!state.isPlaying && !force) return;

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
    // If playback is paused, do NOT advance to next track
    if (!state.isPlaying) return;

    const dur = engines.active.duration;
    const cur = engines.active.currentTime;
    
    // 🔥 True track end verification: check if track length reached before declaring ended
    if (dur && isFinite(dur) && dur > 10 && cur < dur - 4) {
        console.warn(`[Playback] Premature stream disconnect at ${cur.toFixed(1)}s / ${dur.toFixed(1)}s. Auto-resuming...`);
        const resumePos = cur;
        engines.active.load();
        engines.active.currentTime = resumePos;
        engines.active.play().catch(e => console.warn("Stream auto-resume blocked:", e));
        return;
    }

    state.retryCount = 0;
    nextTrack();
}

function onAudioError(e) {
    if (!engines.active.src || engines.active.src === window.location.href) {
        return;
    }
    console.warn("[Playback] Audio error event:", e);
    
    if (!state.isPlaying) return;

    if (state.retryCount < CONFIG.retryLimit && navigator.onLine) {
        state.retryCount++;
        setTimeout(() => {
            const t = engines.active.currentTime || 0;
            engines.active.load();
            if (t > 0) engines.active.currentTime = t;
            engines.active.play().catch(err => console.warn("Recovery play blocked:", err));
        }, 1200);
    } else {
        state.retryCount = 0;
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

let wakeLock = null;
async function requestWakeLock() {
    try {
        if ('wakeLock' in navigator && !wakeLock) {
            wakeLock = await navigator.wakeLock.request('screen');
            wakeLock.addEventListener('release', () => { wakeLock = null; });
        }
    } catch (e) {}
}

function setupMediaSession() {
    if (!('mediaSession' in navigator)) return;
    try {
        navigator.mediaSession.setActionHandler('play', () => togglePlay());
        navigator.mediaSession.setActionHandler('pause', () => togglePlay());
        navigator.mediaSession.setActionHandler('previoustrack', () => {
            loadTrack((state.currentIndex - 1 + state.tracks.length) % state.tracks.length);
        });
        navigator.mediaSession.setActionHandler('nexttrack', () => nextTrack());
        navigator.mediaSession.setActionHandler('seekto', (details) => {
            if (details.seekTime !== undefined) seekToTime(details.seekTime);
        });
    } catch (e) {
        console.warn("[MediaSession] Error setting handlers:", e);
    }
}

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        requestWakeLock();
        if (state.sessionToken) {
            Network.fetchHubState().then(liveState => {
                if (liveState && liveState.status === 'active' && liveState.file_unique_id) {
                    const idx = state.tracks.findIndex(t => t.file_unique_id === liveState.file_unique_id);
                    if (idx !== -1) {
                        if (state.currentIndex !== idx) {
                            loadTrack(idx, liveState.is_playing, liveState.seek_position);
                        } else if (!state.isPlaying && liveState.is_playing) {
                            loadTrack(idx, true, liveState.seek_position);
                        } else if (state.isPlaying && !liveState.is_playing) {
                            handleRemotePause();
                        }
                    }
                }
            }).catch(() => {});
        }
    }
});

function unlockAudio() {
    const audioCtx = getAudioContext();
    if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
    if (engines.active) {
        engines.active.play().then(() => {
            state.isPlaying = true;
            UI.updatePlayBtn(true);
            requestWakeLock();
        }).catch(err => console.warn("Unlock attempt blocked:", err));
    }
}

// Automatic unlock of browser soundcard on first user gesture
const unlockOnGesture = () => {
    const ctx = getAudioContext();
    if (ctx && ctx.state === 'suspended') ctx.resume();
    if (engines.active && state.isPlaying && engines.active.paused) {
        engines.active.play().catch(() => {});
    }
    document.removeEventListener('pointerdown', unlockOnGesture);
    document.removeEventListener('keydown', unlockOnGesture);
};
document.addEventListener('pointerdown', unlockOnGesture, { once: true });
document.addEventListener('keydown', unlockOnGesture, { once: true });

// ==========================================
// 🎧 UNIVERSAL JOIN LIVE HUB MODAL HANDLERS
// ==========================================

function showJoinModal(track, liveState) {
    const modal = document.getElementById('join-hub-modal');
    const box = document.getElementById('join-hub-box');
    if (!modal) return;

    const playState = document.getElementById('join-playing-state');
    const idleState = document.getElementById('join-idle-state');
    const titleEl = document.getElementById('join-track-title');
    const artistEl = document.getElementById('join-track-artist');
    const hubNameEl = document.getElementById('join-hub-name');

    if (window.HUB_NAME && hubNameEl) {
        hubNameEl.textContent = window.HUB_NAME;
    }

    const currentTrack = track || (state.tracks && state.tracks.length > 0 ? state.tracks[state.currentIndex || 0] : null);
    const isPlaying = liveState && (liveState.is_playing === true || liveState.status === 'active');

    if (currentTrack && isPlaying) {
        if (titleEl) titleEl.innerText = currentTrack.title || 'Unknown Track';
        if (artistEl) artistEl.innerText = currentTrack.performer || 'Unknown Artist';
        playState?.classList.remove('hidden');
        idleState?.classList.add('hidden');
    } else if (currentTrack) {
        if (titleEl) titleEl.innerText = currentTrack.title || 'Ready to Play';
        if (artistEl) artistEl.innerText = currentTrack.performer || 'Lyraz Studio';
        playState?.classList.remove('hidden');
        idleState?.classList.add('hidden');
    } else {
        playState?.classList.add('hidden');
        idleState?.classList.remove('hidden');
    }

    modal.classList.remove('opacity-0', 'pointer-events-none');
    box?.classList.remove('translate-y-6', 'scale-95');
}

async function joinLiveHubNow() {
    hasUserJoined = true;
    primeAudioEngines();
    dismissJoinModal();

    state.isSyncing = true;
    const liveState = await Network.fetchHubState();
    if (liveState && liveState.status === 'active' && liveState.file_unique_id) {
        const idx = state.tracks.findIndex(t => t.file_unique_id === liveState.file_unique_id);
        if (idx !== -1) {
            pendingSyncCommand = {
                base_seek: liveState.seek_position,
                server_now: liveState.server_time
            };
            loadTrack(idx, true, liveState.seek_position);
        }
    } else if (state.tracks.length > 0) {
        loadTrack(state.currentIndex >= 0 ? state.currentIndex : 0, true);
    }
    setTimeout(() => { state.isSyncing = false; }, 1000);
}

function dismissJoinModal() {
    hasUserJoined = true;
    const modal = document.getElementById('join-hub-modal');
    const box = document.getElementById('join-hub-box');
    if (!modal) return;
    modal.classList.add('opacity-0', 'pointer-events-none');
    box?.classList.add('translate-y-6', 'scale-95');
}

window.joinLiveHubNow = joinLiveHubNow;
window.dismissJoinModal = dismissJoinModal;
window.showJoinModal = showJoinModal;
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
window.unlockAudio = unlockAudio;
window.engines = engines;