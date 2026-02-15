# core/routes/services/bot/database.py

import sqlite3
import logging
from core.config import Config

logger = logging.getLogger(__name__)

def get_db_connection():
    """ایجاد اتصال به دیتابیس با تنظیمات استاندارد"""
    conn = sqlite3.connect(Config.DATABASE_URI, check_same_thread=False)
    # این قابلیت باعث می‌شود خروجی‌ها شبیه دیکشنری باشند (دسترسی با نام ستون)
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
            # برای مقادیر تکی، استفاده از index [0] سریع‌تر است
            res = conn.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return res['id'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get User ID Error: {e}")
        return None

def update_user_session(telegram_id, session_token):
    """آپدیت کردن سشن فعلی کاربر"""
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE users SET current_session = ? WHERE telegram_id = ?", (session_token, telegram_id))
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Update Session Error: {e}")

def get_user_current_session(telegram_id):
    """دریافت توکن سشن فعال کاربر"""
    try:
        with get_db_connection() as conn:
            res = conn.execute("SELECT current_session FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return res['current_session'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get Current Session Error: {e}")
        return None

def get_session_info(token):
    """دریافت کامل اطلاعات یک سشن"""
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Session Info Error: {e}")
        return None

def get_active_sessions(user_id):
    """دریافت لیست سشن‌های فعال متعلق به یک ادمین"""
    try:
        with get_db_connection() as conn:
            return conn.execute("""
                SELECT * FROM sessions 
                WHERE admin_id = ? AND status = 'active' 
                ORDER BY created_at DESC
            """, (user_id,)).fetchall()
    except sqlite3.Error as e:
        logger.error(f"Get Active Sessions Error: {e}")
        return []

def set_device_name(token, name):
    """تغییر نام یک دیوایس"""
    try:
        with get_db_connection() as conn:
            conn.execute("UPDATE sessions SET device_name = ? WHERE token = ?", (name, token))
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
    """دریافت تمپلت کپشن برای یک کانال خاص"""
    try:
        with get_db_connection() as conn:
            res = conn.execute("SELECT caption_template FROM channels WHERE chat_id = ?", (chat_id,)).fetchone()
            return res['caption_template'] if res else None
    except sqlite3.Error as e:
        logger.error(f"Get Channel Template Error: {e}")
        return None

def get_track_by_youtube_id(video_id):
    """دریافت اطلاعات آهنگ بر اساس ID یوتیوب (برای سیستم کش)"""
    try:
        with get_db_connection() as conn:
            return conn.execute("SELECT * FROM tracks WHERE youtube_id = ?", (video_id,)).fetchone()
    except sqlite3.Error as e:
        logger.error(f"Get Track Error: {e}")
        return None