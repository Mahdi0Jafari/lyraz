# core/logger.py

import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(service_name, log_filename):
    """
    ماژول مرکزی لاگینگ (IQ 170+ Optimized).
    لاگ‌ها را همزمان در خروجی استاندارد داکر و فایل فیزیکی (Shared Volume) می‌نویسد.
    نوشتن آنی (Real-time) برای پشتیبانی از استریم SSE در پنل ادمین تضمین شده است.
    """
    # محاسبه مسیر مطلق ریشه پروژه برای جلوگیری از باگ‌های Relative Path در داکر
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    log_dir = os.path.join(base_dir, 'logs')
    
    # ساخت پوشه در صورت عدم وجود
    os.makedirs(log_dir, exist_ok=True)
    
    file_path = os.path.join(log_dir, log_filename)
    
    # فرمت استاندارد و ماشین-خوان
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    # فایل لاگ با قابلیت چرخش (حداکثر 5 مگابایت، نگهداری 2 نسخه پشتیبان)
    # delay=False باعث می‌شود فایل در لایه سیستم‌عامل بلافاصله ساخته/باز شود
    file_handler = RotatingFileHandler(
        file_path, 
        maxBytes=5*1024*1024, 
        backupCount=2, 
        encoding='utf-8',
        delay=False 
    )
    file_handler.setFormatter(formatter)
    
    # خروجی کنسول (برای نمایش در دستور docker logs)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    
    # جلوگیری از تکثیر لاگ‌ها (Duplicate Logs) در صورت فراخوانی مجدد ماژول
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    # مسدود کردن اسپم‌های بی‌ارزشِ کتابخانه‌های جانبی (کاهش نویز شبکه)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    return logger