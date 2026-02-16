# core/routes/control.py

from flask import Blueprint, render_template, jsonify, request
from core.models import get_db
from core.sse import announcer
import json
import logging

# تعریف بلوپرینت
control_bp = Blueprint('control', __name__)
logger = logging.getLogger(__name__)

@control_bp.route('/remote/<token>')
def remote_ui(token):
    """
    نمایش رابط کاربری موبایل (Remote Control)
    با اسکن QR Code، کاربر به این صفحه هدایت می‌شود.
    """
    db = get_db()
    
    # بررسی اعتبار توکن سشن و دریافت اطلاعات (شامل device_name)
    session = db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    
    # اگر سشن وجود نداشت یا منقضی شده بود
    if not session:
        return """
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; background:#121212; color:white; font-family:sans-serif; text-align:center;">
            <h1 style="color:#e74c3c; font-size:4rem; margin-bottom: 0;">🚫</h1>
            <h2>Session Not Found</h2>
            <p style="color:#888;">This QR code is invalid or the session has expired.</p>
        </div>
        """, 404
    
    # ارسال آبجکت session به تمپلیت (برای نمایش نام دیوایس در هدر)
    return render_template('mobile_control.html', token=token, session=session)

@control_bp.route('/api/control/queue/<token>')
def get_queue(token):
    """
    API: دریافت لیست صف پخش (Playlist) برای یک سشن خاص.
    شامل نام آهنگ، خواننده، وضعیت پخش و نام کاربری که آهنگ را اضافه کرده است.
    """
    db = get_db()
    
    # کوئری برای گرفتن اطلاعات آهنگ + نام فرستنده
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
            # تعیین نام نمایشی فرستنده
            sender = row["sender_name"]
            if not sender:
                sender = row["sender_username"]
            if not sender:
                sender = "Unknown"

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

@control_bp.route('/api/control/command', methods=['POST'])
def send_command():
    """
    API: دریافت فرمان از موبایل و ارسال به تلویزیون (Player) از طریق SSE.
    فرمان‌ها شامل: play, pause, next, prev, remove, jump, toggle, seek, volume
    """
    data = request.json
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    token = data.get('token')
    
    # 🔥🔥🔥 اصلاحیه مهم: خواندن کلید 'action' که از موبایل ارسال می‌شود 🔥🔥🔥
    # اگر action نبود، command را چک کن (برای سازگاری عقب‌رو)
    cmd = data.get('action') or data.get('command')   
    payload = data.get('payload') 
    
    if not token or not cmd:
        return jsonify({'status': 'error', 'message': 'Missing token or action'}), 400

    db = get_db()
    
    logger.info(f"📱 Remote Command: {cmd} | Payload: {payload} | Token: {token}")

    try:
        # --- سناریو ۱: حذف آهنگ از صف ---
        if cmd == 'remove' and payload:
            # payload در اینجا باید item_id باشد
            db.execute(
                "DELETE FROM playlist_items WHERE id = ? AND session_token = ?", 
                (payload, token)
            )
            db.commit()
            logger.info(f"🗑️ Item {payload} removed from playlist.")

        # --- ارسال سیگنال به کلاینت‌های متصل (تلویزیون) ---
        msg_data = {
            'type': 'command',
            'action': cmd, # ارسال به عنوان action برای فرانت‌اند
            'payload': payload,
            'session_token': token
        }
        
        # فرمت استاندارد SSE
        announcer.announce(f"data: {json.dumps(msg_data)}\n\n")
        
        return jsonify({'status': 'success'})

    except Exception as e:
        logger.error(f"Command Processing Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@control_bp.route('/api/control/mark_played', methods=['POST'])
def mark_played():
    """
    API: تغییر وضعیت آهنگ به 'پخش شده'.
    """
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
            logger.info(f"✅ Track {unique_id} marked as played.")
            return jsonify({'status': 'success'})
        else:
            return jsonify({'status': 'error', 'message': 'Track not found'}), 404
        
    except Exception as e:
        logger.error(f"Mark Played Error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@control_bp.route('/api/control/report_status', methods=['POST'])
def report_status():
    """
    دریافت وضعیت لحظه‌ای از تلویزیون و پخش آن برای موبایل‌ها (سینک دوطرفه).
    """
    data = request.json
    if not data: return jsonify({'status': 'error'}), 400

    # ساخت پیام برای برودکست SSE به ریموت‌ها
    msg = {
        'type': 'status_update',
        'session_token': data.get('token'),
        'payload': {
            'file_unique_id': data.get('file_unique_id'),
            'is_playing': data.get('is_playing'),
            'current_time': data.get('current_time'),
            'duration': data.get('duration')
        }
    }
    
    announcer.announce(f"data: {json.dumps(msg)}\n\n")
    
    return jsonify({'status': 'ok'})