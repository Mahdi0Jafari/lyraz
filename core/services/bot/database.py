# core/services/bot/database.py

import sqlite3
import logging
import threading
from core.config import Config

logger = logging.getLogger(__name__)

# 🚀 IQ 170+ Optimization: Thread-Local Connection Storage
# این متغیرِ ایزوله، تضمین می‌کند هر Thread (ساخته شده توسط asyncio.to_thread)
# کانکشن اختصاصی خودش را به دیتابیس داشته باشد و از باز و بسته شدن مداوم فایل جلوگیری شود.
_local = threading.local()

def get_db_connection():
    """
    ایجاد یا دریافت اتصال به دیتابیس با تنظیمات بهینه (WAL + Thread Caching).
    کاهش تاخیر I/O دیسک به صفر در درخواست‌های متوالی.
    """
    if not hasattr(_local, 'conn') or _local.conn is None:
        try:
            # timeout=10.0: جلوگیری قطعی از خطای Database Locked در محیط چند کانتینری
            conn = sqlite3.connect(Config.DATABASE_URI, check_same_thread=False, timeout=10.0)
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;')
            # اختصاص 64 مگابایت رم (RAM) برای کش دیتابیس جهت اجرای کوئری‌ها در حافظه موقت
            conn.execute('PRAGMA cache_size=-64000;') 
            conn.execute('PRAGMA foreign_keys=ON;')
            conn.row_factory = sqlite3.Row
            _local.conn = conn
        except sqlite3.Error as e:
            logger.error(f"Critical DB Connection Error: {e}")
            return None
    return _local.conn

def bot_db_exec(query, args=()):
    """اجرای کوئری‌های نوشتن (INSERT, UPDATE, DELETE) با مدیریت تراکنش ایمن"""
    conn = get_db_connection()
    if not conn: return None
    try:
        # کلمه کلیدی with تراکنش را آغاز و به صورت خودکار Commit یا در صورت خطا Rollback می‌کند
        with conn: 
            c = conn.cursor()
            c.execute(query, args)
            return c.lastrowid
    except sqlite3.Error as e:
        logger.error(f"Database Execute Error: {e} | Query: {query}")
        return None

def get_user_id(telegram_id):
    """دریافت ID داخلی کاربر بر اساس ID تلگرام"""
    conn = get_db_connection()
    if not conn: return None
    try:
        res = conn.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return res['id'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get User ID Error: {e}")
        return None

def get_user_role(telegram_id):
    """دریافت نقش کاربر (Admin, Pro, User) برای کنترل کیفیت دانلود و محدودیت‌ها"""
    conn = get_db_connection()
    if not conn: return 'user'
    try:
        res = conn.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return res['role'] if res else 'user'
    except sqlite3.Error as e:
        logger.error(f"Get User Role Error: {e}")
        return 'user'

def check_user_quota_status(telegram_id):
    """
    بررسی سهمیه روزانه کاربر و تعداد دانلودهای امروز (استراتژی رشد لایراز).
    خروجی: (allowed: bool, current_count: int, max_quota: int, role: str)
    """
    conn = get_db_connection()
    if not conn: return True, 0, 999, 'user'
    try:
        user = conn.execute("SELECT id, role, daily_quota FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not user:
            return True, 0, 25, 'user'
            
        role = user['role'] or 'user'
        if role in ['admin', 'pro']:
            return True, 0, 9999, role
            
        max_quota = user['daily_quota'] if user['daily_quota'] is not None else 25
        
        # تعداد دانلودهای امروز کاربر (بر اساس ساعت محلی سرور)
        res = conn.execute("""
            SELECT COUNT(*) FROM playlist_items 
            WHERE added_by = ? AND date(created_at, 'localtime') = date('now', 'localtime')
        """, (user['id'],)).fetchone()
        
        current_count = res[0] if res else 0
        allowed = (current_count < max_quota)
        return allowed, current_count, max_quota, role
    except sqlite3.Error as e:
        logger.error(f"Check Quota Error: {e}")
        return True, 0, 25, 'user'

def register_referral(referrer_tg_id, new_user_tg_id):
    """
    ثبت ریفرال با اعتبارسنجی‌های ضد تقلب و پاداش دوطرفه (Double-Sided Viral Loop):
    ۱. جلوگیری از دعوت خود (Self-invite).
    ۲. قفل عدم تکرار (هر کاربر فقط یک‌بار می‌تواند دعوت شود).
    ۳. پاداش معرف: +۲۵ سهمیه روزانه به ازای هر دوست (۳ دوست = Pro نامحدود).
    ۴. پاداش دعوت‌شده: +۵ سهمیه خوش‌آمدگویی.
    """
    if referrer_tg_id == new_user_tg_id:
        return False, 0, False, None

    conn = get_db_connection()
    if not conn: return False, 0, False, None

    try:
        with conn:
            # ۱. اطلاعات معرف
            referrer = conn.execute(
                "SELECT id, role, daily_quota, first_name FROM users WHERE telegram_id = ?", 
                (referrer_tg_id,)
            ).fetchone()
            if not referrer:
                return False, 0, False, None

            # ۲. اطلاعات کاربر جدید
            new_user = conn.execute(
                "SELECT id, daily_quota FROM users WHERE telegram_id = ?", 
                (new_user_tg_id,)
            ).fetchone()
            if not new_user:
                return False, 0, False, None

            # ۳. بررسی اینکه آیا کاربر قبلاً توسط کسی دعوت شده؟
            existing = conn.execute(
                "SELECT id FROM referrals WHERE referred_id = ?", 
                (new_user['id'],)
            ).fetchone()
            if existing:
                return False, 0, False, None

            # ۴. ثبت رکورد دعوت
            conn.execute(
                "INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)", 
                (referrer['id'], new_user['id'])
            )

            # ۵. محاسبه تعداد کل دعوت‌های معرف
            count_res = conn.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", 
                (referrer['id'],)
            ).fetchone()
            total_refs = count_res[0] if count_res else 1

            became_pro = False
            if total_refs >= 3 and referrer['role'] != 'admin':
                conn.execute("UPDATE users SET role = 'pro' WHERE id = ?", (referrer['id'],))
                became_pro = True
            else:
                cur_quota = referrer['daily_quota'] if referrer['daily_quota'] is not None else 25
                conn.execute("UPDATE users SET daily_quota = ? WHERE id = ?", (cur_quota + 25, referrer['id']))

            # ۶. پاداش کاربر دعوت‌شده (+۵ دانلود هدیه)
            new_cur_q = new_user['daily_quota'] if new_user['daily_quota'] is not None else 25
            conn.execute("UPDATE users SET daily_quota = ? WHERE id = ?", (new_cur_q + 5, new_user['id']))

            return True, total_refs, became_pro, referrer['first_name']
    except sqlite3.Error as e:
        logger.error(f"Register Referral Error: {e}")
        return False, 0, False, None

def get_user_referral_stats(telegram_id):
    """دریافت تعداد دعوت‌های موفق، سهمیه فعلی و نقش کاربر"""
    conn = get_db_connection()
    if not conn: return 0, 25, 'user'
    try:
        user = conn.execute("SELECT id, role, daily_quota FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if not user: return 0, 25, 'user'
        res = conn.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user['id'],)).fetchone()
        count = res[0] if res else 0
        quota = user['daily_quota'] if user['daily_quota'] is not None else 25
        return count, quota, user['role'] or 'user'
    except sqlite3.Error as e:
        logger.error(f"Get Referral Stats Error: {e}")
        return 0, 25, 'user'

def update_user_session(telegram_id, session_token):
    """
    🔥 آپدیت کردن هاب فعال کاربر (Collaborative Hub Logic):
    با استفاده از کانتکست منیجر (with conn)، هر دو آپدیت در یک تراکنشِ واحد (Atomic)
    اجرا می‌شوند تا سرعت بالا برود و احتمال از دست رفتن داده صفر شود.
    """
    conn = get_db_connection()
    if not conn: return
    try:
        with conn:
            conn.execute(
                "UPDATE users SET current_session = ? WHERE telegram_id = ?", 
                (session_token, telegram_id)
            )
            conn.execute(
                "UPDATE sessions SET last_active_at = CURRENT_TIMESTAMP WHERE token = ?", 
                (session_token,)
            )
    except sqlite3.Error as e:
        logger.error(f"Update Session Error: {e}")

def get_user_current_session(telegram_id):
    """دریافت هاب فعالِ کاربر (از پروفایل شخصی، نه از جدول سشن‌ها)"""
    conn = get_db_connection()
    if not conn: return None
    try:
        res = conn.execute(
            "SELECT current_session FROM users WHERE telegram_id = ?", 
            (telegram_id,)
        ).fetchone()
        return res['current_session'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get Current Session Error: {e}")
        return None

def get_session_info(token):
    """دریافت کامل اطلاعات یک هاب (سشن)"""
    conn = get_db_connection()
    if not conn: return None
    try:
        return conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Session Info Error: {e}")
        return None

def get_active_sessions(user_id):
    """دریافت لیست هاب‌های فعال متعلق به یک کاربر (که او ادمین آن‌هاست)"""
    conn = get_db_connection()
    if not conn: return []
    try:
        return conn.execute("""
            SELECT * FROM sessions 
            WHERE admin_id = ? AND status = 'active' 
            ORDER BY last_active_at DESC
        """, (user_id,)).fetchall()
    except sqlite3.Error as e:
        logger.error(f"Get Active Sessions Error: {e}")
        return []

def set_device_name(token, name):
    """تغییر نام یک هاب/دیوایس"""
    conn = get_db_connection()
    if not conn: return
    try:
        with conn:
            conn.execute("UPDATE sessions SET device_name = ?, last_active_at = CURRENT_TIMESTAMP WHERE token = ?", (name, token))
    except sqlite3.Error as e:
        logger.error(f"Set Device Name Error: {e}")

def get_settings():
    """دریافت تنظیمات کلی سیستم"""
    conn = get_db_connection()
    if not conn: return None
    try:
        return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Settings Error: {e}")
        return None

def get_channel_template(chat_id):
    """دریافت تمپلیت کپشن برای یک کانال خاص"""
    conn = get_db_connection()
    if not conn: return None
    try:
        res = conn.execute("SELECT caption_template FROM channels WHERE chat_id = ?", (chat_id,)).fetchone()
        return res['caption_template'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get Channel Template Error: {e}")
        return None

def get_track_by_youtube_id(video_id):
    """دریافت اطلاعات آهنگ بر اساس ID یوتیوب (لایه اول کش)"""
    conn = get_db_connection()
    if not conn: return None
    try:
        return conn.execute("SELECT * FROM tracks WHERE youtube_id = ?", (video_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Track Error: {e}")
        return None

def get_track_by_spotify_id(spotify_id):
    """دریافت اطلاعات آهنگ بر اساس ID اسپاتیفای (لایه دوم کش - Zero-Latency Mapping)"""
    if not spotify_id: return None
    
    conn = get_db_connection()
    if not conn: return None
    try:
        return conn.execute("SELECT * FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Track by Spotify Error: {e}")
        return None

def update_hub_state(token, play_status, current_track_id, seek_position, sync_timestamp):
    """
    🔥 ذخیره وضعیت زنده هاب در دیتابیس (The State Machine Core)
    این تابع توسط APIهای کنترل فراخوانی می‌شود تا وضعیت پخش برای Multi-Screen Sync آپدیت شود.
    """
    conn = get_db_connection()
    if not conn: return
    try:
        with conn:
            conn.execute("""
                UPDATE sessions 
                SET play_status = ?, 
                    current_track_id = ?, 
                    seek_position = ?, 
                    sync_timestamp = ?,
                    last_active_at = CURRENT_TIMESTAMP
                WHERE token = ?
            """, (play_status, current_track_id, seek_position, sync_timestamp, token))
    except sqlite3.Error as e:
        logger.error(f"Update Hub State Error: {e}")