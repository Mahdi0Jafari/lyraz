let currentFileId = null;
let isBulkMode = false;

// --- 1. TABS SYSTEM ---
function switchTab(tabName) {
    // 🔥 تب 'users' به آرایه اضافه شد تا توسط سیستم شناخته شود
    const tabs = ['library', 'automation', 'channels', 'devices', 'users'];
    
    // Hide all tabs
    tabs.forEach(t => {
        const el = document.getElementById(`tab-${t}`);
        if(el) el.classList.add('hidden');
    });
    
    // Show target tab
    const targetTab = document.getElementById(`tab-${tabName}`);
    if(targetTab) targetTab.classList.remove('hidden');
    
    // Update Sidebar Active State
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    const navItem = document.getElementById(`nav-${tabName}`);
    if(navItem) navItem.classList.add('active');
    
    // Update Header Title (تایتل تب جدید اضافه شد)
    const titles = {
        'library': 'Music Library', 
        'automation': 'Automation Rules', 
        'channels': 'Channel Manager',
        'devices': 'Device Manager',
        'users': 'Intelligence & Users'
    };
    const pageTitle = document.getElementById('page-title');
    if(pageTitle && titles[tabName]) {
        // تغییر فقط متن اول بدون حذف کردن آیکون/بج (Badge) داخل هدر
        pageTitle.firstChild.nodeValue = titles[tabName] + " ";
    }
}

// --- 2. MULTI-SELECT SYSTEM (For Library) ---
function updateSelection() {
    const checkboxes = document.querySelectorAll('.track-checkbox:checked');
    const count = checkboxes.length;
    const bar = document.getElementById('floating-bar');
    
    const countEl = document.getElementById('selected-count');
    if(countEl) countEl.innerText = count;
    
    if(bar) {
        count > 0 ? bar.classList.add('visible') : bar.classList.remove('visible');
    }
}

function toggleAll(source) {
    document.querySelectorAll('.track-checkbox').forEach(cb => {
        cb.checked = source.checked;
    });
    updateSelection();
}

// --- 3. MODAL SYSTEM (For Broadcasting) ---
function openBroadcastModal(fileId, title) {
    isBulkMode = false;
    currentFileId = fileId;
    
    const titleEl = document.getElementById('modal-track-title');
    if(titleEl) titleEl.innerText = title;
    
    const modal = document.getElementById('broadcast-modal');
    if(modal) modal.classList.remove('hidden');
}

function openBulkModal() {
    const count = document.querySelectorAll('.track-checkbox:checked').length;
    if (count === 0) return alert("No tracks selected!");
    
    isBulkMode = true;
    const titleEl = document.getElementById('modal-track-title');
    if(titleEl) titleEl.innerText = `${count} Tracks Selected for Bulk Send`;
    
    const modal = document.getElementById('broadcast-modal');
    if(modal) modal.classList.remove('hidden');
}

function closeModal() {
    const modal = document.getElementById('broadcast-modal');
    if(modal) modal.classList.add('hidden');
    currentFileId = null;
    isBulkMode = false;
}

// --- 4. API ACTIONS ---

// A. Broadcast Logic (Music)
async function confirmBroadcast() {
    const channelSelect = document.getElementById('modal-channel-select');
    const captionInput = document.getElementById('modal-manual-caption');
    const btn = document.getElementById('modal-send-btn');
    
    const channelId = channelSelect ? channelSelect.value : null;
    const caption = captionInput ? captionInput.value : '';
    
    if(!channelId) return alert("Select a channel");
    
    btn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">sync</span> Sending...';
    btn.disabled = true;

    let payload = { channel_id: channelId, caption: caption };
    
    if (isBulkMode) {
        const selected = Array.from(document.querySelectorAll('.track-checkbox:checked')).map(cb => cb.value);
        payload.track_ids = selected;
    } else {
        payload.file_id = currentFileId; 
    }

    try {
        const res = await fetch('/api/admin/bulk/broadcast', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        const data = await res.json();
        if(data.status === 'success') {
            closeModal();
            alert(`✅ Successfully sent ${data.count} items!`);
            if(isBulkMode) {
                // اگر گروهی بود، تیک‌ها را بردار
                document.querySelectorAll('.track-checkbox').forEach(cb => cb.checked = false);
                document.querySelector('.custom-checkbox').checked = false; // header checkbox
                updateSelection();
            }
        } else {
            alert("Error: " + data.message);
        }
    } catch(e) { 
        alert("Network Error: Check your connection."); 
    }
    
    btn.innerHTML = "Send Now";
    btn.disabled = false;
}

// B. Global Settings
async function saveSettings() {
    const enabledEl = document.getElementById('auto-enabled');
    const channelEl = document.getElementById('auto-channel');
    const captionEl = document.getElementById('auto-caption');

    const enabled = enabledEl ? enabledEl.checked : false;
    const channel = channelEl ? channelEl.value : '';
    const caption = captionEl ? captionEl.value : '';

    try {
        await fetch('/api/admin/settings/update', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ enabled, channel_id: channel, caption })
        });
        alert("✅ Settings successfully saved.");
    } catch(e) {
        alert("Network Error while saving settings.");
    }
}

// C. Channel Management (Add/Delete)
async function addChannel() {
    const chatIdEl = document.getElementById('new-channel-id');
    const titleEl = document.getElementById('new-channel-name');
    
    const chatId = chatIdEl ? chatIdEl.value.trim() : '';
    const title = titleEl ? titleEl.value.trim() : '';
    
    if(!chatId) return alert("Channel/Chat ID is required. (Usually starts with -100)");
    
    try {
        await fetch('/api/admin/channels/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ chat_id: chatId, title: title })
        });
        location.reload();
    } catch(e) {
        alert("Network Error while adding channel.");
    }
}

async function deleteChannel(id) {
    if(!confirm("⚠️ Are you sure you want to remove this channel from the manager?")) return;
    
    try {
        await fetch('/api/admin/channels/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ chat_id: id })
        });
        location.reload();
    } catch(e) {
        alert("Network Error while deleting channel.");
    }
}

// D. Device Linking (Bind Device to Channel)
async function updateDeviceLink(token, selectElement) {
    const channelId = selectElement.value;
    
    // Visual feedback (Green border temporarily)
    const originalBorder = selectElement.style.borderColor;
    selectElement.style.borderColor = '#0df233'; 
    selectElement.disabled = true;

    try {
        const res = await fetch('/api/admin/device/link', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ token: token, channel_id: channelId })
        });
        
        const data = await res.json();
        
        if(data.status === 'success') {
            setTimeout(() => {
                selectElement.style.borderColor = originalBorder;
                selectElement.disabled = false;
            }, 600);
        } else {
            alert("Error linking device.");
            selectElement.style.borderColor = '#ef4444'; // Tailwind text-red-500
            selectElement.disabled = false;
        }
    } catch(e) {
        alert("Network Error.");
        selectElement.style.borderColor = '#ef4444';
        selectElement.disabled = false;
    }
}

// E. Channel Custom Rules (Save Template)
async function saveChannelRule(chatId, btn) {
    const textarea = document.getElementById(`rule-${chatId}`);
    const template = textarea.value;
    
    // Loading State with Spinner
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span class="material-symbols-outlined text-[12px] animate-spin">sync</span> Saving...';
    btn.disabled = true;

    try {
        const res = await fetch('/api/admin/channels/update_template', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ chat_id: chatId, template: template })
        });
        
        const data = await res.json();
        
        if(data.status === 'success') {
            btn.innerHTML = '<span class="material-symbols-outlined text-[12px]">check</span> Saved';
            btn.classList.add('text-green-400', 'border-green-400'); // Visual Success
            
            setTimeout(() => {
                btn.innerHTML = originalText;
                btn.classList.remove('text-green-400', 'border-green-400');
                btn.disabled = false;
            }, 2000);
        } else {
            alert("Error saving custom rule.");
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    } catch(e) {
        alert("Network Error.");
        btn.disabled = false;
        btn.innerHTML = originalText;
    }
}

// --- Helpers ---
function insertVar(text) {
    const textarea = document.getElementById('auto-caption');
    if(textarea) {
        // Insert at cursor position or end
        textarea.value += text;
        textarea.focus();
    }
}