# app.py
import os
import logging
import sys
from core import create_app 

# تنظیمات لاگ وب
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - [WEB_CONTAINER] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

app = create_app()

if __name__ == '__main__':
    # این بخش فقط برای تست دستی لوکال است
    # در پروداکشن، gunicorn این فایل را ایمپورت می‌کند و این بلوک اجرا نمی‌شود
    print("⚠️ Running in LOCAL mode. For Production use Gunicorn.")
    app.run(
        debug=True, 
        host='0.0.0.0', 
        port=5000
    )