# app.py
import os
import logging
import sys
import signal
from core import create_app 

# تنظیمات لاگینگ با چگالی اطلاعات بالا (IQ 170+ Logging)
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - [%(levelname)s] - WEB_HUB - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# ایجاد اپلیکیشن با ساختار فکتوری
app = create_app()

def handle_exit(sig, frame):
    """مدیریت خروج امن برای جلوگیری از باز ماندن کانکشن‌های دیتابیس"""
    logger.info("📡 Shutting down Web Hub gracefully...")
    sys.exit(0)

# ثبت سیگنال‌های خروج (مخصوص داکر)
signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


if __name__ == '__main__':
    # این بخش فقط در حالت توسعه (Development) اجرا می‌شود
    logger.warning("🛠 Running in DEVELOPMENT mode (Flask Server).")
    
    # غیرفعال کردن reloader اگر باعث تداخل در WAL Mode دیتابیس شد
    app.run(
        debug=True, 
        host='0.0.0.0', 
        port=5000,
        use_reloader=True 
    )