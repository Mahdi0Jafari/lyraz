# core/services/admin_service.py

import os
import shutil
import logging
from collections import deque
from core.models import get_db
from core.config import Config

logger = logging.getLogger(__name__)

class AdminAnalyticsService:
    """
    مغز متفکر تحلیل اکوسیستم کاربران و سیستم مانیتورینگ متمرکز.
    این کلاس تمام کوئری‌های پیچیده (Join های سنگین) و I/O های سیستم را از روت‌ها مخفی می‌کند.
    """
    def __init__(self):
        # بررسی اسکیما در اینجا (زمان Import) انجام نمی‌شود تا خطای Application Context نگیریم.
        # فقط یک فلگ برای بهینه‌سازی سرعت تعریف می‌کنیم.
        self._schema_checked = False

    def _ensure_schema(self):
        """
        Auto-Migration: اضافه کردن ستون‌های سطح دسترسی و بن 
        به جدول کاربران به صورت Lazy (تنبل) و فقط یک‌بار در هر چرخه حیات.
        """
        if self._schema_checked:
            return

        db = get_db()
        try:
            db.execute("SELECT role FROM users LIMIT 1")
        except:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
            
        try:
            db.execute("SELECT is_banned FROM users LIMIT 1")
        except:
            db.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")

        try:
            db.execute("SELECT daily_quota FROM users LIMIT 1")
        except:
            db.execute("ALTER TABLE users ADD COLUMN daily_quota INTEGER DEFAULT 25")

        try:
            db.execute('''CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(referrer_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(referred_id) REFERENCES users(id) ON DELETE CASCADE
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);')
        except Exception as e:
            logger.error(f"Error ensuring referrals schema: {e}")

        try:
            db.execute('''CREATE TABLE IF NOT EXISTS user_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                source TEXT DEFAULT 'bot',
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_downloads_user ON user_downloads(user_id);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_downloads_track ON user_downloads(track_id);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_downloads_date ON user_downloads(downloaded_at);')

            # مایگریشن خودکار داده‌های قبلی (یک‌بار اجرا در صورت خالی بودن جدول)
            db.execute('''
                INSERT INTO user_downloads (user_id, track_id, source, downloaded_at)
                SELECT added_by, track_id, 'web_hub', created_at
                FROM playlist_items
                WHERE added_by IS NOT NULL AND track_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM user_downloads LIMIT 1);
            ''')
        except Exception as e:
            logger.error(f"Error ensuring user_downloads schema: {e}")

        try:
            for col, col_type, default_val in [
                ('crawler_enabled', 'BOOLEAN', '0'),
                ('crawler_schedule_hour', 'TEXT', "'04:00'"),
                ('crawler_max_tracks', 'INTEGER', '15'),
                ('crawler_source', 'TEXT', "'global_top_50'")
            ]:
                try:
                    db.execute(f"ALTER TABLE settings ADD COLUMN {col} {col_type} DEFAULT {default_val}")
                except:
                    pass

            db.execute('''CREATE TABLE IF NOT EXISTS ingestion_logs (
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
            db.execute('CREATE INDEX IF NOT EXISTS idx_ingestion_status ON ingestion_logs(status);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_ingestion_created ON ingestion_logs(created_at);')

            # ==========================================
            # 🌟 ARTIST VAULT CAMPAIGNS & TRACKS SCHEMA
            # ==========================================
            db.execute('''CREATE TABLE IF NOT EXISTS artist_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_name TEXT NOT NULL,
                spotify_id TEXT,
                spotify_url TEXT,
                avatar_url TEXT,
                target_channel_id TEXT,
                total_tracks INTEGER DEFAULT 0,
                completed_tracks INTEGER DEFAULT 0,
                status TEXT DEFAULT 'processing',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_campaigns_status ON artist_campaigns(status);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_campaigns_channel ON artist_campaigns(target_channel_id);')

            db.execute('''CREATE TABLE IF NOT EXISTS campaign_tracks (
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
            db.execute('CREATE INDEX IF NOT EXISTS idx_campaign_tracks_cid ON campaign_tracks(campaign_id);')
            db.execute('CREATE INDEX IF NOT EXISTS idx_campaign_tracks_status ON campaign_tracks(status);')
        except Exception as e:
            logger.error(f"Error ensuring ingestion schema: {e}")

        db.commit()
        
        # پس از یک بار بررسی موفق، فلگ را تغییر می‌دهیم تا کوئری‌های اضافی به دیتابیس زده نشود
        self._schema_checked = True

    # ==========================================
    # 📊 SYSTEM LOGGING ENGINE
    # ==========================================
    
    def get_system_logs(self, log_type='web', lines=150):
        """
        خواندن بهینه‌ی انتهای فایل‌های لاگ تولید شده توسط کانتینرهای داکر.
        استفاده از deque برای جلوگیری از بارگذاری فایل‌های حجیم در حافظه رم.
        """
        log_files = {
            'web': 'web.log',
            'bot': 'bot.log',
            'worker': 'worker.log'
        }
        
        filename = log_files.get(log_type, 'web.log')
        path = os.path.join(os.getcwd(), 'logs', filename)
        
        if not os.path.exists(path):
            return f"⚠️ System Initializing... Log file [{filename}] is empty or has not been created yet."
            
        try:
            with open(path, 'r', encoding='utf-8') as f:
                last_lines = deque(f, maxlen=lines)
                return "".join(last_lines)
        except Exception as e:
            logger.error(f"Error reading log file {path}: {e}")
            return f"❌ Critical Error reading log file: {str(e)}"

    # ==========================================
    # 👥 USER ANALYTICS ENGINE
    # ==========================================

    def get_dashboard_summary(self):
        """محاسبه آمار حیاتی (Hero Stats) برای هدر داشبورد"""
        self._ensure_schema()  # 🔥 فراخوانی ایمن در زمان دریافت ریکوئست
        db = get_db()
        
        # ۱. کل جامعه آماری
        total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        
        # ۲. نرخ تبدیل (کاربرانی که حداقل یک هاب ساخته‌اند)
        hubs_created = db.execute("SELECT COUNT(DISTINCT admin_id) FROM sessions").fetchone()[0]
        conversion_rate = round((hubs_created / total_users * 100), 1) if total_users > 0 else 0
        
        # ۳. فعالان امروز (محاسبه هوشمند بر اساس جدول رویدادهای دانلود)
        active_today = db.execute("""
            SELECT COUNT(DISTINCT user_id) 
            FROM user_downloads 
            WHERE datetime(downloaded_at, 'localtime') >= datetime('now', '-1 day', 'localtime')
        """).fetchone()[0]

        return {
            "total_users": total_users,
            "conversion_rate": conversion_rate,
            "active_today": active_today
        }

    def get_users_analytics(self, page=1, per_page=50, search_query=None, sort_by="tracks"):
        """
        واکشی کامل لیست کاربران همراه با آمار دقیق دانلودها و وضعیت مصرف دیتابیس.
        """
        self._ensure_schema()  # 🔥 فراخوانی ایمن
        db = get_db()
        offset = (page - 1) * per_page

        base_query = """
            SELECT 
                u.id, u.telegram_id, u.first_name, u.username, u.role, u.is_banned, u.daily_quota, u.created_at as join_date,
                (SELECT COUNT(*) FROM user_downloads ud WHERE ud.user_id = u.id) as total_tracks,
                (SELECT COUNT(DISTINCT pi.session_token) FROM playlist_items pi WHERE pi.added_by = u.id) as hubs_connected,
                (SELECT MAX(ud.downloaded_at) FROM user_downloads ud WHERE ud.user_id = u.id) as last_activity,
                (SELECT COALESCE(SUM(t.file_size), 0) FROM user_downloads ud JOIN tracks t ON ud.track_id = t.id WHERE ud.user_id = u.id) as total_storage_bytes,
                (SELECT COUNT(*) FROM referrals r WHERE r.referrer_id = u.id) as referral_count
            FROM users u
        """
        
        params = []
        where_clauses = []
        
        # موتور جستجوی پیشرفته
        if search_query:
            where_clauses.append("(u.first_name LIKE ? OR u.username LIKE ? OR u.telegram_id LIKE ?)")
            search_term = f"%{search_query}%"
            params.extend([search_term, search_term, search_term])
            
        if where_clauses:
            base_query += " WHERE " + " AND ".join(where_clauses)
            
        # موتور رتبه‌بندی و مرتب‌سازی
        if sort_by == "recent":
            base_query += " ORDER BY last_activity DESC NULLS LAST, join_date DESC"
        elif sort_by == "storage":
            base_query += " ORDER BY total_storage_bytes DESC NULLS LAST"
        elif sort_by == "referrals":
            base_query += " ORDER BY referral_count DESC, total_tracks DESC"
        else: # پیش‌فرض: بیشترین دانلود (tracks)
            base_query += " ORDER BY total_tracks DESC, hubs_connected DESC"
            
        # محاسبه تعداد کل رکوردها برای صفحه‌بندی (Pagination)
        count_query = f"SELECT COUNT(*) FROM users u {'WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''}"
        total_records = db.execute(count_query, params).fetchone()[0]
        total_pages = (total_records + per_page - 1) // per_page
        
        # اعمال Limit و Offset
        base_query += " LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        
        users_data = db.execute(base_query, params).fetchall()
        
        # پردازش نهایی داده‌ها (استخراج حجم مگابایت و تعیین وضعیت Badge)
        processed_users = []
        for row in users_data:
            user_dict = dict(row)
            
            # تبدیل بایت به مگابایت
            storage = user_dict.get('total_storage_bytes') or 0
            user_dict['storage_mb'] = round(storage / (1024 * 1024), 2)
            
            # نشان افتخار برای بیش از 50 آهنگ
            user_dict['is_power_user'] = True if user_dict['total_tracks'] >= 50 else False
            
            processed_users.append(user_dict)
            
        return {
            "users": processed_users,
            "total_pages": total_pages,
            "current_page": page,
            "total_records": total_records
        }

    def update_user_status(self, target_id, action, value):
        """کنترلر امنیتی: تغییر نقش (User/Pro/Admin) یا مسدودسازی (Ban)"""
        self._ensure_schema()  # 🔥 فراخوانی ایمن
        db = get_db()
        try:
            if action == 'role':
                # value: 'user', 'pro', 'admin'
                db.execute("UPDATE users SET role = ? WHERE id = ?", (value, target_id))
            elif action == 'ban':
                # value: 1 (ban) or 0 (unban)
                db.execute("UPDATE users SET is_banned = ? WHERE id = ?", (int(value), target_id))
            elif action == 'quota':
                db.execute("UPDATE users SET daily_quota = ? WHERE id = ?", (int(value), target_id))
            db.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update user {target_id}: {e}")
            return False

    def get_target_telegram_ids(self, selection_type='all', specific_ids=None):
        """
        استخراج لیست آیدی‌های عددی تلگرام برای ارسال پیام گروهی (Broadcast).
        به صورت خودکار کاربران بن شده را از لیست ارسال پیام حذف می‌کند.
        """
        self._ensure_schema()  # 🔥 فراخوانی ایمن
        db = get_db()
        
        if selection_type == 'specific' and specific_ids:
            placeholders = ','.join('?' for _ in specific_ids)
            query = f"SELECT telegram_id FROM users WHERE telegram_id IN ({placeholders}) AND is_banned = 0"
            rows = db.execute(query, specific_ids).fetchall()
        else: # 'all'
            rows = db.execute("SELECT telegram_id FROM users WHERE is_banned = 0").fetchall()
            
        return [row['telegram_id'] for row in rows if row['telegram_id']]

    def get_user_referral_network(self, user_id):
        """
        واکشی لیست تمام کاربرانی که توسط این کاربر دعوت شده‌اند برای نمایش در پنل ادمین.
        """
        self._ensure_schema()
        db = get_db()
        user = db.execute("SELECT id, first_name, username, telegram_id, role, daily_quota FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return None
            
        rows = db.execute("""
            SELECT u.id, u.telegram_id, u.first_name, u.username, u.role, r.created_at as join_date
            FROM referrals r
            JOIN users u ON r.referred_id = u.id
            WHERE r.referrer_id = ?
            ORDER BY r.created_at DESC
        """, (user_id,)).fetchall()
        
        return {
            "user": dict(user),
            "referrals": [dict(r) for r in rows],
            "total": len(rows)
        }

    # ==========================================
    # 🩺 SYSTEM HEALTH & MAINTENANCE ENGINE
    # ==========================================

    def get_system_health(self):
        """
        بررسی وضعیت زنده سلامت سرور، مصرف دیسک، کش دانلود و اعتبارسنجی کوکی یوتیوب.
        """
        self._ensure_schema()
        
        # ۱. اطلاعات مصرف دیسک سرور
        disk_path = Config.INSTANCE_PATH if os.path.exists(Config.INSTANCE_PATH) else os.getcwd()
        try:
            total_b, used_b, free_b = shutil.disk_usage(disk_path)
            disk_total_gb = round(total_b / (1024 ** 3), 1)
            disk_used_gb = round(used_b / (1024 ** 3), 1)
            disk_free_gb = round(free_b / (1024 ** 3), 1)
            disk_percent = round((used_b / total_b) * 100, 1) if total_b > 0 else 0
        except Exception as e:
            logger.error(f"Error reading disk usage: {e}")
            disk_total_gb, disk_used_gb, disk_free_gb, disk_percent = 0, 0, 0, 0

        # ۲. بررسی حجم کش موقت دانلودها (yt_cache)
        cache_path = Config.DOWNLOAD_CACHE_PATH
        cache_size_bytes = 0
        cache_file_count = 0
        if os.path.exists(cache_path):
            try:
                for entry in os.scandir(cache_path):
                    if entry.is_file():
                        cache_size_bytes += entry.stat().st_size
                        cache_file_count += 1
            except Exception as e:
                logger.error(f"Error reading cache directory: {e}")
        cache_size_mb = round(cache_size_bytes / (1024 * 1024), 2)

        # ۳. بررسی حجم فایل دیتابیس
        db_path = Config.DATABASE_URI
        db_size_bytes = 0
        if os.path.exists(db_path):
            db_size_bytes += os.path.getsize(db_path)
        wal_path = db_path + "-wal"
        if os.path.exists(wal_path):
            db_size_bytes += os.path.getsize(wal_path)
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

        # ۴. وضعیت اعتبار فایل کوکی یوتیوب
        cookie_path = Config.YT_COOKIES_PATH
        cookie_info = {
            "exists": False,
            "status": "missing",
            "label": "Missing / No Cookie",
            "color": "red",
            "details": "cookies.txt not found. YouTube may block datacenter requests."
        }
        if os.path.exists(cookie_path):
            try:
                with open(cookie_path, 'r', encoding='utf-8', errors='ignore') as cf:
                    content = cf.read()
                cookie_info["exists"] = True
                has_sid = "SID\t" in content or "\tSID" in content or "SID" in content
                has_hsid = "HSID" in content
                has_ssid = "SSID" in content
                has_login = "LOGIN_INFO" in content

                if has_sid and has_hsid and has_ssid:
                    cookie_info["status"] = "valid"
                    cookie_info["label"] = "Authenticated (Full Session)"
                    cookie_info["color"] = "emerald"
                    cookie_info["details"] = "Full Google authentication tags present (SID, HSID, SSID)."
                elif has_login or "PSID" in content:
                    cookie_info["status"] = "partial"
                    cookie_info["label"] = "Partial (Temporary)"
                    cookie_info["color"] = "yellow"
                    cookie_info["details"] = "Temporary cookies detected. May require periodic refresh."
                else:
                    cookie_info["status"] = "unauthenticated"
                    cookie_info["label"] = "Anonymous / Expired"
                    cookie_info["color"] = "red"
                    cookie_info["details"] = "No authentication tags detected in cookies.txt."
            except Exception as e:
                logger.error(f"Error checking cookies file: {e}")
                cookie_info["details"] = str(e)

        return {
            "disk": {
                "total_gb": disk_total_gb,
                "used_gb": disk_used_gb,
                "free_gb": disk_free_gb,
                "percent": disk_percent
            },
            "cache": {
                "size_mb": cache_size_mb,
                "files_count": cache_file_count
            },
            "database": {
                "size_mb": db_size_mb,
                "path": os.path.basename(db_path)
            },
            "cookies": cookie_info
        }

    def purge_cache(self):
        """
        پاکسازی ایمن پوشه کش فایل‌های موقت بدون دستکاری دیتابیس یا فایل‌های اصلی.
        """
        cache_path = Config.DOWNLOAD_CACHE_PATH
        deleted_files = 0
        freed_bytes = 0
        if os.path.exists(cache_path):
            for entry in os.scandir(cache_path):
                try:
                    if entry.is_file():
                        freed_bytes += entry.stat().st_size
                        os.remove(entry.path)
                        deleted_files += 1
                except Exception as e:
                    logger.warning(f"Could not delete cache file {entry.path}: {e}")
        
        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        return {
            "success": True,
            "deleted_files": deleted_files,
            "freed_mb": freed_mb
        }

    def optimize_database(self):
        """
        بهینه‌سازی دیتابیس SQLite و آزاد کردن فضای جدول‌های حذف‌شده (Vacuum & Optimize).
        """
        db = get_db()
        try:
            db.execute("PRAGMA optimize;")
            db.commit()
            return {"success": True, "message": "Database optimization completed successfully."}
        except Exception as e:
            logger.error(f"Database optimization failed: {e}")
            return {"success": False, "message": str(e)}

    def get_trending_analytics(self):
        """
        تحلیل هوشمند محبوب‌ترین قطعات موسیقی و پرطرفدارترین خوانندگان در کل پلتفرم.
        """
        self._ensure_schema()
        db = get_db()

        # ۱. تاپ ۵ موزیک پرطرفدار بر اساس تکرار در دانلودها
        top_tracks_rows = db.execute("""
            SELECT 
                t.id, t.title, t.performer, t.duration, t.file_size,
                COUNT(ud.id) as request_count
            FROM tracks t
            JOIN user_downloads ud ON t.id = ud.track_id
            GROUP BY t.id
            ORDER BY request_count DESC
            LIMIT 5
        """).fetchall()

        # ۲. تاپ ۵ آرتیست محبوب
        top_artists_rows = db.execute("""
            SELECT 
                t.performer,
                COUNT(ud.id) as total_requests,
                COUNT(DISTINCT t.id) as unique_tracks
            FROM tracks t
            JOIN user_downloads ud ON t.id = ud.track_id
            WHERE t.performer IS NOT NULL AND TRIM(t.performer) != '' AND LOWER(t.performer) != 'unknown'
            GROUP BY t.performer
            ORDER BY total_requests DESC
            LIMIT 5
        """).fetchall()

        top_tracks = []
        for r in top_tracks_rows:
            d = dict(r)
            size_bytes = d.get('file_size') or 0
            d['size_mb'] = round(size_bytes / (1024 * 1024), 1)
            top_tracks.append(d)

        top_artists = [dict(r) for r in top_artists_rows]

        return {
            "top_tracks": top_tracks,
            "top_artists": top_artists
        }

# سینگلتون برای استفاده در سراسر پروژه
admin_analytics = AdminAnalyticsService()