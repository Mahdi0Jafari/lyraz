# core/routes/admin.py

import os
import time
import math
import requests
import logging
from collections import deque
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, Response, send_file
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
    system_health = admin_analytics.get_system_health()
    trending_data = admin_analytics.get_trending_analytics()

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
        system_health=system_health,
        trending_data=trending_data,
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

# ==========================================
# 🩺 SYSTEM HEALTH & MAINTENANCE APIs
# ==========================================

@admin_bp.route('/api/admin/system/health', methods=['GET'])
def get_system_health_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    health = admin_analytics.get_system_health()
    return jsonify({'status': 'success', 'data': health})

@admin_bp.route('/api/admin/system/purge-cache', methods=['POST'])
def purge_system_cache_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    result = admin_analytics.purge_cache()
    return jsonify(result)

@admin_bp.route('/api/admin/system/optimize-db', methods=['POST'])
def optimize_db_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    result = admin_analytics.optimize_database()
    return jsonify(result)

@admin_bp.route('/api/admin/system/backup-db', methods=['GET'])
def backup_database_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db_path = Config.DATABASE_URI
    if not os.path.exists(db_path):
        return jsonify({'status': 'error', 'message': 'Database file not found'}), 404
    
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    download_filename = f"lyraz_backup_{timestamp}.db"
    return send_file(db_path, as_attachment=True, download_name=download_filename)

@admin_bp.route('/api/admin/users/update_quota', methods=['POST'])
def update_user_quota_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json
    target_id = data.get('user_id')
    quota = data.get('quota')
    
    if target_id is None or quota is None:
        return jsonify({'status': 'error', 'message': 'Missing user_id or quota'}), 400
        
    try:
        quota_int = int(quota)
        success = admin_analytics.update_user_status(target_id, 'quota', quota_int)
        if success:
            return jsonify({'status': 'success', 'quota': quota_int})
        return jsonify({'status': 'error', 'message': 'Update failed in database'}), 500
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid quota number'}), 400

@admin_bp.route('/api/admin/users/<int:user_id>/referrals', methods=['GET'])
def get_user_referrals_api(user_id):
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = admin_analytics.get_user_referral_network(user_id)
    if not data:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    return jsonify({'status': 'success', 'data': data})


# ==========================================
# ⚡️ CATALOG PRE-WARMER & QUEUE APIS
# ==========================================

@admin_bp.route('/api/admin/crawler/status', methods=['GET'])
def get_crawler_status_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    from core.services.crawler import crawler_service
    data = crawler_service.get_queue_and_stats()
    return jsonify({'status': 'success', 'data': data})

@admin_bp.route('/api/admin/crawler/trigger', methods=['POST'])
def trigger_crawler_chart_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json or {}
    chart = data.get('chart', 'global_top_50')
    limit = int(data.get('limit', 20))
    
    from core.services.crawler import crawler_service
    if chart == 'persian_trending':
        tracks = crawler_service.get_persian_trending(limit=limit * 2)
    else:
        chart_res = crawler_service.get_spotify_chart(chart)
        tracks = chart_res.get('tracks', [])
        
    result = crawler_service.ingest_tracks_one_by_one(tracks, source_label=f"trend_{chart}", max_limit=limit)
    return jsonify({'status': 'success', 'result': result})

@admin_bp.route('/api/admin/crawler/ingest_artist', methods=['POST'])
def ingest_artist_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json or {}
    artist = (data.get('artist') or '').strip()
    limit = int(data.get('limit', 10))
    
    if not artist:
        return jsonify({'status': 'error', 'message': 'Artist name is required'}), 400
        
    from core.services.crawler import crawler_service
    tracks = crawler_service.get_artist_top_tracks(artist, limit=limit)
    if not tracks:
        return jsonify({'status': 'error', 'message': f'No tracks found for {artist}'}), 404
        
    result = crawler_service.ingest_tracks_one_by_one(tracks, source_label=f"artist:{artist[:20]}", max_limit=limit)
    return jsonify({'status': 'success', 'result': result})

@admin_bp.route('/api/admin/crawler/ingest_link', methods=['POST'])
def ingest_link_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json or {}
    url = (data.get('url') or '').strip()
    
    if not url:
        return jsonify({'status': 'error', 'message': 'URL is required'}), 400
        
    from core.services.crawler import crawler_service
    tracks = crawler_service.parse_custom_link(url)
    if not tracks:
        return jsonify({'status': 'error', 'message': 'Could not parse link or collection is empty'}), 400
        
    result = crawler_service.ingest_tracks_one_by_one(tracks, source_label="custom_link", max_limit=50)
    return jsonify({'status': 'success', 'result': result})

@admin_bp.route('/api/admin/crawler/settings', methods=['POST'])
def update_crawler_settings_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json or {}
    
    enabled = 1 if data.get('enabled') else 0
    hour = data.get('hour', '04:00')
    max_tracks = int(data.get('max_tracks', 15))
    source = data.get('source', 'global_top_50')
    
    db = get_db()
    db.execute("""
        UPDATE settings SET 
            crawler_enabled = ?,
            crawler_schedule_hour = ?,
            crawler_max_tracks = ?,
            crawler_source = ?
        WHERE id = 1
    """, (enabled, hour, max_tracks, source))
    db.commit()
    return jsonify({'status': 'success'})

@admin_bp.route('/api/admin/crawler/clear_logs', methods=['POST'])
def clear_crawler_logs_api():
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db = get_db()
    db.execute("DELETE FROM ingestion_logs WHERE status IN ('completed', 'skipped')")
    db.commit()
    return jsonify({'status': 'success'})

@admin_bp.route('/api/admin/artist_hub/search', methods=['POST'])
def artist_hub_search_api():
    """جستجوی زنده خواننده در اسپاتیفای بر اساس نام"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    data = request.json or {}
    q = (data.get('q') or '').strip()
    if not q:
        return jsonify({'status': 'success', 'artists': []})

    from core.services.spotify_extractor import spotify_extractor
    try:
        results = spotify_extractor.search_artist(q)
        return jsonify({'status': 'success', 'artists': results})
    except Exception as e:
        logger.error(f"Artist search error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@admin_bp.route('/api/admin/artist_hub/preview', methods=['POST'])
def artist_hub_preview_api():
    """استخراج پیش‌نمایش آرتیست یا پلی‌لیست اسپاتیفای"""
    if not is_admin(): return jsonify({'status': 'error', 'message': 'Forbidden'}), 403
    data = request.json or {}
    url = (data.get('url') or '').strip()
    dedup = data.get('deduplicate', True)
    
    if not url:
        return jsonify({'status': 'error', 'message': 'Spotify URL or ID is required'}), 400

    from core.services.spotify_extractor import spotify_extractor
    try:
        item_type, item_id = spotify_extractor.parse_spotify_link(url)
        if item_type == 'artist':
            res = spotify_extractor.fetch_artist_discography(item_id, deduplicate_by_name=dedup)
            return jsonify({'status': 'success', 'type': 'artist', 'data': res})
        elif item_type == 'playlist':
            res = spotify_extractor.fetch_playlist_tracks(item_id)
            return jsonify({'status': 'success', 'type': 'playlist', 'data': res})
        else:
            return jsonify({'status': 'error', 'message': f"Extraction not supported for link type '{item_type}'. Please provide an Artist or Playlist URL."}), 400
    except Exception as e:
        logger.error(f"Artist Hub preview error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@admin_bp.route('/api/admin/artist_hub/ingest', methods=['POST'])
def artist_hub_ingest_api():
    """تزریق دسته‌ای آهنگ‌های آرتیست به صف ورکر با مقصد کانال اختصاصی"""
    if not is_admin(): return jsonify({'status': 'error', 'message': 'Forbidden'}), 403
    data = request.json or {}
    tracks = data.get('tracks', [])
    artist_name = data.get('artist_name', 'Various Artists')
    target_channel_id = data.get('target_channel_id') or None
    limit = int(data.get('limit', 0))

    if not tracks:
        return jsonify({'status': 'error', 'message': 'No tracks provided for ingestion'}), 400

    if limit > 0:
        tracks = tracks[:limit]

    from core.services.crawler import crawler_service
    from core.tasks import download_and_process_track
    import time
    
    queued_count = 0
    skipped_count = 0

    db = get_db()

    # ۱. ایجاد رکورد کمپین در جدول artist_campaigns (با کلید اصلی id)
    cur_camp = db.execute("""
        INSERT INTO artist_campaigns (artist_name, spotify_id, spotify_url, avatar_url, target_channel_id, total_tracks, completed_tracks, status)
        VALUES (?, ?, ?, ?, ?, ?, 0, 'processing')
    """, (
        artist_name,
        data.get('spotify_id', ''),
        data.get('spotify_url', ''),
        data.get('avatar_url', ''),
        target_channel_id,
        len(tracks)
    ))
    campaign_id = cur_camp.lastrowid
    db.commit()

    # ۲. ایجاد تسک در ورکر پس‌زمینه بدون مسدود کردن ریکوئست وب
    from core.tasks import ingest_artist_campaign_task
    ingest_artist_campaign_task(
        campaign_id=campaign_id,
        tracks=tracks,
        artist_name=artist_name,
        target_channel_id=target_channel_id
    )

    return jsonify({
        'status': 'success',
        'result': {
            'campaign_id': campaign_id,
            'queued': len(tracks),
            'total': len(tracks),
            'target_channel': target_channel_id
        }
    })

@admin_bp.route('/api/admin/artist_hub/campaigns', methods=['GET'])
def get_artist_campaigns_api():
    """واکشی لیست تمام کارت‌های کمپین آرتیست با آمار زنده و درصد پیشرفت"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db = get_db()
    rows = db.execute("""
        SELECT c.*, 
               (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = c.id AND status = 'completed') as live_completed,
               (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = c.id AND status = 'queued') as live_queued,
               (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = c.id AND status = 'downloading') as live_downloading,
               (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = c.id AND status = 'failed') as live_failed
        FROM artist_campaigns c
        ORDER BY c.created_at DESC
    """).fetchall()
    
    campaigns = []
    for r in rows:
        d = dict(r)
        total = d.get('total_tracks') or 0
        comp = d.get('live_completed') or 0
        d['progress_percent'] = round((comp / total) * 100) if total > 0 else 0
        campaigns.append(d)

    return jsonify({'status': 'success', 'campaigns': campaigns})

@admin_bp.route('/api/admin/artist_hub/campaign/<int:campaign_id>/tracks', methods=['GET'])
def get_campaign_tracks_api(campaign_id):
    """واکشی ریز وضعیت تک‌تک آهنگ‌های یک کمپین آرتیست برای مودال جزییات"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db = get_db()
    c_row = db.execute("SELECT * FROM artist_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not c_row:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404

    tracks = db.execute("""
        SELECT * FROM campaign_tracks 
        WHERE campaign_id = ? 
        ORDER BY id ASC
    """, (campaign_id,)).fetchall()

    return jsonify({
        'status': 'success',
        'campaign': dict(c_row),
        'tracks': [dict(t) for t in tracks]
    })

@admin_bp.route('/api/admin/artist_hub/campaign/<int:campaign_id>', methods=['DELETE'])
def delete_artist_campaign_api(campaign_id):
    """حذف کارت یک کمپین (همراه با cascade روی آهنگ‌های کمپین)"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db = get_db()
    db.execute("DELETE FROM campaign_tracks WHERE campaign_id = ?", (campaign_id,))
    db.execute("DELETE FROM artist_campaigns WHERE id = ?", (campaign_id,))
    db.commit()
    return jsonify({'status': 'success'})

@admin_bp.route('/api/admin/artist_hub/verify_channel', methods=['POST'])
def verify_channel_api():
    """بررسی دسترسی و ادمین بودن بات در کانال مورد نظر قبل از شروع کمپین"""
    if not is_admin(): return jsonify({'status': 'error', 'message': 'Forbidden'}), 403
    data = request.json or {}
    chat_id = str(data.get('channel_id', '')).strip()

    if not chat_id:
        return jsonify({'status': 'error', 'message': 'Channel ID is required'}), 400

    from core.tasks import get_bot_instance
    import asyncio

    async def _check_access():
        bot = get_bot_instance()
        async with bot:
            me = await bot.get_me()
            try:
                chat = await bot.get_chat(chat_id=chat_id)
            except Exception as e:
                return False, f"Channel not found or bot has not been added: {e}", None

            try:
                member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
                # بررسی وضعیت ادمین یا سازنده
                status = getattr(member, 'status', None)
                can_post = getattr(member, 'can_post_messages', True) # در سوپرگروه‌ها معمولاً True یا ادمین
                is_admin_member = status in ('administrator', 'creator')
                if not is_admin_member:
                    return False, f"Bot is present but NOT an Administrator in '{chat.title}'!", chat.title

                return True, "Verified", {
                    'title': chat.title,
                    'username': chat.username,
                    'chat_id': chat_id,
                    'is_admin': True,
                    'can_post': can_post
                }
            except Exception as e:
                return False, f"Could not check bot permissions: {e}", getattr(chat, 'title', chat_id)

    try:
        ok, msg, info = asyncio.run(_check_access())
        if ok:
            return jsonify({'status': 'success', 'verified': True, 'info': info})
        else:
            return jsonify({'status': 'error', 'verified': False, 'message': msg, 'channel_title': info}), 400
    except Exception as exc:
        logger.error(f"Channel verification error: {exc}")
        return jsonify({'status': 'error', 'verified': False, 'message': str(exc)}), 500

@admin_bp.route('/api/admin/artist_hub/campaign/<int:campaign_id>/retry_failed', methods=['POST'])
def retry_campaign_failed_api(campaign_id):
    """تلاش مجدد برای ارسال تمام آهنگ‌های خطا خورده یک کمپین"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db = get_db()
    c_row = db.execute("SELECT * FROM artist_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not c_row:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404

    target_channel = c_row['target_channel_id']
    failed_tracks = db.execute("""
        SELECT * FROM campaign_tracks 
        WHERE campaign_id = ? AND status = 'failed'
    """, (campaign_id,)).fetchall()

    if not failed_tracks:
        return jsonify({'status': 'success', 'message': 'No failed tracks to retry', 'retried': 0})

    from core.tasks import download_and_process_track
    for trk in failed_tracks:
        # تغییر وضعیت به queued
        db.execute("UPDATE campaign_tracks SET status = 'queued', error_msg = NULL WHERE id = ?", (trk['id'],))
        
        # ثبت در ingestion_logs
        cur = db.execute("""
            INSERT INTO ingestion_logs (title, performer, youtube_id, source, status)
            VALUES (?, ?, ?, ?, 'queued')
        """, (trk['title'], trk['artist'], trk['youtube_id'], f"retry:{c_row['artist_name'][:15]}"))
        log_id = cur.lastrowid

        download_and_process_track(
            video_id=trk['youtube_id'],
            title=trk['title'],
            artist=trk['artist'],
            user_id=0,
            user_first_name="ArtistHubRetry",
            session_token=None,
            chat_id=None,
            message_id=None,
            quality=None,
            cover_url=trk['cover_url'],
            duration=trk['duration_seconds'],
            log_id=log_id,
            target_channel_id=target_channel,
            priority=15
        )

    db.commit()
    return jsonify({'status': 'success', 'retried': len(failed_tracks)})


@admin_bp.route('/api/admin/artist_hub/campaign/<int:campaign_id>/create_hub', methods=['POST'])
def create_artist_hub_api(campaign_id):
    """ایجاد یا ویرایش هاب اختصاصی لایو برای آرتیست با امکان انتخاب اسلاگ سفارشی"""
    if not is_admin(): return jsonify({'status': 'error', 'message': 'Forbidden'}), 403
    import re
    import unicodedata
    
    db = get_db()
    c_row = db.execute("SELECT * FROM artist_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not c_row:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404

    data = request.json or {}
    custom_slug = (data.get('custom_slug') or '').strip()

    # ۱. تولید و پاکسازی اسلاگ
    if custom_slug:
        slug = re.sub(r'[^a-zA-Z0-9_\-]', '', custom_slug.lower())
    else:
        # ساخت اسلاگ پیش‌فرض از نام انگلیسی یا شناسه
        raw_name = c_row['artist_name'] or 'artist'
        # حذف کاراکترهای غیراستاندارد
        clean_slug = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_name.lower().replace(' ', '-'))
        if not clean_slug or len(clean_slug) < 2:
            clean_slug = f"artist-{campaign_id}"
        slug = clean_slug

    if not slug:
        return jsonify({'status': 'error', 'message': 'Invalid slug. Please use English letters, numbers, dashes or underscores.'}), 400

    # ۲. بررسی تداخل با سشن‌های موجود دیگر
    existing_session = db.execute("SELECT * FROM sessions WHERE token = ?", (slug,)).fetchone()
    if existing_session and c_row['hub_token'] != slug:
        # بررسی اینکه آیا این سشن متعلق به کمپین دیگری است یا خیر
        other_camp = db.execute("SELECT id, artist_name FROM artist_campaigns WHERE hub_token = ? AND id != ?", (slug, campaign_id)).fetchone()
        if other_camp:
            return jsonify({
                'status': 'error', 
                'message': f'Slug "{slug}" is already assigned to artist "{other_camp["artist_name"]}". Please choose a different slug.'
            }), 400

    # ۳. ثبت یا آپدیت سشن در جدول sessions
    hub_name = f"{c_row['artist_name']} Discography"
    db.execute("""
        INSERT INTO sessions (token, admin_id, status, device_name, is_persistent, play_status)
        VALUES (?, 1, 'active', ?, 1, 'stop')
        ON CONFLICT(token) DO UPDATE SET 
            status='active',
            device_name=excluded.device_name,
            is_persistent=1
    """, (slug, hub_name))

    # ۴. انتقال تمام آهنگ‌های تکمیل‌شده کمپین به جدول playlist_items این هاب
    completed_tracks = db.execute("""
        SELECT ct.*, t.id as global_track_id 
        FROM campaign_tracks ct
        JOIN tracks t ON ct.youtube_id = t.youtube_id
        WHERE ct.campaign_id = ? AND ct.status = 'completed'
        ORDER BY ct.id ASC
    """, (campaign_id,)).fetchall()

    synced_count = 0
    for idx, trk in enumerate(completed_tracks):
        existing_item = db.execute(
            "SELECT id FROM playlist_items WHERE session_token = ? AND track_id = ?", 
            (slug, trk['global_track_id'])
        ).fetchone()
        
        if not existing_item:
            db.execute("""
                INSERT INTO playlist_items (session_token, track_id, added_by, position, is_played)
                VALUES (?, ?, 1, ?, 0)
            """, (slug, trk['global_track_id'], idx))
            synced_count += 1

    # ۵. اتصال hub_token به رکورد کمپین
    db.execute("UPDATE artist_campaigns SET hub_token = ? WHERE id = ?", (slug, campaign_id))
    db.commit()

    return jsonify({
        'status': 'success',
        'hub_token': slug,
        'hub_url': f'/live/{slug}',
        'total_tracks': len(completed_tracks),
        'newly_synced': synced_count,
        'message': f'Live Hub created successfully for {c_row["artist_name"]}!'
    })


@admin_bp.route('/api/admin/artist_hub/campaign/<int:campaign_id>/sync_hub', methods=['POST'])
def sync_artist_hub_api(campaign_id):
    """سینک کردن آهنگ‌های جدیداً دانلود شده کمپین به پلی‌لیست هاب"""
    if not is_admin(): return jsonify({'status': 'error'}), 403
    db = get_db()
    c_row = db.execute("SELECT * FROM artist_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not c_row or not c_row['hub_token']:
        return jsonify({'status': 'error', 'message': 'Campaign or Hub not found'}), 404

    slug = c_row['hub_token']

    completed_tracks = db.execute("""
        SELECT ct.*, t.id as global_track_id 
        FROM campaign_tracks ct
        JOIN tracks t ON ct.youtube_id = t.youtube_id
        WHERE ct.campaign_id = ? AND ct.status = 'completed'
        ORDER BY ct.id ASC
    """, (campaign_id,)).fetchall()

    synced_count = 0
    for idx, trk in enumerate(completed_tracks):
        existing_item = db.execute(
            "SELECT id FROM playlist_items WHERE session_token = ? AND track_id = ?", 
            (slug, trk['global_track_id'])
        ).fetchone()
        
        if not existing_item:
            db.execute("""
                INSERT INTO playlist_items (session_token, track_id, added_by, position, is_played)
                VALUES (?, ?, 1, ?, 0)
            """, (slug, trk['global_track_id'], idx))
            synced_count += 1

    db.commit()
    return jsonify({
        'status': 'success',
        'hub_token': slug,
        'hub_url': f'/live/{slug}',
        'total_tracks': len(completed_tracks),
        'newly_synced': synced_count
    })