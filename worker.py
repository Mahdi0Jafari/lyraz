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

if __name__ == "__main__":
    logger.info("\n" + "="*40)
    logger.info("👷 Lyraz WORKER SERVICE INITIALIZED")
    logger.info("🚀 Listening for tasks from: core.tasks")
    logger.info("="*40 + "\n")

    try:
        # تنظیمات مصرف‌کننده (Consumer)
        consumer = Consumer(
            huey,
            workers=2,             # حداکثر ۲ دانلود همزمان (برای مدیریت پهنای باند سرور)
            worker_type='thread',  # استفاده از Thread (چون دانلود I/O است نه CPU)
            check_worker_health=True
        )
        consumer.run()
        
    except KeyboardInterrupt:
        logger.warning("🛑 Worker Stopped manually.")
    except Exception as e:
        logger.error(f"💀 Worker Crashed: {e}", exc_info=True)