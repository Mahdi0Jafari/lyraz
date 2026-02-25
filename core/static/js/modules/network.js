// modules/network.js
import { state } from './state.js';

let sseConnection = null;
let authConnection = null;

// ==========================================
// 🔐 Authentication & Session
// ==========================================

function setupAuthSSE(callbacks) {
    if (authConnection) authConnection.close();
    // 🔥 اصلاح امنیتی: ارسال توکن در مسیر درخواست SSE
    authConnection = new EventSource(`/api/events/${state.sessionToken}`);
    
    authConnection.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'session_activated' && msg.session_token === state.sessionToken) {
                console.log("[Network] Hub activated via SSE!");
                authConnection.close();
                callbacks.onLogin(msg.admin);
            }
        } catch (err) {}
    };
}

export async function initAuth(callbacks) {
    if (state.sessionToken && state.hubStatus === 'waiting') {
        console.log("[Network] Resuming existing waiting Hub:", state.sessionToken);
        callbacks.onQRReady(`${window.location.origin}/connect/${state.sessionToken}`);
        setupAuthSSE(callbacks);
        return;
    }

    console.log("[Network] Requesting new Live Hub...");
    try {
        const res = await fetch('/api/auth/init', { method: 'POST' });
        const data = await res.json();
        
        if (data.status === 'success') {
            state.sessionToken = data.token;
            state.hubStatus = 'waiting';
            localStorage.setItem('fanus_session_token', state.sessionToken);
            
            if (!window.location.pathname.includes(state.sessionToken)) {
                window.history.replaceState({}, '', `/live/${state.sessionToken}`);
            }
            
            callbacks.onQRReady(`${window.location.origin}/connect/${state.sessionToken}`);
            setupAuthSSE(callbacks);
        }
    } catch (err) { 
        console.error("[Network] Hub Creation Failed", err); 
    }
}

export async function validateSession(callbacks) {
    if (!state.sessionToken) {
        return initAuth(callbacks);
    }

    try {
        console.log(`[Network] Validating Hub: ${state.sessionToken}`);
        const res = await fetch(`/api/auth/check/${state.sessionToken}`);
        
        if (res.status === 404) {
            console.warn("[Network] Hub not found. Resetting...");
            localStorage.removeItem('fanus_session_token');
            state.sessionToken = null;
            state.hubStatus = 'waiting';
            
            if (window.location.pathname !== '/') {
                window.location.href = '/';
                return;
            }
            return initAuth(callbacks);
        }
        
        const data = await res.json();
        state.hubStatus = data.status;

        if (data.status === 'active') {
            console.log("[Network] Hub is Active. Proceeding to player.");
            callbacks.onLogin(data.admin);
        } else {
            console.log("[Network] Hub is Waiting for Admin.");
            initAuth(callbacks); 
        }
    } catch (e) { 
        console.error("[Network] Validation Error:", e);
        initAuth(callbacks); 
    }
}

// ==========================================
// 📡 Real-time Sync (SSE) & NTP Logic
// ==========================================

export function initControlSSE(callbacks) {
    if (sseConnection) sseConnection.close();
    // 🔥 اصلاح امنیتی: ارسال توکن در مسیر درخواست SSE
    sseConnection = new EventSource(`/api/events/${state.sessionToken}`);
    
    sseConnection.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.session_token && data.session_token !== state.sessionToken) return;
            
            // 🔥 V4.1: تفکیک سیگنال ترک جدید از فرمان‌ها
            if (data.type === 'new_track') {
                callbacks.onQueueUpdate();
            }
            // ساختار قدیمی برای سازگاری عقب‌رو
            else if (data.file_unique_id && !data.action) {
                 callbacks.onQueueUpdate();
            }
            
            // 🔥 V4.1: NTP Sync - دریافت فرمان زمان‌بندی شده
            if (data.type === 'command') {
                // کالیبره کردن ساعت کلاینت با سرور
                if (data.server_now) {
                    const localNow = Date.now() / 1000;
                    state.serverTimeOffset = data.server_now - localNow;
                }
                callbacks.onCommand(data);
            }
            
        } catch (err) {}
    };

    sseConnection.onerror = () => {
        console.warn("SSE Connection lost. Reconnecting...");
        sseConnection.close();
        setTimeout(() => initControlSSE(callbacks), 3000);
    };
}

// ==========================================
// 📦 API Operations
// ==========================================

export async function fetchQueue() {
    try {
        const res = await fetch(`/api/control/queue/${state.sessionToken}`);
        const data = await res.json();
        return Array.isArray(data) ? data : [];
    } catch (e) { 
        return []; 
    }
}

export async function fetchHubState() {
    if (!state.sessionToken) return null;
    try {
        const res = await fetch(`/api/stream/state/${state.sessionToken}`);
        if (!res.ok) return null;
        return await res.json();
    } catch (e) {
        console.error("[Network] Hub State Fetch Error", e);
        return null;
    }
}

export function reportStatus(trackId, isPlaying, currentTime, duration) {
    if (!state.sessionToken) return;
    fetch('/api/control/report_status', {
        method: 'POST',
        keepalive: true, 
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            token: state.sessionToken,
            file_unique_id: trackId,
            is_playing: isPlaying,
            current_time: currentTime,
            duration: duration || 0
        })
    }).catch(() => {});
}

export function markAsPlayed(trackId) {
    fetch('/api/control/mark_played', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ token: state.sessionToken, file_unique_id: trackId })
    }).catch(() => {});
}

// ==========================================
// 🧠 Smart Network Awareness & Pre-fetching
// ==========================================

export function isNetworkFavorableForPreload() {
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    if (conn) {
        if (conn.saveData) return false;
        if (conn.effectiveType === '2g' || conn.effectiveType === 'slow-2g') return false;
        if (conn.downlink && conn.downlink < 1.0) return false;
    }
    return true; 
}

export function preloadAssets(trackId) {
    if (!isNetworkFavorableForPreload() || !trackId) return false; 
    const img = new Image();
    img.crossOrigin = "Anonymous";
    img.src = `/cover/${trackId}`;
    fetch(`/stream/lyrics/${trackId}`, { priority: 'low' }).catch(() => {});
    return true; 
}