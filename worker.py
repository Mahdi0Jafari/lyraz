# worker.py
import sys
import os
import gc

# افزودن مسیر جاری به PATH پایتون (برای اطمینان از پیدا شدن پوشه core)
sys.path.append(os.getcwd())

from huey import signals
from huey.consumer import Consumer
from core.tasks import huey
from core.logger import setup_logger

# 🔥 اتصال به سیستم لاگینگ متمرکز (ذخیره در worker.log)
logger = setup_logger('WORKER', 'worker.log')

def get_current_rss_mb():
    """محاسبه دقیق حافظه مصرفی واقعی فرآیند ورکر در سیستم‌عامل برحسب مگابایت"""
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0 if sys.platform == 'darwin' else 1.0) / 1024.0
    except Exception:
        return 0.0

@huey.signal(signals.SIGNAL_COMPLETE, signals.SIGNAL_ERROR)
def on_task_finish(signal, task, exc=None):
    """جمع‌آوری بلادرنگ زباله‌های حافظه پس از اجرای هر تسک برای جلوگیری از فرگمنتیشن حافظه"""
    gc.collect()

class MemoryAwareConsumer(Consumer):
    """مصرف‌کننده هوشمند با دیده‌بان حافظه برای بازیافت خودکار در صورت عبور از سقف مجاز"""
    def __init__(self, *args, max_memory_mb=350, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_memory_mb = max_memory_mb

    def check_worker_health(self):
        healthy = super().check_worker_health()
        rss_mb = get_current_rss_mb()
        if rss_mb > self.max_memory_mb:
            self._logger.warning(
                f"🚨 [Memory Watchdog] Worker RSS memory reached {rss_mb:.1f} MB (exceeds limit {self.max_memory_mb} MB). "
                f"Initiating graceful restart to reclaim system memory..."
            )
            self.stop(graceful=True)
            # خروج صفر تا داکر کانتینر را بلافاصله با رم تازه و بدون نشتی بالا بیاورد
            os._exit(0)
        return healthy

def clean_stale_backlog():
    """پاکسازی صف از تسک‌های انباشته‌شده دوره‌ای یا حلقه‌های مرگبار هنگام استارت ورکر"""
    try:
        import sqlite3
        queue_file = os.path.join('instance', 'queue.db')
        if os.path.exists(queue_file):
            with sqlite3.connect(queue_file) as conn:
                # ۱. حذف تسک‌های انباشته‌شده دوره‌ای (Periodic backlog tasks)
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

                # ۲. پاکسازی قطعی قفل‌های باقیمانده از قبل در جدول kv تا تسک‌های دوره‌ای هرگز گیر نکنند
                cur_locks = conn.execute("DELETE FROM kv WHERE key LIKE '%.lock.%'")
                deleted_locks = cur_locks.rowcount

                conn.commit()
                logger.info(f"🧹 Backlog Cleanup: Removed {len(stale_periodic_ids)} periodic tasks and {deleted_locks} stale locks.")
    except Exception as e:
        logger.warning(f"Warning during queue backlog cleanup: {e}")

if __name__ == "__main__":
    clean_stale_backlog()
    logger.info("\n" + "="*40)
    logger.info("👷 Lyraz WORKER SERVICE INITIALIZED")
    logger.info("🚀 Listening for tasks from: core.tasks")
    logger.info("="*40 + "\n")

    try:
        # تنظیمات مصرف‌کننده بهینه با دیده‌بان حافظه و ۲ ورکر همزمان
        consumer = MemoryAwareConsumer(
            huey,
            workers=2,             # ۲ پردازشگر همزمان جهت جلوگیری از داغ شدن CPU و جهش ناگهانی رم
            worker_type='thread',  # استفاده از Thread
            check_worker_health=True,
            max_memory_mb=350
        )
        consumer.run()
        
    except KeyboardInterrupt:
        logger.warning("🛑 Worker Stopped manually.")
    except Exception as e:
        logger.error(f"💀 Worker Crashed: {e}", exc_info=True)