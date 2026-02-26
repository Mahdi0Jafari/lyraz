# core/routes/auth.py

from flask import Blueprint, jsonify, request, render_template, send_from_directory
from core.models import get_db
from core.config import Config
import secrets
import string
import logging

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

def generate_hub_token(length=7):
    """
    تولید توکن ۷ کاراکتری (Base36 - حروف کوچک و اعداد)
    ظرفیت: حدود ۷۸ میلیارد ترکیب یکتا.
    ایده‌آل برای تایپ کردن با کیبورد تلویزیون و لینک‌های کوتاه.
    """
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# --- ۱. صفحه واسط اتصال (Landing Page) ---
@auth_bp.route('/connect/<token>')
def connect_landing(token):
    """
    وقتی کاربر QR Code روی تلویزیون را اسکن می‌کند، به این صفحه می‌آید.
    هدایت کاربر به ربات تلگرام به همراه توکن هاب.
    """
    # ساخت لینک عمیق (Deep Link) برای شروع ربات با توکن هاب
    # مثال: https://t.me/LyrazMusicBot?start=session_a7k9p2x
    telegram_link = f"https://t.me/{Config.BOT_USERNAME}?start=session_{token}"
    
    return render_template('connect.html', telegram_link=telegram_link)

# --- ۲. API شروع هاب (توسط تلویزیون/کلاینت صدا زده می‌شود) ---
@auth_bp.route('/api/auth/init', methods=['POST'])
def init_session():
    """
    ایجاد یک هاب جدید (Live Hub) برای دستگاه پخش‌کننده.
    یک توکن ۷ رقمی می‌سازد و به عنوان سشن دائمی ثبت می‌کند.
    """
    token = generate_hub_token()
    db = get_db()
    
    try:
        # در معماری V4، هاب‌ها به صورت پیش‌فرض دائمی (is_persistent=1) ثبت می‌شوند
        db.execute(
            "INSERT INTO sessions (token, status, is_persistent) VALUES (?, 'waiting', 1)", 
            (token,)
        )
        db.commit()
        
        bot_link = f"https://t.me/{Config.BOT_USERNAME}?start=session_{token}"
        
        return jsonify({
            'status': 'success',
            'token': token,
            'url': bot_link 
        })
    except Exception as e:
        logger.error(f"Hub Initialization Error: {e}")
        return jsonify({'status': 'error', 'message': 'Failed to initialize hub.'}), 500

# --- ۳. API بررسی وضعیت (Polling اولیه تا زمان لاگین) ---
@auth_bp.route('/api/auth/check/<token>', methods=['GET'])
def check_session_status(token):
    """
    پلیرها قبل از اینکه به SSE وصل شوند، این مسیر را چک می‌کنند 
    تا ببینند ادمین دستگاه را در تلگرام رجیستر کرده است یا خیر.
    """
    db = get_db()
    
    query = """
        SELECT s.status, s.admin_id, s.device_name, u.first_name, u.telegram_id 
        FROM sessions s
        LEFT JOIN users u ON s.admin_id = u.id
        WHERE s.token = ?
    """
    sess = db.execute(query, (token,)).fetchone()
    
    # اگر توکن در دیتابیس نبود
    if not sess:
        return jsonify({'status': 'expired'}), 404
        
    # اگر وضعیت active شده بود (ادمین استارت زده است)
    if sess['status'] == 'active' and sess['admin_id']:
        return jsonify({
            'status': 'active',
            'admin': {
                'id': sess['admin_id'],
                'name': sess['first_name'],
                'telegram_id': sess['telegram_id'],
                # نمایش نام شخصی‌سازی شده هاب یا نام صاحب آن
                'device_display_name': sess['device_name'] or f"{sess['first_name']}'s Hub"
            }
        })
        
    # هنوز کسی اسکن و تایید نکرده است
    return jsonify({'status': 'waiting'})

# --- ۴. مسیر حیاتی PWA (Service Worker) ---
@auth_bp.route('/sw.js')
def serve_sw():
    """
    سرو کردن فایل Service Worker روی مسیر روت برای کنترل کش (Offline Mode).
    """
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')