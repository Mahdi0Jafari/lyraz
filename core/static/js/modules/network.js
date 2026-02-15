// modules/network.js
import { state } from './state.js';

let sseConnection = null;
let authConnection = null;

export async function initAuth(callbacks) {
    try {
        const res = await fetch('/api/auth/init', { method: 'POST' });
        const data = await res.json();
        
        state.sessionToken = data.token;
        localStorage.setItem('fanus_session_token', state.sessionToken);
        
        callbacks.onQRReady(`${window.location.origin}/connect/${state.sessionToken}`);
        
        // Listen for Login
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
        if (data.status === 'active') callbacks.onLogin(data.admin);
        else initAuth(callbacks); // اگر توکن هست ولی اکتیو نیست
    } catch (e) { initAuth(callbacks); }
}

export function initControlSSE(callbacks) {
    if (sseConnection) sseConnection.close();
    sseConnection = new EventSource("/events");
    
    sseConnection.onmessage = (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.session_token && data.session_token !== state.sessionToken) return;
            
            // New Track added
            if (data.file_unique_id && !data.action) callbacks.onQueueUpdate();
            
            // Remote Command
            if (data.type === 'command') callbacks.onCommand(data);
            
        } catch (err) {}
    };
}

export async function fetchQueue() {
    try {
        const res = await fetch(`/api/control/queue/${state.sessionToken}`);
        const data = await res.json();
        return Array.isArray(data) ? data : [];
    } catch (e) { return []; }
}

export function reportStatus(trackId, isPlaying, currentTime, duration) {
    if (!state.sessionToken) return;
    fetch('/api/control/report_status', {
        method: 'POST',
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