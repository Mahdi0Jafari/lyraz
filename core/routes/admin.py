# core/routes/admin.py

import os
import time
import math
import requests
import logging
from collections import deque
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, Response
from core.models import get_db
from core.config import Config
from core.services.admin_service import admin_analytics

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin', __name__)

def is_admin():
    return session.get('is_admin')

# --- Login / Logout ---
@admin_bp.route('/admin/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == Config.ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin.dashboard'))
        else:
            return render_template('login_admin.html', error="Invalid Password")
    return render_template('login_admin.html')

@admin_bp.route('/admin/logout')
def logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin.login'))

# --- Main Dashboard ---
@admin_bp.route('/admin')
def dashboard():
    if not is_admin(): return redirect(url_for('admin.login'))

    db = get_db()
    
    # 1. تنظیمات
    settings = db.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    if not settings:
        db.execute("INSERT OR IGNORE INTO settings (id, default_caption, is_auto_broadcast_enabled) VALUES (1, '', 0)")
        db.commit()
        settings = db.execute("SELECT * FROM settings WHERE id = 1").fetchone()

    # 2. آمار اصلی (Hero Stats)
    total_tracks = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    total_channels = db.execute("SELECT COUNT(*) FROM channels WHERE is_active = 1").fetchone()[0]
    total_devices = db.execute("SELECT COUNT(*) FROM sessions WHERE status = 'active'").fetchone()[0]
    
    # گرفتن آمار تحلیلی مربوط به کاربران از سرویس جدید
    user_stats = admin_analytics.get_dashboard_summary()

    # 3. لیست کانال‌ها
    channels = db.execute("SELECT * FROM channels ORDER BY is_active DESC, title ASC").fetchall()
    
    # 4. لیست دیوایس‌ها
    devices = db.execute("""
        SELECT s.*, u.first_name as owner_name, c.title as channel_name 
        FROM sessions s
        LEFT JOIN users u ON s.admin_id = u.id
        LEFT JOIN channels c ON s.linked_channel_id = c.chat_id
        WHERE s.status = 'active'
        ORDER BY s.created_at DESC
    """).fetchall()

    # 5. لیست موزیک‌ها (Pagination & Search)
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search_query = request.args.get('q', '')
    offset = (page - 1) * per_page
    
    if search_query:
        search_term = f"%{search_query}%"
        tracks = db.execute("SELECT * FROM tracks WHERE title LIKE ? OR performer LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?", 
                            (search_term, search_term, per_page, offset)).fetchall()
        count_res = db.execute("SELECT COUNT(*) FROM tracks WHERE title LIKE ? OR performer LIKE ?", (search_term, search_term)).fetchone()[0]
    else:
        tracks = db.execute("SELECT * FROM tracks ORDER BY id DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
        count_res = total_tracks

    total_pages = math.ceil(count_res / per_page) if per_page else 1

    # 6. لیست کاربران (واکشی از سرویس تحلیل)
    users_page = request.args.get('u_page', 1, type=int)
    users_search = request.args.get('u_q', '')
    users_sort = request.args.get('sort', 'tracks')
    users_data = admin_analytics.get_users_analytics(page=users_page, per_page=50, search_query=users_search, sort_by=users_sort)

    return render_template(
        'admin.html', 
        tracks=tracks, 
        channels=channels, 
        devices=devices,
        settings=settings,
        stats={'tracks': total_tracks, 'channels': total_channels, 'devices': total_devices},
        user_stats=user_stats,
        users_data=users_data,
        page=page, 
        total_pages=total_pages,
        search_query=search_query,
        users_search=users_search,
        users_sort=users_sort
    )

# ==========================================
# 📈 SYSTEM MONITORING & LOGS (Real-time SSE)
# ==========================================

@admin_bp.route('/api/admin/logs/stream/<log_type>')
def stream_logs_sse(log_type):
    """
    استریم زنده (Live Stream) لاگ‌ها با استفاده از معماری Server-Sent Events.
    مانند دستور tail -f در لینوکس عمل می‌کند.
    """
    if not is_admin(): return "Unauthorized", 403

    def generate():
        log_files = {
            'web': 'web.log', 
            'bot': 'bot.log', 
            'worker': 'worker.log'
        }
        filename = log_files.get(log_type, 'web.log')
        # محاسبه مسیر مطلق فایل لاگ (مشابه core/logger.py)
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
        path = os.path.join(base_dir, 'logs', filename)
        
        if not os.path.exists(path):
            yield f"data: [SYSTEM] Waiting for {filename} to be created...\n\n"
            # منتظر می‌مانیم تا فایل ساخته شود
            while not os.path.exists(path):
                time.sleep(2)
            yield f"data: [SYSTEM] File {filename} created. Starting stream...\n\n"

        with open(path, 'r', encoding='utf-8') as f:
            # 1. خواندن ۱۰۰ خط آخر (Historical Context)
            initial_lines = deque(f, maxlen=100)
            for line in initial_lines:
                # حذف کاراکترهای خطرناک برای امنیت SSE
                safe_line = line.strip().replace('\n', ' ')
                if safe_line:
                    yield f"data: {safe_line}\n\n"
            
            # 2. رفتن به حالت انتظار برای خطوط جدید (Real-time Streaming)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5) # نیم ثانیه وقفه برای جلوگیری از مصرف CPU
                    continue
                
                safe_line = line.strip().replace('\n', ' ')
                if safe_line:
                    yield f"data: {safe_line}\n\n"

    # تنظیم هدرها برای جلوگیری از بافرینگ در Nginx و مرورگر
    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no', 
        'Connection': 'keep-alive'
    })


# ==========================================
# 👥 USER MANAGEMENT & ANALYTICS APIs
# ==========================================

@admin_bp.route('/api/admin/users/update_status', methods=['POST'])
def update_user_status():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    
    target_id = data.get('user_id')
    action = data.get('action') # 'role' or 'ban'
    value = data.get('value')
    
    if not target_id or not action:
        return jsonify({'status': 'error', 'message': 'Missing parameters'})
        
    success = admin_analytics.update_user_status(target_id, action, value)
    
    if success:
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Database update failed'})

@admin_bp.route('/api/admin/users/broadcast', methods=['POST'])
def broadcast_to_users():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    
    message_text = data.get('message')
    selection_type = data.get('type', 'all') 
    specific_ids = data.get('user_ids', [])
    
    if not message_text:
        return jsonify({'status': 'error', 'message': 'Message text is empty'})
        
    target_telegram_ids = admin_analytics.get_target_telegram_ids(selection_type, specific_ids)
    
    if not target_telegram_ids:
        return jsonify({'status': 'error', 'message': 'No eligible users found'})

    try:
        from core.tasks import send_bulk_message_task
        send_bulk_message_task(target_telegram_ids, message_text)
        return jsonify({'status': 'success', 'count': len(target_telegram_ids)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


# ==========================================
# 📻 BROADCAST & MUSIC APIs
# ==========================================

@admin_bp.route('/api/admin/broadcast', methods=['POST'])
def broadcast_track():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    return bulk_broadcast()

@admin_bp.route('/api/admin/bulk/broadcast', methods=['POST'])
def bulk_broadcast():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    
    data = request.json
    track_ids = data.get('track_ids', [])
    single_file_id = data.get('file_id') or data.get('single_file_id')
    channel_id = data.get('channel_id')
    manual_caption = data.get('caption')

    if not channel_id: return jsonify({'status': 'error', 'message': 'No channel selected'})

    db = get_db()
    
    channel_info = db.execute("SELECT caption_template FROM channels WHERE chat_id = ?", (channel_id,)).fetchone()
    channel_specific_template = channel_info['caption_template'] if channel_info else None
    
    settings = db.execute("SELECT default_caption FROM settings WHERE id = 1").fetchone()
    global_default = settings['default_caption'] if settings else "{title} - {artist}"

    tracks_to_send = []
    if single_file_id:
        track = db.execute("SELECT file_id, title, performer FROM tracks WHERE file_id = ?", (single_file_id,)).fetchone()
        if track: tracks_to_send.append(track)
    elif track_ids:
        placeholders = ','.join('?' for _ in track_ids)
        query = f"SELECT file_id, title, performer FROM tracks WHERE id IN ({placeholders})"
        tracks_to_send = db.execute(query, track_ids).fetchall()

    if not tracks_to_send: return jsonify({'status': 'error', 'message': 'No tracks found'})

    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/sendAudio"
    success_count = 0
    
    for track in tracks_to_send:
        if manual_caption and manual_caption.strip():
            base_template = manual_caption
        elif channel_specific_template and channel_specific_template.strip():
            base_template = channel_specific_template
        else:
            base_template = global_default
        
        title = track['title'] or 'Unknown'
        artist = track['performer'] or 'Unknown'
        sender_name = "Admin Panel"
        
        final_caption = base_template.replace('{title}', title)\
                                     .replace('{artist}', artist)\
                                     .replace('{sender}', sender_name)
        try:
            resp = requests.post(url, data={'chat_id': channel_id, 'audio': track['file_id'], 'caption': final_caption})
            if resp.status_code == 200: success_count += 1
        except: pass

    return jsonify({'status': 'success', 'count': success_count})


# ==========================================
# ⚙️ SYSTEM & DEVICE APIs
# ==========================================

@admin_bp.route('/api/admin/device/link', methods=['POST'])
def link_device_channel():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    token = data.get('token')
    target_channel = data.get('channel_id') or None
    
    db = get_db()
    db.execute("UPDATE sessions SET linked_channel_id = ? WHERE token = ?", (target_channel, token))
    db.commit()
    return jsonify({'status': 'success'})

@admin_bp.route('/api/admin/settings/update', methods=['POST'])
def update_settings():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    db = get_db()
    is_enabled = 1 if data.get('enabled') else 0
    db.execute("UPDATE settings SET auto_broadcast_channel_id=?, default_caption=?, is_auto_broadcast_enabled=? WHERE id=1",
               (data.get('channel_id'), data.get('caption'), is_enabled))
    db.commit()
    return jsonify({'status': 'success'})

@admin_bp.route('/api/admin/channels/add', methods=['POST'])
def add_channel():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    db = get_db()
    try:
        db.execute("INSERT OR REPLACE INTO channels (chat_id, title, is_active) VALUES (?, ?, 1)", (data.get('chat_id'), data.get('title')))
        db.commit()
        return jsonify({'status': 'success'})
    except Exception as e: return jsonify({'status': 'error', 'message': str(e)})

@admin_bp.route('/api/admin/channels/delete', methods=['POST'])
def delete_channel():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db = get_db()
    db.execute("DELETE FROM channels WHERE chat_id = ?", (request.json.get('chat_id'),))
    db.commit()
    return jsonify({'status': 'success'})

@admin_bp.route('/api/admin/channels/update_template', methods=['POST'])
def update_channel_template():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    db = get_db()
    db.execute("UPDATE channels SET caption_template = ? WHERE chat_id = ?", (data.get('template'), data.get('chat_id')))
    db.commit()
    return jsonify({'status': 'success'})