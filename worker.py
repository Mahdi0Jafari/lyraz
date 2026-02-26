# worker.py

import logging
import sys
import os

# افزودن مسیر جاری به PATH پایتون (برای اطمینان از پیدا شدن پوشه core)
sys.path.append(os.getcwd())

from huey.consumer import Consumer
from core.tasks import huey

# تنظیمات لاگینگ (نمایش در لاگ‌های داکر)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [WORKER] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    print("\n" + "="*40)
    print("👷 Lyraz WORKER SERVICE INITIALIZED")
    print("🚀 Listening for tasks from: core.tasks")
    print("="*40 + "\n")

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
        print("🛑 Worker Stopped manually.")
    except Exception as e:
        logger.error(f"💀 Worker Crashed: {e}", exc_info=True)