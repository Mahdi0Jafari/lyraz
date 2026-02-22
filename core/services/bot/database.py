# core/services/bot/database.py

import sqlite3
import logging
from core.config import Config

logger = logging.getLogger(__name__)

def get_db_connection():
    """ایجاد اتصال به دیتابیس با تنظیمات بهینه (WAL + Foreign Keys)"""
    conn = sqlite3.connect(Config.DATABASE_URI, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA foreign_keys=ON;') # 🔥 حیاتی برای حفظ یکپارچگی داده‌ها در V4
    conn.row_factory = sqlite3.Row
    return conn

def bot_db_exec(query, args=()):
    """اجرای کوئری‌های نوشتن (INSERT, UPDATE, DELETE)"""
    try:
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute(query, args)
            conn.commit()
            return c.lastrowid
    except sqlite3.Error as e:
        logger.error(f"Database Execute Error: {e} | Query: {query}")
        return None

def get_user_id(telegram_id):
    """دریافت ID داخلی کاربر بر اساس ID تلگرام"""
    try:
        with get_db_connection() as conn:
            res = conn.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return res['id'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get User ID Error: {e}")
        return None

def get_user_role(telegram_id):
    """
    دریافت نقش کاربر (Admin, Pro, User) برای کنترل کیفیت دانلود و محدودیت‌ها
    """
    try:
        with get_db_connection() as conn:
            res = conn.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return res['role'] if res else 'user'
    except sqlite3.Error as e:
        logger.error(f"Get User Role Error: {e}")
        return 'user'

def update_user_session(telegram_id, session_token):
    """
    آپدیت کردن هاب فعال کاربر:
    در V4 به جای ذخیره توکن در جدول یوزر، زمان آخرین استفاده (last_active_at) 
    در جدول سشن‌ها بروزرسانی می‌شود.
    """
    try:
        internal_id = get_user_id(telegram_id)
        if internal_id:
            with get_db_connection() as conn:
                conn.execute(
                    "UPDATE sessions SET last_active_at = CURRENT_TIMESTAMP WHERE token = ? AND admin_id = ?", 
                    (session_token, internal_id)
                )
                conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Update Session Error: {e}")

def get_user_current_session(telegram_id):
    """
    دریافت توکن آخرین سشن فعال کاربر (بر اساس جدیدترین Timestamp)
    """
    try:
        with get_db_connection() as conn:
            query = """
                SELECT s.token 
                FROM sessions s
                JOIN users u ON s.admin_id = u.id
                WHERE u.telegram_id = ? AND s.status = 'active'
                ORDER BY s.last_active_at DESC 
                LIMIT 1
            """
            res = conn.execute(query, (telegram_id,)).fetchone()
            return res['token'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get Current Session Error: {e}")
        return None

def get_session_info(token):
    """دریافت کامل اطلاعات یک هاب (سشن)"""
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Session Info Error: {e}")
        return None

def get_active_sessions(user_id):
    """دریافت لیست هاب‌های فعال متعلق به یک کاربر (مرتب‌شده بر اساس زمان استفاده)"""
    try:
        with get_db_connection() as conn:
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
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE sessions SET device_name = ?, last_active_at = CURRENT_TIMESTAMP WHERE token = ?", (name, token))
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Set Device Name Error: {e}")

def get_settings():
    """دریافت تنظیمات کلی سیستم"""
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Settings Error: {e}")
        return None

def get_channel_template(chat_id):
    """دریافت تمپلیت کپشن برای یک کانال خاص"""
    try:
        with get_db_connection() as conn:
            res = conn.execute("SELECT caption_template FROM channels WHERE chat_id = ?", (chat_id,)).fetchone()
            return res['caption_template'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get Channel Template Error: {e}")
        return None

def get_track_by_youtube_id(video_id):
    """دریافت اطلاعات آهنگ بر اساس ID یوتیوب (لایه اول کش)"""
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT * FROM tracks WHERE youtube_id = ?", (video_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Track Error: {e}")
        return None

def get_track_by_spotify_id(spotify_id):
    """
    دریافت اطلاعات آهنگ بر اساس ID اسپاتیفای (لایه دوم کش - Zero-Latency Mapping)
    """
    if not spotify_id: return None
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT * FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Track by Spotify Error: {e}")
        return None

def update_hub_state(token, play_status, current_track_id, seek_position, sync_timestamp):
    """
    🔥 ذخیره وضعیت زنده هاب در دیتابیس (The State Machine Core)
    این تابع توسط APIهای کنترل فراخوانی می‌شود تا وضعیت پخش برای Multi-Screen Sync آپدیت شود.
    """
    try:
        with get_db_connection() as conn:
            conn.execute("""
                UPDATE sessions 
                SET play_status = ?, 
                    current_track_id = ?, 
                    seek_position = ?, 
                    sync_timestamp = ?,
                    last_active_at = CURRENT_TIMESTAMP
                WHERE token = ?
            """, (play_status, current_track_id, seek_position, sync_timestamp, token))
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Update Hub State Error: {e}")