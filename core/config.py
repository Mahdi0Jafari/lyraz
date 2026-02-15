# core/config.py

import os
from dotenv import load_dotenv

# لود کردن فایل .env از مسیر اصلی پروژه
load_dotenv()

class Config:
    # --- تنظیمات امنیتی ---
    SECRET_KEY = os.getenv('SECRET_KEY', 'fanus-default-secret-key')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin')
    
    # --- تنظیمات تلگرام ---
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    BOT_USERNAME = os.getenv('BOT_USERNAME', 'naqoosbot')
    
    # 🔥 تنظیم جدید: آیدی کانال آرشیو موزیک
    # نکته: باید حتما با -100 شروع شود (فرمت کانال‌های تلگرام)
    STORAGE_CHANNEL_ID = os.getenv('STORAGE_CHANNEL_ID')
    
    # --- تنظیمات عمومی ---
    BASE_URL = os.getenv('BASE_URL', 'http://127.0.0.1:5000')
    
    # --- مسیرها و دیتابیس ---
    # مسیر پایه پوشه instance (محل ذخیره دیتابیس، لاگ‌ها و کوکی‌ها)
    # در داکر این مسیر به والیوم متصل است
    INSTANCE_PATH = os.path.join(os.getcwd(), 'instance')
    
    DB_NAME = os.getenv('DB_NAME', 'database.db')
    DATABASE_URI = os.path.join(INSTANCE_PATH, DB_NAME)
    
    # 🔥 تنظیم حیاتی: مسیر فایل کوکی یوتیوب 
    # این فایل توسط CI/CD (Github Actions) روی سرور تزریق می‌شود
    YT_COOKIES_PATH = os.path.join(INSTANCE_PATH, 'cookies.txt')