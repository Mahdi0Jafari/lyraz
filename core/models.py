# core/models.py

import sqlite3
import os
import logging
from flask import g
from core.config import Config

logger = logging.getLogger(__name__)

def get_db():
    """
    اتصال به دیتابیس با تنظیمات بهینه برای هم‌روندی (Concurrency).
    """
    db = getattr(g, '_database', None)
    if db is None:
        try:
            db = g._database = sqlite3.connect(Config.DATABASE_URI)
            
            # 🔥 بهینه‌سازی حیاتی: فعال‌سازی WAL Mode
            # این اجازه می‌دهد خواندن و نوشتن همزمان انجام شود بدون قفل شدن دیتابیس
            db.execute('PRAGMA journal_mode=WAL;')
            
            # تنظیم همگام‌سازی روی NORMAL برای تعادل بین امنیت و سرعت
            db.execute('PRAGMA synchronous=NORMAL;')
            
            db.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database: {e}")
            return None
    return db

def close_db(e=None):
    """بستن اتصال دیتابیس در پایان هر درخواست"""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """
    ایجاد ساختار اولیه دیتابیس (Schema)
    """
    db_folder = os.path.dirname(Config.DATABASE_URI)
    if db_folder and not os.path.exists(db_folder):
        try:
            os.makedirs(db_folder)
            print(f"✅ Created database directory: {db_folder}")
        except OSError as e:
            print(f"❌ Error creating directory {db_folder}: {e}")
            return

    try:
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            # فعال‌سازی WAL برای کانکشن اولیه
            conn.execute('PRAGMA journal_mode=WAL;')
            c = conn.cursor()
            
            # 1. Users Table
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                first_name TEXT,
                username TEXT,
                current_session TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

            # 2. Tracks Table
            c.execute('''CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY,
                file_unique_id TEXT UNIQUE,
                file_id TEXT,
                title TEXT,
                performer TEXT,
                duration INTEGER,
                file_size INTEGER,
                thumb_id TEXT,
                youtube_id TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            # 🔥 ایندکس برای جستجوی سریع آهنگ‌ها
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_unique ON tracks(file_unique_id);')
            
            # 3. Channels Table
            c.execute('''CREATE TABLE IF NOT EXISTS channels (
                chat_id TEXT PRIMARY KEY,
                title TEXT,
                username TEXT,
                caption_template TEXT DEFAULT NULL, 
                is_active BOOLEAN DEFAULT 1,
                added_by INTEGER
            )''')

            # 4. Sessions Table
            c.execute('''CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                admin_id INTEGER,
                status TEXT DEFAULT 'waiting',
                device_name TEXT DEFAULT NULL,
                device_agent TEXT,
                linked_channel_id TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(admin_id) REFERENCES users(id),
                FOREIGN KEY(linked_channel_id) REFERENCES channels(chat_id) ON DELETE SET NULL
            )''')
            
            # 5. Playlist Items Table
            c.execute('''CREATE TABLE IF NOT EXISTS playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                track_id INTEGER,
                added_by INTEGER,
                session_token TEXT,
                is_played BOOLEAN DEFAULT 0,
                position INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_id) REFERENCES users(id),
                FOREIGN KEY(track_id) REFERENCES tracks(id),
                FOREIGN KEY(added_by) REFERENCES users(id)
            )''')
            # 🔥 ایندکس حیاتی برای سرعت لود شدن لیست پخش
            c.execute('CREATE INDEX IF NOT EXISTS idx_playlist_session ON playlist_items(session_token);')

            # 6. Settings Table
            c.execute('''CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                auto_broadcast_channel_id TEXT,
                default_caption TEXT,
                is_auto_broadcast_enabled BOOLEAN DEFAULT 0
            )''')
            
            c.execute("INSERT OR IGNORE INTO settings (id, default_caption, is_auto_broadcast_enabled) VALUES (1, '🎧 {title} - {artist}\n👤 Sent by: {sender}', 0)")

            # 7. Lyrics Cache Table
            c.execute('''CREATE TABLE IF NOT EXISTS lyrics_cache (
                file_unique_id TEXT PRIMARY KEY,
                lyrics TEXT,
                source TEXT,
                updated_at INTEGER
            )''')
            
            conn.commit()
            print("✅ Database Schema Optimized & Ready (WAL Mode Enabled)")
            
    except sqlite3.Error as e:
        print(f"❌ Database Initialization Failed: {e}")