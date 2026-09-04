# worker.py
import sys
import os

# افزودن مسیر جاری به PATH پایتون (برای اطمینان از پیدا شدن پوشه core)
sys.path.append(os.getcwd())

from huey.consumer import Consumer
from core.tasks import huey
from core.logger import setup_logger

# 🔥 اتصال به سیستم لاگینگ متمرکز (ذخیره در worker.log)
logger = setup_logger('WORKER', 'worker.log')

def clean_stale_backlog():
    """پاکسازی صف از تسک‌های انباشته‌شده دوره‌ای یا حلقه‌های مرگبار هنگام استارت ورکر"""
    try:
        import sqlite3
        queue_file = os.path.join('instance', 'queue.db')
        if os.path.exists(queue_file):
            with sqlite3.connect(queue_file) as conn:
                # ۱. حذف تسک‌های با اولویت مرگبار قدیمی
                cur = conn.execute("DELETE FROM task WHERE priority >= 12")
                deleted_p12 = cur.rowcount

                # ۲. حذف تسک‌های انباشته‌شده دوره‌ای (Periodic backlog tasks)
                cursor = conn.cursor()
                cursor.execute("SELECT id, data FROM task")
                rows = cursor.fetchall()
                stale_periodic_ids = []
                for tid, data in rows:
                    try:
                        task_obj = huey.serializer.deserialize(data)
                        tname = getattr(task_obj, 'name', '')
                        if 'check_autopilot_tick' in tname or 'check_crawler_schedule' in tname:
                            stale_periodic_ids.append(tid)
                    except Exception:
                        pass

                if stale_periodic_ids:
                    conn.executemany("DELETE FROM task WHERE id = ?", [(tid,) for tid in stale_periodic_ids])

                conn.commit()
                logger.info(f"🧹 Backlog Cleanup: Removed {deleted_p12} stuck tasks and {len(stale_periodic_ids)} stale periodic tasks.")
    except Exception as e:
        logger.warning(f"Warning during queue backlog cleanup: {e}")

if __name__ == "__main__":
    clean_stale_backlog()
    logger.info("\n" + "="*40)
    logger.info("👷 Lyraz WORKER SERVICE INITIALIZED")
    logger.info("🚀 Listening for tasks from: core.tasks")
    logger.info("="*40 + "\n")

    try:
        # تنظیمات مصرف‌کننده (Consumer)
        consumer = Consumer(
            huey,
            workers=3,             # ۳ پردازشگر همزمان بهینه و هماهنگ با محدودیت سرعت تلگرام برای خنک ماندن سرور
            worker_type='thread',  # استفاده از Thread
            check_worker_health=True
        )
        consumer.run()
        
    except KeyboardInterrupt:
        logger.warning("🛑 Worker Stopped manually.")
    except Exception as e:
        logger.error(f"💀 Worker Crashed: {e}", exc_info=True)