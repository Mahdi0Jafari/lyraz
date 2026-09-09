#!/usr/bin/env python3
# scripts/vault_repair.py
"""
موتور خودکار پایش و بازسازی ترک‌های نامنطبق مخزن (Vault Repair Engine)
ترک‌هایی از دیتابیس را که به علت محدودیت‌های سرچ قدیمی عنوان یا هویت متفاوتی
از فایل مخزن دارند شناسایی کرده و با اولویت پایین (Priority 5) به صورت امن اصلاح می‌کند.
"""

import os
import sys
import time
import sqlite3
import re
import logging
from difflib import SequenceMatcher

# افزودن ریشه پروژه به مسیر پایتون
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("vault_repair")

def get_db():
    conn = sqlite3.connect(Config.DATABASE_URI)
    conn.row_factory = sqlite3.Row
    return conn

def init_repair_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vault_repair_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_track_id INTEGER UNIQUE,
                title TEXT,
                artist TEXT,
                duration_seconds INTEGER,
                old_youtube_id TEXT,
                new_youtube_id TEXT,
                status TEXT DEFAULT 'pending', -- pending, resolving, queued, completed, failed
                error_msg TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_status ON vault_repair_jobs(status);")
        conn.commit()

def clean_str(s):
    if not s: return ''
    s = re.sub(r'\(.*?\)|\[.*?\]', '', s.lower())
    s = re.sub(r'[^a-zA-Z0-9\u0600-\u06FF\s]', ' ', s)
    return ' '.join(s.split())

def similarity(a, b):
    return SequenceMatcher(None, clean_str(a), clean_str(b)).ratio()

def scan_mismatches():
    """اسکن جدول‌های campaign_tracks و tracks و ثبت موارد نامنطبق در جدول vault_repair_jobs"""
    init_repair_db()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT ct.id, ct.title as ct_title, ct.artist as ct_artist, ct.duration_seconds as ct_dur,
                   t.title as t_title, t.performer as t_artist, t.duration as t_dur, ct.youtube_id
            FROM campaign_tracks ct
            JOIN tracks t ON ct.youtube_id = t.youtube_id
        """).fetchall()

        new_jobs = []
        for r in rows:
            ct_id, ct_t, ct_a, ct_d = r[0], r[1], r[2], r[3] or 0
            t_t, t_a, t_d = r[4], r[5], r[6] or 0
            old_vid = r[7]

            t_sim = similarity(ct_t, t_t)
            w1 = set(clean_str(ct_t).split())
            w2 = set(clean_str(t_t).split())
            has_common = bool(w1.intersection(w2))

            # معیار دقیق عدم انطباق
            is_acceptable = (t_sim >= 0.60) or (has_common and abs(ct_d - t_d) < 30) or has_common or (t_sim >= 0.40)
            if not is_acceptable:
                new_jobs.append((ct_id, ct_t, ct_a, ct_d, old_vid))

        if new_jobs:
            conn.executemany("""
                INSERT OR IGNORE INTO vault_repair_jobs (campaign_track_id, title, artist, duration_seconds, old_youtube_id, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, new_jobs)
            conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM vault_repair_jobs").fetchone()[0]
        logger.info(f"Scan complete. Total suspicious tracks registered in repair queue: {total} (New: {len(new_jobs)})")
        return total

def get_status_summary():
    """دریافت آمار زنده و درصد پیشرفت عملیات بازسازی مخزن"""
    init_repair_db()
    with get_db() as conn:
        counts = dict(conn.execute("""
            SELECT status, COUNT(*) FROM vault_repair_jobs GROUP BY status
        """).fetchall())

        total = sum(counts.values())
        completed = counts.get('completed', 0)
        queued = counts.get('queued', 0)
        resolving = counts.get('resolving', 0)
        pending = counts.get('pending', 0)
        failed = counts.get('failed', 0)

        percent = round((completed / total * 100), 1) if total > 0 else 100.0

        # دریافت ۳ آهنگ اخیراً اصلاح‌شده
        recent = [dict(r) for r in conn.execute("""
            SELECT title, artist, new_youtube_id, updated_at 
            FROM vault_repair_jobs 
            WHERE status = 'completed' 
            ORDER BY updated_at DESC LIMIT 3
        """).fetchall()]

        return {
            'total': total,
            'completed': completed,
            'queued': queued,
            'resolving': resolving,
            'pending': pending,
            'failed': failed,
            'in_progress': queued + resolving + pending,
            'percent': percent,
            'recent': recent
        }

def run_repair_step(limit=50):
    """
    اجرای یک دسته از تسک‌های بازسازی مخزن با اولویت استاندارد پس‌زمینه (Priority 5)
    """
    from core.services.youtube import YouTubeService
    from core.tasks import download_and_process_track
    yt_service = YouTubeService()

    init_repair_db()
    with get_db() as conn:
        jobs = [dict(r) for r in conn.execute("""
            SELECT * FROM vault_repair_jobs 
            WHERE status IN ('pending', 'failed')
            ORDER BY id ASC LIMIT ?
        """, (limit,)).fetchall()]

    if not jobs:
        logger.info("No pending repair jobs found.")
        return 0

    processed = 0
    for job in jobs:
        job_id = job['id']
        c_id = job['campaign_track_id']
        title = job['title']
        artist = job['artist']
        duration = job['duration_seconds']

        with get_db() as conn:
            conn.execute("UPDATE vault_repair_jobs SET status = 'resolving', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (job_id,))
            conn.commit()

        search_query = f"{artist} {title}"
        logger.info(f"🔍 Resolving authentic match for: {search_query} ({duration}s)")
        
        try:
            new_vid = yt_service.find_best_match(search_query, title=title, artist=artist, duration=duration)
            if not new_vid:
                with get_db() as conn:
                    conn.execute("UPDATE vault_repair_jobs SET status = 'failed', error_msg = 'No authentic match found', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (job_id,))
                    conn.commit()
                continue

            # بررسی آیا ویدیوی جدید از قبل در مخزن وجود دارد یا خیر
            with get_db() as conn:
                existing_vault = conn.execute("SELECT id FROM tracks WHERE youtube_id = ?", (new_vid,)).fetchone()
                
                if existing_vault:
                    # ترک واقعی در مخزن هست، فقط جدول کمپین را آپدیت کن
                    conn.execute("UPDATE campaign_tracks SET youtube_id = ?, status = 'completed' WHERE id = ?", (new_vid, c_id))
                    conn.execute("UPDATE vault_repair_jobs SET new_youtube_id = ?, status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_vid, job_id))
                    conn.commit()
                    logger.info(f"✨ Instant Vault Hit for '{title}' -> {new_vid}")
                else:
                    # باید در پس‌زمینه با اولویت پایین (Priority 5) دانلود و ذخیره شود
                    cur = conn.execute("""
                        INSERT INTO ingestion_logs (title, performer, youtube_id, source, status)
                        VALUES (?, ?, ?, 'vault_repair', 'queued')
                    """, (title, artist, new_vid))
                    log_id = cur.lastrowid
                    
                    conn.execute("UPDATE campaign_tracks SET youtube_id = ?, status = 'queued' WHERE id = ?", (new_vid, c_id))
                    conn.execute("UPDATE vault_repair_jobs SET new_youtube_id = ?, status = 'queued', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_vid, job_id))
                    conn.commit()

                    download_and_process_track(
                        video_id=new_vid,
                        title=title,
                        artist=artist,
                        user_id=0,
                        user_first_name="VaultRepair",
                        session_token=None,
                        chat_id=None,
                        message_id=None,
                        quality=Config.AUDIO_QUALITY,
                        duration=duration,
                        log_id=log_id,
                        priority=5  # اولویت پایین‌تر از کاربران زنده (کاربر عادی: 40، ادمین: 100)
                    )
                    logger.info(f"🚀 Queued download for '{title}' [{new_vid}] with priority 5")

            processed += 1
            time.sleep(1) # استراحت ۱ ثانیه‌ای برای جلوگیری از مسدودی
        except Exception as e:
            logger.error(f"Error repairing job {job_id}: {e}")
            with get_db() as conn:
                conn.execute("UPDATE vault_repair_jobs SET status = 'failed', error_msg = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (str(e), job_id))
                conn.commit()

    return processed

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--scan" in args:
        count = scan_mismatches()
        print(f"✅ Scan completed. {count} tracks in repair queue.")
    elif "--status" in args:
        s = get_status_summary()
        filled = int(s['percent'] / 10)
        bar = "█" * filled + "░" * (10 - filled)
        print("\n" + "="*45)
        print("🛠  گزارش وضعیت بازسازی مخزن (Vault Repair Status)")
        print("="*45)
        print(f"📦 کل قطعات مشکوک:        {s['total']}")
        print(f"✅ تکمیل و اصلاح‌شده:     {s['completed']} ({s['percent']}%)")
        print(f"⏳ در صف انتظار یا اجرا:   {s['in_progress']}")
        print(f"   ├─ در صف Huey:         {s['queued']}")
        print(f"   ├─ در حال جستجو:       {s['resolving']}")
        print(f"   └─ در انتظار نوبت:     {s['pending']}")
        print(f"❌ ناموفق:                 {s['failed']}")
        print(f"📊 درصد پیشرفت:           [{bar}] {s['percent']}%")
        print("="*45 + "\n")
    elif "--start" in args:
        scan_mismatches()
        print("🚀 Starting background repair worker loop...")
        while True:
            done = run_repair_step(limit=20)
            s = get_status_summary()
            if s['pending'] == 0:
                print(f"🎉 All pending jobs processed! Final stats: {s['completed']}/{s['total']} completed.")
                break
            time.sleep(5)
    else:
        print("Usage:")
        print("  python3 scripts/vault_repair.py --scan    # شناسایی و اسکن قطعات")
        print("  python3 scripts/vault_repair.py --status  # مشاهده وضعیت و درصد پیشرفت")
        print("  python3 scripts/vault_repair.py --start   # شروع عملیات اصلاح در پس‌زمینه")
