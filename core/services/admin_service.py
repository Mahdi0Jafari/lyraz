# core/services/admin_service.py

import logging
from core.models import get_db

logger = logging.getLogger(__name__)

class AdminAnalyticsService:
    """
    مغز متفکر تحلیل اکوسیستم کاربران.
    این کلاس تمام کوئری‌های پیچیده (Join های سنگین) را از روت‌ها مخفی می‌کند.
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
        db.commit()
        
        # پس از یک بار بررسی موفق، فلگ را تغییر می‌دهیم تا کوئری‌های اضافی به دیتابیس زده نشود
        self._schema_checked = True

    def get_dashboard_summary(self):
        """محاسبه آمار حیاتی (Hero Stats) برای هدر داشبورد"""
        self._ensure_schema()  # 🔥 فراخوانی ایمن در زمان دریافت ریکوئست
        db = get_db()
        
        # ۱. کل جامعه آماری
        total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        
        # ۲. نرخ تبدیل (کاربرانی که حداقل یک هاب ساخته‌اند)
        hubs_created = db.execute("SELECT COUNT(DISTINCT admin_id) FROM sessions").fetchone()[0]
        conversion_rate = round((hubs_created / total_users * 100), 1) if total_users > 0 else 0
        
        # ۳. فعالان امروز (محاسبه هوشمند بر اساس تایم‌زون سرور)
        active_today = db.execute("""
            SELECT COUNT(DISTINCT added_by) 
            FROM playlist_items 
            WHERE datetime(created_at, 'localtime') >= datetime('now', '-1 day', 'localtime')
        """).fetchone()[0]

        return {
            "total_users": total_users,
            "conversion_rate": conversion_rate,
            "active_today": active_today
        }

    def get_users_analytics(self, page=1, per_page=50, search_query=None, sort_by="tracks"):
        """
        واکشی کامل لیست کاربران همراه با 10 فیچر تحلیلی (Interdisciplinary Join).
        این کوئری ۳ جدول users، playlist_items و tracks را به هم می‌دوزد.
        """
        self._ensure_schema()  # 🔥 فراخوانی ایمن
        db = get_db()
        offset = (page - 1) * per_page

        base_query = """
            SELECT 
                u.id, u.telegram_id, u.first_name, u.username, u.role, u.is_banned, u.created_at as join_date,
                COUNT(pi.id) as total_tracks,
                COUNT(DISTINCT pi.session_token) as hubs_connected,
                MAX(pi.created_at) as last_activity,
                SUM(t.file_size) as total_storage_bytes
            FROM users u
            LEFT JOIN playlist_items pi ON u.id = pi.added_by
            LEFT JOIN tracks t ON pi.track_id = t.id
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
            
        base_query += " GROUP BY u.id"
        
        # موتور رتبه‌بندی و مرتب‌سازی
        if sort_by == "recent":
            base_query += " ORDER BY last_activity DESC NULLS LAST, join_date DESC"
        elif sort_by == "storage":
            base_query += " ORDER BY total_storage_bytes DESC NULLS LAST"
        else: # پیش‌فرض: بیشترین مشارکت (tracks)
            base_query += " ORDER BY total_tracks DESC, hubs_connected DESC"
            
        # محاسبه تعداد کل رکوردها برای صفحه‌بندی (Pagination)
        count_query = f"SELECT COUNT(*) FROM (SELECT u.id FROM users u {'WHERE ' + ' AND '.join(where_clauses) if where_clauses else ''} GROUP BY u.id)"
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

# سینگلتون برای استفاده در سراسر پروژه
admin_analytics = AdminAnalyticsService()