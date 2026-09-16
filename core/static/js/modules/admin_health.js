// static/js/modules/admin_health.js

/**
 * Lyraz Admin System Health & Maintenance Module
 * Controls live metrics polling, cache purging, database optimization, and backup requests.
 */

async function purgeTempCache() {
    if (!confirm("🧹 Are you sure you want to purge all temporary YouTube audio files from the cache?")) {
        return;
    }

    const btn1 = document.getElementById('btn-purge-quick');
    const btn2 = document.getElementById('btn-purge-main');
    
    if (btn1) btn1.disabled = true;
    if (btn2) {
        btn2.disabled = true;
        btn2.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">sync</span><span>Purging...</span>';
    }

    try {
        const res = await fetch('/api/admin/system/purge-cache', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        
        if (data.success) {
            alert(`✅ Cache Purged Successfully!\n\nDeleted ${data.deleted_files} files.\nFreed ${data.freed_mb} MB of disk space.`);
            // Refresh health stats on the page
            await refreshSystemHealth();
        } else {
            alert("❌ Failed to purge cache: " + (data.message || 'Unknown error'));
        }
    } catch (e) {
        alert("⚠️ Network error while trying to purge cache.");
    } finally {
        if (btn1) btn1.disabled = false;
        if (btn2) {
            btn2.disabled = false;
            btn2.innerHTML = '<span class="material-symbols-outlined text-[16px]">cleaning_services</span><span>Free Up Disk Space</span>';
        }
    }
}

async function optimizeDatabase() {
    const btn1 = document.getElementById('btn-opt-quick');
    const btn2 = document.getElementById('btn-opt-main');

    if (btn1) btn1.disabled = true;
    if (btn2) {
        btn2.disabled = true;
        btn2.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">sync</span><span>Optimizing...</span>';
    }

    try {
        const res = await fetch('/api/admin/system/optimize-db', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();

        if (data.success) {
            alert("⚡ Database Optimized!\n\nPRAGMA optimize completed successfully. Indexes and query plans are updated.");
        } else {
            alert("❌ Database optimization failed: " + (data.message || 'Unknown error'));
        }
    } catch (e) {
        alert("⚠️ Network error while optimizing database.");
    } finally {
        if (btn1) btn1.disabled = false;
        if (btn2) {
            btn2.disabled = false;
            btn2.innerHTML = '<span class="material-symbols-outlined text-[16px]">bolt</span><span>Run PRAGMA Optimize</span>';
        }
    }
}

async function refreshSystemHealth() {
    const refreshBtn = document.getElementById('btn-refresh-health');
    if (refreshBtn) {
        refreshBtn.classList.add('opacity-50', 'pointer-events-none');
    }

    try {
        const res = await fetch('/api/admin/system/health');
        const json = await res.json();

        if (json.status === 'success') {
            // Smoothly reload page tab or update DOM
            window.location.href = '/admin?active_tab=health';
        }
    } catch (e) {
        console.error("Failed to refresh health metrics:", e);
    } finally {
        if (refreshBtn) {
            refreshBtn.classList.remove('opacity-50', 'pointer-events-none');
        }
    }
}

async function testYouTubeAuth() {
    const icon = document.getElementById('btn-test-auth-icon');
    const label = document.getElementById('cookie-status-label');
    const badge = document.getElementById('cookie-status-badge');
    const dot = document.getElementById('cookie-status-dot');
    const details = document.getElementById('cookie-status-details');
    const glow = document.getElementById('cookie-card-glow');
    const cardIcon = document.getElementById('cookie-card-icon');
    const lastChecked = document.getElementById('cookie-last-checked');

    if (icon) icon.classList.add('animate-spin');
    if (label) label.textContent = 'Verifying...';

    try {
        const res = await fetch('/api/admin/system/test-cookies', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const json = await res.json();
        const data = json.data;

        if (label) label.textContent = data.label;
        if (details) {
            details.textContent = data.details;
            details.title = data.details;
        }
        if (lastChecked && data.last_checked) {
            lastChecked.textContent = data.last_checked;
        }

        if (data.status === 'valid') {
            if (badge) badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-bold bg-primary/20 text-primary border border-primary/30';
            if (dot) dot.className = 'size-1.5 rounded-full bg-primary animate-pulse';
            if (cardIcon) cardIcon.className = 'size-11 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-primary shadow-inner';
            if (glow) glow.className = 'absolute -right-6 -top-6 size-24 bg-primary/10 rounded-full blur-2xl transition-all duration-500';
        } else if (data.status === 'partial') {
            if (badge) badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-bold bg-yellow-500/20 text-yellow-400 border border-yellow-500/30';
            if (dot) dot.className = 'size-1.5 rounded-full bg-yellow-400';
            if (cardIcon) cardIcon.className = 'size-11 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-yellow-400 shadow-inner';
            if (glow) glow.className = 'absolute -right-6 -top-6 size-24 bg-yellow-500/10 rounded-full blur-2xl transition-all duration-500';
        } else {
            if (badge) badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-bold bg-red-500/20 text-red-400 border border-red-500/30';
            if (dot) dot.className = 'size-1.5 rounded-full bg-red-400';
            if (cardIcon) cardIcon.className = 'size-11 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-red-400 shadow-inner';
            if (glow) glow.className = 'absolute -right-6 -top-6 size-24 bg-red-500/10 rounded-full blur-2xl transition-all duration-500';
        }
    } catch (e) {
        if (label) label.textContent = 'Connection Error';
        if (details) details.textContent = 'Failed to communicate with admin backend.';
    } finally {
        if (icon) icon.classList.remove('animate-spin');
    }
}
