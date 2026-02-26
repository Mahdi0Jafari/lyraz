# core/logger.py

import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(service_name, log_filename):
    """
    ماژول مرکزی لاگینگ (IQ 170+ Optimized).
    لاگ‌ها را همزمان در خروجی استاندارد داکر و فایل فیزیکی (Shared Volume) می‌نویسد.
    """
    log_dir = os.path.join(os.getcwd(), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    file_path = os.path.join(log_dir, log_filename)
    
    # فرمت استاندارد و ماشین-خوان
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    # فایل لاگ با قابلیت چرخش (حداکثر 5 مگابایت، نگهداری 2 نسخه پشتیبان)
    file_handler = RotatingFileHandler(file_path, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # خروجی کنسول (برای دستور docker logs)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    
    # جلوگیری از تکرار لاگ‌ها در صورت فراخوانی مجدد
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    # غیرفعال کردن لاگ‌های مزاحم کتابخانه‌های جانبی
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    
    return logger