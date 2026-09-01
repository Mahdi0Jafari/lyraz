# core/models.py

import sqlite3
import os
import logging
from flask import g
from core.config import Config

logger = logging.getLogger(__name__)

def get_db():
    """
    اتصال به دیتابیس با تنظیمات بهینه برای هم‌روندی و یکپارچگی داده (V4 Architecture).
    """
    db = getattr(g, '_database', None)
    if db is None:
        try:
            db = g._database = sqlite3.connect(Config.DATABASE_URI)
            
            # 🔥 بهینه‌سازی‌های حیاتی مقیاس کلان (High-Scale 200k+ Pragmas)
            db.execute('PRAGMA journal_mode=WAL;')
            db.execute('PRAGMA synchronous=NORMAL;')
            db.execute('PRAGMA cache_size=-64000;')          # ۶۴ مگابایت کش رم اختصاصی
            db.execute('PRAGMA mmap_size=268435456;')        # ۲۵۶ مگابایت Memory-Mapped I/O
            db.execute('PRAGMA temp_store=MEMORY;')          # سورت و جداول موقت روی رم
            db.execute('PRAGMA busy_timeout=10000;')         # انتظار تا ۱۰ ثانیه برای جلوگیری از قفل
            db.execute('PRAGMA foreign_keys=ON;')
            
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
    ایجاد ساختار اولیه دیتابیس (Schema V4.7 - High-Scale 200k+ Engine with FTS5)
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
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;')
            conn.execute('PRAGMA cache_size=-64000;')
            conn.execute('PRAGMA mmap_size=268435456;')
            conn.execute('PRAGMA temp_store=MEMORY;')
            conn.execute('PRAGMA busy_timeout=10000;')
            conn.execute('PRAGMA foreign_keys=ON;')
            c = conn.cursor()
            
            # 1. Users Table (RBAC Architecture)
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                first_name TEXT,
                username TEXT,
                role TEXT DEFAULT 'user',
                current_session TEXT DEFAULT NULL,
                daily_quota INTEGER DEFAULT 25,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

            # 2. Tracks Table (Global Audio Assets)
            c.execute('''CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_unique_id TEXT UNIQUE,
                file_id TEXT,
                title TEXT,
                performer TEXT,
                duration INTEGER,
                file_size INTEGER,
                thumb_id TEXT,
                youtube_id TEXT UNIQUE,
                spotify_id TEXT UNIQUE,
                bitrate INTEGER DEFAULT 192,
                storage_message_id INTEGER DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            try:
                c.execute('ALTER TABLE tracks ADD COLUMN storage_message_id INTEGER DEFAULT NULL;')
            except Exception:
                pass

            # ایندکس‌های B-Tree برای مپینگ و جستجوی پرسرعت
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_unique ON tracks(file_unique_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_youtube ON tracks(youtube_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_spotify ON tracks(spotify_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_storage_msg ON tracks(storage_message_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_performer ON tracks(performer);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_tracks_created ON tracks(created_at DESC);')

            # 🔥 موتور جستجوی متنی فوق‌سریع SQLite FTS5 با توکنایزر چندزبانه و رتبه‌بندی BM25
            c.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
                title,
                performer,
                content='tracks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );''')

            # تریگرهای سه‌گانه همگام‌سازی بی‌درنگ FTS5
            c.execute('''CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
                INSERT INTO tracks_fts(rowid, title, performer) VALUES (new.id, new.title, new.performer);
            END;''')

            c.execute('''CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
                INSERT INTO tracks_fts(tracks_fts, rowid, title, performer) VALUES('delete', old.id, old.title, old.performer);
            END;''')

            c.execute('''CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
                INSERT INTO tracks_fts(tracks_fts, rowid, title, performer) VALUES('delete', old.id, old.title, old.performer);
                INSERT INTO tracks_fts(rowid, title, performer) VALUES (new.id, new.title, new.performer);
            END;''')

            # مهاجرت و ایندکس خودکار تمام آهنگ‌های قبلی موجود در دیتابیس
            c.execute('''
                INSERT OR IGNORE INTO tracks_fts(rowid, title, performer)
                SELECT id, title, performer FROM tracks;
            ''')
            
            # 3. Channels Table
            c.execute('''CREATE TABLE IF NOT EXISTS channels (
                chat_id TEXT PRIMARY KEY,
                title TEXT,
                username TEXT,
                caption_template TEXT DEFAULT NULL, 
                is_active BOOLEAN DEFAULT 1,
                added_by INTEGER
            )''')

            # 4. Sessions Table (The Live Hub)
            c.execute('''CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                admin_id INTEGER,
                status TEXT DEFAULT 'waiting',
                device_name TEXT DEFAULT NULL,
                device_agent TEXT,
                linked_channel_id TEXT DEFAULT NULL,
                is_persistent BOOLEAN DEFAULT 1,
                current_track_id INTEGER DEFAULT NULL,
                play_status TEXT DEFAULT 'stop',
                seek_position REAL DEFAULT 0.0,
                sync_timestamp REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(admin_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(linked_channel_id) REFERENCES channels(chat_id) ON DELETE SET NULL,
                FOREIGN KEY(current_track_id) REFERENCES tracks(id) ON DELETE SET NULL
            )''')
            
            # 5. Playlist Items Table (Queue Management)
            c.execute('''CREATE TABLE IF NOT EXISTS playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                track_id INTEGER,
                added_by INTEGER,
                session_token TEXT,
                is_played BOOLEAN DEFAULT 0,
                position INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                FOREIGN KEY(added_by) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(session_token) REFERENCES sessions(token) ON DELETE CASCADE
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_playlist_session ON playlist_items(session_token);')

            # 6. Settings Table
            c.execute('''CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                auto_broadcast_channel_id TEXT,
                default_caption TEXT,
                is_auto_broadcast_enabled BOOLEAN DEFAULT 0,
                crawler_enabled BOOLEAN DEFAULT 0,
                crawler_schedule_hour TEXT DEFAULT '04:00',
                crawler_max_tracks INTEGER DEFAULT 15,
                crawler_source TEXT DEFAULT 'global_top_50',
                autopilot_enabled BOOLEAN DEFAULT 0,
                autopilot_target_goal INTEGER DEFAULT 25000
            )''')
            for col, col_type, default_val in [
                ('crawler_enabled', 'BOOLEAN', '0'),
                ('crawler_schedule_hour', 'TEXT', "'04:00'"),
                ('crawler_max_tracks', 'INTEGER', '15'),
                ('crawler_source', 'TEXT', "'global_top_50'"),
                ('autopilot_enabled', 'BOOLEAN', '0'),
                ('autopilot_target_goal', 'INTEGER', '25000')
            ]:
                try:
                    c.execute(f"ALTER TABLE settings ADD COLUMN {col} {col_type} DEFAULT {default_val}")
                except sqlite3.OperationalError:
                    pass

            c.execute("INSERT OR IGNORE INTO settings (id, default_caption, is_auto_broadcast_enabled) VALUES (1, '🎧 {title} - {artist}\n👤 Sent by: {sender}', 0)")

            # 7. Lyrics Cache Table
            c.execute('''CREATE TABLE IF NOT EXISTS lyrics_cache (
                file_unique_id TEXT PRIMARY KEY,
                lyrics TEXT,
                source TEXT,
                updated_at INTEGER
            )''')

            # 8. Dynamic Spotify Radar Categories Table
            c.execute('''CREATE TABLE IF NOT EXISTS radar_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_key TEXT UNIQUE,
                title TEXT NOT NULL,
                subtitle TEXT,
                search_queries TEXT NOT NULL,
                is_default BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

            # Seed Default Radar Categories from JSON database
            try:
                import json
                from pathlib import Path
                json_path = Path(__file__).resolve().parent / "data" / "radar_categories.json"
                if json_path.exists():
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for cat in data.get("categories", []):
                        queries_str = ", ".join(cat.get("queries", []))
                        c.execute("""
                            INSERT OR REPLACE INTO radar_categories (category_key, title, subtitle, search_queries, is_default)
                            VALUES (?, ?, ?, ?, ?)
                        """, (cat.get("key"), cat.get("title"), cat.get("subtitle"), queries_str, 1 if cat.get("is_default") else 0))
            except Exception as e:
                logger.warning(f"Could not load radar_categories.json: {e}")

            # 8. Referrals Table (Viral Growth Engine)
            c.execute('''CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(referrer_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(referred_id) REFERENCES users(id) ON DELETE CASCADE
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);')

            # 9. User Downloads Table (Clean Many-to-Many Event Architecture)
            c.execute('''CREATE TABLE IF NOT EXISTS user_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                source TEXT DEFAULT 'bot',
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_user ON user_downloads(user_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_track ON user_downloads(track_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_downloads_date ON user_downloads(downloaded_at);')

            # 10. Ingestion Logs Table (Pre-warmer & Auto-Catalog Tracking)
            c.execute('''CREATE TABLE IF NOT EXISTS ingestion_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                performer TEXT NOT NULL,
                youtube_id TEXT,
                source TEXT DEFAULT 'crawler',
                status TEXT DEFAULT 'queued',
                error_msg TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_ingestion_status ON ingestion_logs(status);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_ingestion_created ON ingestion_logs(created_at);')

            # 11. Artist Vault Campaigns & Campaign Tracks Table
            c.execute('''CREATE TABLE IF NOT EXISTS artist_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_name TEXT NOT NULL,
                spotify_id TEXT,
                spotify_url TEXT,
                avatar_url TEXT,
                target_channel_id TEXT,
                hub_token TEXT DEFAULT NULL,
                total_tracks INTEGER DEFAULT 0,
                completed_tracks INTEGER DEFAULT 0,
                status TEXT DEFAULT 'processing',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            try:
                c.execute('ALTER TABLE artist_campaigns ADD COLUMN hub_token TEXT DEFAULT NULL;')
            except sqlite3.OperationalError:
                pass
            
            # پاکسازی خودکار رکوردهای تکراری کمپین‌ها (نگه‌داشتن کامل‌ترین رکورد)
            try:
                c.execute("""
                    DELETE FROM artist_campaigns 
                    WHERE id NOT IN (
                        SELECT MIN(id) 
                        FROM artist_campaigns 
                        GROUP BY spotify_id, LOWER(artist_name)
                    )
                """)
            except Exception:
                pass

            c.execute('CREATE INDEX IF NOT EXISTS idx_campaigns_status ON artist_campaigns(status);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_campaigns_channel ON artist_campaigns(target_channel_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_campaigns_hub_token ON artist_campaigns(hub_token);')

            c.execute('''CREATE TABLE IF NOT EXISTS campaign_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album_name TEXT,
                release_date TEXT,
                cover_url TEXT,
                duration_seconds INTEGER DEFAULT 0,
                spotify_url TEXT,
                youtube_id TEXT,
                status TEXT DEFAULT 'queued',
                error_msg TEXT,
                delivered_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (campaign_id) REFERENCES artist_campaigns(id) ON DELETE CASCADE
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_campaign_tracks_cid ON campaign_tracks(campaign_id);')
            c.execute('CREATE INDEX IF NOT EXISTS idx_campaign_tracks_status ON campaign_tracks(status);')
            
            conn.commit()
            print("✅ Database Schema V4.6 Optimized & Ready (WAL + Foreign Keys + Referrals + Downloads + Ingestion + Artist Hubs)")
            
    except sqlite3.Error as e:
        print(f"❌ Database Initialization Failed: {e}")