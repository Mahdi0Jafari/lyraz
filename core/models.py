# core/models.py
import sqlite3
import os
from flask import g
from core.config import Config

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(Config.DATABASE_URI)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    db_folder = os.path.dirname(Config.DATABASE_URI)
    if db_folder and not os.path.exists(db_folder):
        try:
            os.makedirs(db_folder)
            print(f"✅ Created database directory: {db_folder}")
        except OSError as e:
            print(f"❌ Error creating directory {db_folder}: {e}")
            return

    with sqlite3.connect(Config.DATABASE_URI) as conn:
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

        # 2. Tracks Table (Updated for YouTube Integration)
        # فیلد youtube_id اضافه شده تا از دانلود تکراری جلوگیری شود
        c.execute('''CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY,
            file_unique_id TEXT UNIQUE,
            file_id TEXT,
            title TEXT,
            performer TEXT,
            duration INTEGER,
            file_size INTEGER,
            thumb_id TEXT,
            youtube_id TEXT UNIQUE, -- 🔥 فیلد جدید: شناسه ویدیو یوتیوب برای کشینگ
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
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

        # 6. Settings Table
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            auto_broadcast_channel_id TEXT,
            default_caption TEXT,
            is_auto_broadcast_enabled BOOLEAN DEFAULT 0
        )''')
        
        # تنظیمات پیش‌فرض
        c.execute("INSERT OR IGNORE INTO settings (id, default_caption, is_auto_broadcast_enabled) VALUES (1, '🎧 {title} - {artist}\n👤 Sent by: {sender}', 0)")

        # 7. Lyrics Cache Table
        c.execute('''CREATE TABLE IF NOT EXISTS lyrics_cache (
            file_unique_id TEXT PRIMARY KEY,
            lyrics TEXT,
            source TEXT,
            updated_at INTEGER
        )''')
        
        conn.commit()
        print("✅ Database Schema Created (v5.0 - With YouTube Support)")