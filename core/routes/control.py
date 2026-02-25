# core/routes/control.py

import time
import json
import uuid
import logging
from flask import Blueprint, render_template, jsonify, request
from core.models import get_db
from core.sse import announcer

control_bp = Blueprint('control', __name__)
logger = logging.getLogger(__name__)

# ==========================================
# 📱 REMOTE CONTROL (Producer)
# ==========================================
@control_bp.route('/remote/<token>')
def remote_ui(token):
    """
    نمایش رابط کاربری موبایل (Remote Control) برای ادمین.
    """
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    
    if not session:
        return """
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; background:#121212; color:white; font-family:sans-serif; text-align:center;">
            <h1 style="color:#e74c3c; font-size:4rem; margin-bottom: 0;">🚫</h1>
            <h2>Hub Not Found</h2>
            <p style="color:#888;">This Hub is invalid or has been deleted.</p>
        </div>
        """, 404
    
    return render_template('mobile_control.html', token=token, session=session)


# ==========================================
# 🎵 QUEUE MANAGEMENT
# ==========================================
@control_bp.route('/api/control/queue/<token>')
def get_queue(token):
    """دریافت لیست صف پخش (Playlist) برای یک هاب خاص"""
    db = get_db()
    query = """
        SELECT 
            pi.id as item_id, 
            t.title, 
            t.performer, 
            t.file_unique_id, 
            t.duration,
            pi.is_played,
            pi.created_at,
            u.first_name as sender_name,
            u.username as sender_username
        FROM playlist_items pi
        JOIN tracks t ON pi.track_id = t.id
        LEFT JOIN users u ON pi.added_by = u.id 
        WHERE pi.session_token = ? 
        ORDER BY pi.id ASC
    """
    try:
        rows = db.execute(query, (token,)).fetchall()
        queue = []
        for row in rows:
            sender = row["sender_name"] or row["sender_username"] or "Unknown"
            queue.append({
                "item_id": row["item_id"],
                "title": row["title"],
                "performer": row["performer"],
                "file_unique_id": row["file_unique_id"],
                "duration": row["duration"],
                "is_played": row["is_played"],
                "sender_name": sender
            })
        return jsonify(queue)
    except Exception as e:
        logger.error(f"Queue Fetch Error: {e}")
        return jsonify({'error': 'Internal Server Error', 'details': str(e)}), 500


@control_bp.route('/api/control/mark_played', methods=['POST'])
def mark_played():
    """تغییر وضعیت آهنگ به 'پخش شده' در صف"""
    data = request.json
    if not data: return jsonify({'status': 'error', 'message': 'No data'}), 400

    token = data.get('token')
    unique_id = data.get('file_unique_id')
    db = get_db()
    
    try:
        track = db.execute("SELECT id FROM tracks WHERE file_unique_id = ?", (unique_id,)).fetchone()
        if track:
            db.execute("""
                UPDATE playlist_items 
                SET is_played = 1 
                WHERE session_token = ? AND track_id = ?
            """, (token, track['id']))
            db.commit()
            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'Track not found'}), 404
    except Exception as e:
        logger.error(f"Mark Played Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ==========================================
# ⚡️ THE STATE MACHINE (Command & Sync)
# ==========================================
@control_bp.route('/api/control/command', methods=['POST'])
def send_command():
    """
    دریافت فرمان از ریموت، آپدیت وضعیت دیتابیس و برودکست به تمام پلیرها.
    تجهیز شده به سیستم PTP (Precision Time Protocol) برای سینک همزمان دیوایس‌ها.
    """
    data = request.json
    if not data: return jsonify({'status': 'error'}), 400

    token = data.get('token')
    cmd = data.get('action') or data.get('command')   
    payload = data.get('payload') 
    
    if not token or not cmd:
        return jsonify({'status': 'error'}), 400

    db = get_db()
    logger.info(f"📱 Command: {cmd} | Payload: {payload} | Token: {token}")

    try:
        if cmd == 'remove' and payload:
            db.execute("DELETE FROM playlist_items WHERE id = ? AND session_token = ?", (payload, token))
            db.commit()

        # 🔥 PTP Sync Logic (اصلاح شده)
        # سرور فقط زمان دقیق دریافت درخواست را ثبت می‌کند (بدون تاخیر مصنوعی)
        server_now = time.time()
        action_id = str(uuid.uuid4()) 
        
        # استخراج وضعیت فعلی از دیتابیس برای محاسبه آفست کلاینت‌ها
        current_session = db.execute("SELECT seek_position, play_status, sync_timestamp FROM sessions WHERE token = ?", (token,)).fetchone()
        
        # پیش‌فرض‌ها در صورت نبود اطلاعات
        base_seek = current_session['seek_position'] if current_session else 0.0
        
        if cmd in ['play', 'pause', 'toggle', 'seek']:
            new_status = 'playing' if cmd == 'play' else ('paused' if cmd == 'pause' else None)
            
            # اگر فرمان پلی بود، محاسبه کن که از آخرین باری که وضعیت آپدیت شده (Pause بوده)، الان باید از چه ثانیه‌ای شروع کند
            if cmd == 'play' and current_session and current_session['play_status'] == 'paused':
                 pass # همان base_seek می‌ماند
                 
            if new_status:
                db.execute("UPDATE sessions SET play_status = ?, sync_timestamp = ? WHERE token = ?", (new_status, server_now, token))
            
            if cmd == 'seek':
                # اگر فرمان Seek بود، payload همان ثانیه درخواستی است
                base_seek = float(payload)
                db.execute("UPDATE sessions SET seek_position = ?, sync_timestamp = ? WHERE token = ?", (base_seek, server_now, token))
            
            db.commit()

        # برودکست فرمان با اطلاعات دقیق زمان و موقعیت آهنگ
        msg_data = {
            'type': 'command',
            'action': cmd,
            'payload': payload,             # این مقداری است که کاربر فرستاده (مثلا ثانیه 15 برای seek)
            'base_seek': base_seek,         # موقعیت تایید شده آهنگ در لحظه ثبت فرمان روی سرور
            'session_token': token,
            'action_id': action_id,           
            'server_now': server_now        # زمان دقیق سرور برای کالیبره کردن کلاینت‌ها
        }
        announcer.announce(f"data: {json.dumps(msg_data)}\n\n")
        
        return jsonify({
            'status': 'success', 
            'action_id': action_id
        })
    except Exception as e:
        logger.error(f"Command Error: {e}")
        return jsonify({'status': 'error'}), 500


@control_bp.route('/api/control/report_status', methods=['POST'])
def report_status():
    """
    پلیر اصلی این متد را مرتباً صدا می‌زند تا وضعیت دقیق خود را به سرور گزارش دهد.
    """
    data = request.json
    if not data: return jsonify({'status': 'error'}), 400

    token = data.get('token')
    unique_id = data.get('file_unique_id')
    is_playing = data.get('is_playing')
    current_time = data.get('current_time')
    
    db = get_db()
    sync_time = time.time()

    try:
        track = db.execute("SELECT id FROM tracks WHERE file_unique_id = ?", (unique_id,)).fetchone()
        track_id = track['id'] if track else None

        play_status = 'playing' if is_playing else 'paused'
        
        db.execute("""
            UPDATE sessions 
            SET play_status = ?, current_track_id = ?, seek_position = ?, sync_timestamp = ?, last_active_at = CURRENT_TIMESTAMP
            WHERE token = ?
        """, (play_status, track_id, current_time, sync_time, token))
        db.commit()

        msg = {
            'type': 'status_update',
            'session_token': token,
            'payload': {
                'file_unique_id': unique_id,
                'is_playing': is_playing,
                'current_time': current_time,
                'duration': data.get('duration')
            }
        }
        announcer.announce(f"data: {json.dumps(msg)}\n\n")
        
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Report Status Error: {e}")
        return jsonify({'status': 'error'}), 500