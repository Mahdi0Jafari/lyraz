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

// --- 4. Quota Management ---
async function updateUserQuota(userId, newQuota) {
    try {
        const res = await fetch('/api/admin/users/update_quota', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ user_id: userId, quota: parseInt(newQuota) })
        });
        const data = await res.json();
        if(data.status !== 'success') {
            alert("Error updating quota: " + (data.message || 'Unknown error'));
            location.reload();
        }
    } catch(e) {
        alert("Network Error: Could not update user quota.");
    }
}

// --- 5. Referral Network Inspector ---
async function openUserReferralsModal(userId, userName) {
    const modal = document.getElementById('referrals-modal');
    const title = document.getElementById('ref-modal-title');
    const subtitle = document.getElementById('ref-modal-subtitle');
    const loading = document.getElementById('ref-modal-loading');
    const content = document.getElementById('ref-modal-content');
    const empty = document.getElementById('ref-modal-empty');

    if(!modal) return;
    modal.classList.remove('hidden');
    title.innerText = `Referral Network of ${userName}`;
    subtitle.innerText = `Audit list of users invited by ID #${userId}`;
    loading.classList.remove('hidden');
    content.classList.add('hidden');
    empty.classList.add('hidden');
    content.innerHTML = '';

    try {
        const res = await fetch(`/api/admin/users/${userId}/referrals`);
        const json = await res.json();
        loading.classList.add('hidden');

        if (json.status === 'success' && json.data && json.data.referrals && json.data.referrals.length > 0) {
            subtitle.innerText = `${json.data.referrals.length} friend${json.data.referrals.length > 1 ? 's' : ''} invited by ${userName}`;
            content.classList.remove('hidden');

            json.data.referrals.forEach(ref => {
                const item = document.createElement('div');
                item.className = 'flex items-center justify-between p-3 rounded-xl bg-white/5 border border-white/5 hover:border-primary/30 transition-colors';
                
                const roleBadge = ref.role === 'admin' 
                    ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-primary/20 text-primary border border-primary/30 font-bold">Admin</span>' 
                    : (ref.role === 'pro' 
                        ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30 font-bold">Pro</span>' 
                        : '<span class="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-gray-400 font-bold">User</span>');
                
                const dateStr = ref.join_date ? ref.join_date.slice(0, 16) : 'Unknown';
                const userHandle = ref.username ? `@${ref.username}` : `ID: ${ref.telegram_id}`;

                item.innerHTML = `
                    <div class="flex items-center gap-3">
                        <div class="size-8 rounded-full bg-gradient-to-tr from-gray-700 to-gray-600 flex items-center justify-center text-white text-xs font-bold shadow">
                            ${(ref.first_name || 'U').charAt(0).toUpperCase()}
                        </div>
                        <div>
                            <div class="text-xs font-bold text-white flex items-center gap-2">
                                <span>${escapeHtml(ref.first_name || 'Anonymous')}</span>
                                ${roleBadge}
                            </div>
                            <div class="text-[10px] text-gray-500 font-mono tracking-wider">${userHandle}</div>
                        </div>
                    </div>
                    <div class="text-[10px] text-gray-400 font-mono flex items-center gap-1">
                        <span class="material-symbols-outlined text-[12px] text-gray-500">calendar_today</span>
                        ${dateStr}
                    </div>
                `;
                content.appendChild(item);
            });
        } else {
            empty.classList.remove('hidden');
        }
    } catch(e) {
        loading.classList.add('hidden');
        empty.innerText = "Error loading referral data.";
        empty.classList.remove('hidden');
    }
}

function closeUserReferralsModal() {
    const modal = document.getElementById('referrals-modal');
    if(modal) modal.classList.add('hidden');
}

function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}