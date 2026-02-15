# core/routes/auth.py

from flask import Blueprint, jsonify, request, render_template, send_from_directory
from core.models import get_db
from core.config import Config
import uuid

auth_bp = Blueprint('auth', __name__)

# --- ۱. صفحه واسط (Landing Page) ---
@auth_bp.route('/connect/<token>')
def connect_landing(token):
    """
    وقتی کاربر QR Code روی تلویزیون را اسکن می‌کند، به این صفحه می‌آید.
    این صفحه یک دکمه خوشگل نشان می‌دهد که کاربر را به ربات تلگرام می‌فرستد.
    """
    # ساخت لینک عمیق (Deep Link) برای شروع ربات با توکن سشن
    # مثال: https://t.me/FanusMusicBot?start=session_12345678
    telegram_link = f"https://t.me/{Config.BOT_USERNAME}?start=session_{token}"
    
    # رندر کردن قالب connect.html و ارسال لینک به آن
    return render_template('connect.html', telegram_link=telegram_link)


# --- ۲. API شروع سشن (توسط تلویزیون صدا زده می‌شود) ---
@auth_bp.route('/api/auth/init', methods=['POST'])
def init_session():
    """
    ایجاد یک سشن جدید برای نمایشگر (TV/Desktop).
    یک توکن می‌سازد و در دیتابیس ذخیره می‌کند.
    """
    # تولید توکن یکتا و کوتاه (۸ کاراکتر)
    token = str(uuid.uuid4())[:8]
    
    db = get_db()
    try:
        # ایجاد ردیف جدید در جدول sessions با وضعیت waiting
        db.execute("INSERT INTO sessions (token, status) VALUES (?, 'waiting')", (token,))
        db.commit()
        
        # لینک مستقیم ربات (برای استفاده‌های احتمالی، هرچند ما از صفحه connect استفاده می‌کنیم)
        bot_link = f"https://t.me/{Config.BOT_USERNAME}?start=session_{token}"
        
        return jsonify({
            'status': 'success',
            'token': token,
            'url': bot_link 
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- ۳. API بررسی وضعیت (توسط تلویزیون مدام صدا زده می‌شود) ---
@auth_bp.route('/api/auth/check/<token>', methods=['GET'])
def check_session_status(token):
    """
    تلویزیون هر ۲ ثانیه این را صدا می‌زند تا ببیند آیا کسی QR را اسکن کرده و در ربات Start زده است یا خیر.
    """
    db = get_db()
    
    # دریافت اطلاعات سشن به همراه نام ادمین (اگر وصل شده باشد)
    query = """
        SELECT s.*, u.first_name, u.telegram_id 
        FROM sessions s
        LEFT JOIN users u ON s.admin_id = u.id
        WHERE s.token = ?
    """
    sess = db.execute(query, (token,)).fetchone()
    
    # اگر توکن در دیتابیس نبود (منقضی یا اشتباه)
    if not sess:
        return jsonify({'status': 'expired'}), 404
        
    # اگر وضعیت active شده بود (یعنی ادمین در ربات دکمه استارت را زده)
    if sess['status'] == 'active' and sess['admin_id']:
        return jsonify({
            'status': 'active',
            'admin': {
                'id': sess['admin_id'],
                'name': sess['first_name'],
                'telegram_id': sess['telegram_id'],
                # نام دیوایس را برای تلویزیون می‌فرستیم
                'device_display_name': sess['device_name'] or sess['first_name']
            }
        })
        
    # اگر هنوز کسی وصل نشده
    return jsonify({'status': 'waiting'})


# --- 🔥 ۴. مسیر حیاتی PWA (Service Worker) ---
@auth_bp.route('/sw.js')
def serve_sw():
    """
    این تابع فایل sw.js را از پوشه static می‌خواند اما آن را 
    روی آدرس ریشه (yoursite.com/sw.js) سرو می‌کند.
    این کار برای اینکه سرویس‌ورکر بتواند کل سایت را کش کند الزامی است.
    """
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')