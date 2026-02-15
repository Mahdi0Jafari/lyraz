# core/routes/admin.py

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from core.models import get_db
from core.config import Config
import requests
import math

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

    # 2. آمار
    total_tracks = db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    total_channels = db.execute("SELECT COUNT(*) FROM channels WHERE is_active = 1").fetchone()[0]
    total_devices = db.execute("SELECT COUNT(*) FROM sessions WHERE status = 'active'").fetchone()[0]

    # 3. لیست کانال‌ها
    channels = db.execute("SELECT * FROM channels ORDER BY is_active DESC, title ASC").fetchall()
    
    # 4. لیست دیوایس‌ها (همراه با کانال متصل و نام صاحب)
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

    total_pages = math.ceil(count_res / per_page)

    return render_template(
        'admin.html', 
        tracks=tracks, 
        channels=channels, 
        devices=devices,
        settings=settings,
        stats={'tracks': total_tracks, 'channels': total_channels, 'devices': total_devices},
        page=page, 
        total_pages=total_pages,
        search_query=search_query
    )

# --- Broadcast Logic ---
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
    
    # 1. دریافت اطلاعات کانال (برای تمپلیت اختصاصی)
    channel_info = db.execute("SELECT caption_template FROM channels WHERE chat_id = ?", (channel_id,)).fetchone()
    channel_specific_template = channel_info['caption_template'] if channel_info else None
    
    # 2. دریافت تنظیمات پیش‌فرض (گلوبال)
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
        # 🔥 اولویت‌بندی کپشن: 
        # ۱. دستی > ۲. اختصاصی کانال > ۳. پیش‌فرض سیستم
        if manual_caption and manual_caption.strip():
            base_template = manual_caption
        elif channel_specific_template and channel_specific_template.strip():
            base_template = channel_specific_template
        else:
            base_template = global_default
        
        # جایگذاری متغیرها
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

# --- Device Management API ---
@admin_bp.route('/api/admin/device/link', methods=['POST'])
def link_device_channel():
    """اتصال یک دیوایس به یک کانال خاص"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    token = data.get('token')
    channel_id = data.get('channel_id')
    target_channel = channel_id if channel_id else None
    
    db = get_db()
    db.execute("UPDATE sessions SET linked_channel_id = ? WHERE token = ?", (target_channel, token))
    db.commit()
    return jsonify({'status': 'success'})

# --- Settings API ---
@admin_bp.route('/api/admin/settings/update', methods=['POST'])
def update_settings():
    """بروزرسانی تنظیمات گلوبال (پیش‌فرض)"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    db = get_db()
    is_enabled = 1 if data.get('enabled') else 0
    db.execute("UPDATE settings SET auto_broadcast_channel_id=?, default_caption=?, is_auto_broadcast_enabled=? WHERE id=1",
               (data.get('channel_id'), data.get('caption'), is_enabled))
    db.commit()
    return jsonify({'status': 'success'})

# --- Channel Management APIs ---

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

# --- 🔥 Channel Custom Rules (Caption Template) API ---
@admin_bp.route('/api/admin/channels/update_template', methods=['POST'])
def update_channel_template():
    """ذخیره کپشن اختصاصی برای هر کانال"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    chat_id = data.get('chat_id')
    template = data.get('template')
    
    db = get_db()
    db.execute("UPDATE channels SET caption_template = ? WHERE chat_id = ?", (template, chat_id))
    db.commit()
    return jsonify({'status': 'success'})