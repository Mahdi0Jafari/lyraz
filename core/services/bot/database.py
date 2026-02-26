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