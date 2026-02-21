// modules/network.js
import { state } from './state.js';

let sseConnection = null;
let authConnection = null;

// ==========================================
// 🔐 Authentication & Session
// ==========================================

export async function initAuth(callbacks) {
    try {
        const res = await fetch('/api/auth/init', { method: 'POST' });
        const data = await res.json();
        
        state.sessionToken = data.token;
        localStorage.setItem('fanus_session_token', state.sessionToken);
        
        callbacks.onQRReady(`${window.location.origin}/connect/${state.sessionToken}`);
        
        // Listen for Login via SSE
        if (authConnection) authConnection.close();
        authConnection = new EventSource("/events");
        authConnection.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg.type === 'session_activated' && msg.session_token === state.sessionToken) {
                    authConnection.close();
                    callbacks.onLogin(msg.admin);
                }
            } catch (err) {}
        };
        
    } catch (err) { console.error("Auth Failed", err); }
}

export async function validateSession(callbacks) {
    try {
        const res = await fetch(`/api/auth/check/${state.sessionToken}`);
        if (res.status === 404) {
            localStorage.clear();
            location.reload();
            return;
        }
        const data = await res.json();
        if (data.status === 'active') {
            callbacks.onLogin(data.admin);
        } else {
            initAuth(callbacks); // توکن نامعتبر یا منقضی شده
        }
    } catch (e) { 
        initAuth(callbacks); 
    }
}

// ==========================================
// 📡 Real-time Sync (SSE)
// ==========================================

export function initControlSSE(callbacks) {
    if (sseConnection) sseConnection.close();
    sseConnection = new EventSource("/events");
    
    sseConnection.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.session_token && data.session_token !== state.sessionToken) return;
            
            // New Track added to queue
            if (data.file_unique_id && !data.action) callbacks.onQueueUpdate();
            
            // Remote Command received (Play, Pause, Next, etc.)
            if (data.type === 'command') callbacks.onCommand(data);
            
        } catch (err) {}
    };

    // Auto-reconnect logic in case of SSE drops
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

export function reportStatus(trackId, isPlaying, currentTime, duration) {
    if (!state.sessionToken) return;
    // استفاده از keepalive برای اطمینان از ارسال حتی در صورت بستن تب
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

/**
 * بررسی هوشمند وضعیت پهنای باند و محدودیت دیتای کاربر
 * @returns {boolean} آیا شرایط برای Preload مهیا است؟
 */
export function isNetworkFavorableForPreload() {
    const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    
    if (conn) {
        // 1. اگر کاربر Data Saver گوشی را روشن کرده باشد
        if (conn.saveData) return false;
        
        // 2. اگر شبکه 2G یا به شدت کند باشد
        if (conn.effectiveType === '2g' || conn.effectiveType === 'slow-2g') {
            return false;
        }
        
        // 3. اگر پهنای باند خیلی ضعیف باشد
        if (conn.downlink && conn.downlink < 1.0) {
            return false;
        }
    }
    // پیش‌فرض: اجازه لود در پس‌زمینه (4G, 3G, WiFi)
    return true; 
}

/**
 * فچ کردن بی‌صدای (Silent Fetch) Assetهای آهنگ بعدی
 * @param {string} trackId - آیدی آهنگ بعدی
 * @returns {boolean} - آیا بافر کردن فایل صوتی هم مجاز است؟
 */
export function preloadAssets(trackId) {
    if (!isNetworkFavorableForPreload() || !trackId) {
        return false; // به پلیر می‌گوید فایل صوتی را بافر نکند
    }

    // 1. Preload Cover Image (ذخیره خودکار در کش تصویر مرورگر)
    const img = new Image();
    img.crossOrigin = "Anonymous";
    img.src = `/cover/${trackId}`;

    // 2. Preload Lyrics (ذخیره در کش HTTP) - با اولویت پایین
    fetch(`/stream/lyrics/${trackId}`, { priority: 'low' }).catch(() => {});
    
    return true; // سیگنال برای پلیر که ادامه دهد و فایل صوتی را بافر کند
}