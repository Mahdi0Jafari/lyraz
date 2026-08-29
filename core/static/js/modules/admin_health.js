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
