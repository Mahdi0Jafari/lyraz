// static/js/modules/admin_users.js

/**
 * Lyraz User Analytics Module
 * Handles interactions for the User Intelligence Grid and Broadcast System.
 */

// --- 1. User Selection & Floating Bar System ---
function toggleAllUsers(source) {
    document.querySelectorAll('.user-checkbox').forEach(cb => {
        // نادیده گرفتن کاربرانی که بن شده‌اند (به صورت بصری غیرفعال هستند)
        if (!cb.closest('tr').classList.contains('grayscale')) {
            cb.checked = source.checked;
        }
    });
    updateUserSelection();
}

function updateUserSelection() {
    const checkboxes = document.querySelectorAll('.user-checkbox:checked');
    const count = checkboxes.length;
    const bar = document.getElementById('users-floating-bar');
    const countEl = document.getElementById('users-selected-count');
    
    if(countEl) countEl.innerText = count;
    
    if(bar) {
        count > 0 ? bar.classList.add('visible') : bar.classList.remove('visible');
    }
}

// --- 2. Security & Role Control ---
async function updateUserRole(userId, newRole) {
    try {
        const res = await fetch('/api/admin/users/update_status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: userId, action: 'role', value: newRole })
        });
        const data = await res.json();
        
        if(data.status !== 'success') {
            alert("Error updating role: " + (data.message || 'Unknown error'));
            location.reload(); // Revert back if failed
        }
        // اگر موفقیت‌آمیز بود، نیازی به رفرش نیست (Seamless UX)
    } catch(e) {
        alert("Network Error: Could not connect to the server.");
    }
}

async function toggleUserBan(userId, targetStatus) {
    const actionText = targetStatus === 1 ? "Ban" : "Unban";
    
    // لایه امنیتی مضاعف برای جلوگیری از کلیک اشتباه
    if(!confirm(`⚠️ Are you sure you want to ${actionText} this user?`)) return;

    try {
        const res = await fetch('/api/admin/users/update_status', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: userId, action: 'ban', value: targetStatus })
        });
        const data = await res.json();
        
        if(data.status === 'success') {
            // رفرش کردن صفحه برای اعمال استایل‌های Grayscale روی سطر کاربر
            location.reload(); 
        } else {
            alert("Error updating ban status: " + (data.message || 'Unknown error'));
        }
    } catch(e) {
        alert("Network Error");
    }
}

// --- 3. Broadcast Engine (Bulk Messaging) ---
let userBroadcastType = 'all'; // 'all', 'selected', 'specific'
let userBroadcastSpecificIds = [];

function openUserBroadcastModal(type, specificIds = []) {
    userBroadcastType = type;
    userBroadcastSpecificIds = specificIds;
    
    const descEl = document.getElementById('ub-modal-desc');
    
    // داینامیک کردن متن مُدال بر اساس نوع ارسال
    if (type === 'all') {
        descEl.innerHTML = "<span class='text-primary'>Global Broadcast:</span> Sending to ALL active users.";
    } else if (type === 'selected') {
        const count = document.querySelectorAll('.user-checkbox:checked').length;
        if (count === 0) return alert("No users selected!");
        descEl.innerHTML = `<span class='text-blue-400'>Targeted Broadcast:</span> Sending to ${count} selected users.`;
    } else if (type === 'specific') {
        descEl.innerHTML = "<span class='text-purple-400'>Direct Message:</span> Sending a private message.";
    }

    const modal = document.getElementById('user-broadcast-modal');
    if(modal) modal.classList.remove('hidden');
}

function closeUserBroadcastModal() {
    const modal = document.getElementById('user-broadcast-modal');
    if(modal) modal.classList.add('hidden');
    
    const textarea = document.getElementById('ub-message-text');
    if(textarea) textarea.value = '';
    
    userBroadcastType = 'all';
    userBroadcastSpecificIds = [];
}

async function confirmUserBroadcast() {
    const textarea = document.getElementById('ub-message-text');
    const messageText = textarea ? textarea.value.trim() : '';
    const btn = document.getElementById('ub-send-btn');
    
    if(!messageText) {
        textarea.focus();
        return alert("Message body cannot be empty.");
    }
    
    // تغییر حالت دکمه به Loading
    btn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">sync</span> Initiating Task...';
    btn.disabled = true;

    let payload = {
        message: messageText,
        type: userBroadcastType
    };

    // اگر ارسال گروهی انتخابی است، آیدی‌ها را از DOM جمع می‌کنیم
    if (userBroadcastType === 'selected') {
        const selectedIds = Array.from(document.querySelectorAll('.user-checkbox:checked')).map(cb => cb.value);
        payload.type = 'specific';
        payload.user_ids = selectedIds;
    } else if (userBroadcastType === 'specific') {
        payload.user_ids = userBroadcastSpecificIds;
    }

    try {
        const res = await fetch('/api/admin/users/broadcast', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        
        const data = await res.json();
        
        if(data.status === 'success') {
            closeUserBroadcastModal();
            // پیام موفقیت هوشمند
            alert(`✅ Background Task Started!\n\nHuey worker is now sending messages to ${data.count} users in the background to prevent rate limits.`);
            
            // Uncheck all after success
            if(userBroadcastType === 'selected') {
                document.querySelectorAll('.user-checkbox').forEach(cb => cb.checked = false);
                document.querySelector('.custom-checkbox').checked = false; // header checkbox
                updateUserSelection();
            }
        } else {
            alert("Error initiating broadcast: " + data.message);
        }
    } catch(e) { 
        alert("Network Error: Could not reach the server."); 
    }
    
    // بازگردانی دکمه به حالت اولیه (در صورت خطا)
    btn.innerHTML = '<span>Send to Target</span><span class="material-symbols-outlined text-[16px]">rocket_launch</span>';
    btn.disabled = false;
}