# core/config.py

import os
from dotenv import load_dotenv

# لود کردن فایل .env از مسیر اصلی پروژه
load_dotenv()

class Config:
    # --- 1. تنظیمات امنیتی و هویت (Security & Identity) ---
    SECRET_KEY = os.getenv('SECRET_KEY', 'Lyraz-default-secret-key')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin')
    
    # آیدی عددی تلگرام ادمین کل (Master Admin) برای دسترسی‌های حساس
    # نکته: در فایل .env باید عدد باشد. اگر موجود نباشد 0 ست می‌شود.
    ADMIN_TELEGRAM_ID = int(os.getenv('ADMIN_TELEGRAM_ID', 0))
    
    # --- 2. تنظیمات تلگرام (Telegram Interface) ---
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    BOT_USERNAME = os.getenv('BOT_USERNAME', 'Lyrazbot')
    
    # آیدی کانال آرشیو موزیک (باید با -100 شروع شود)
    STORAGE_CHANNEL_ID = os.getenv('STORAGE_CHANNEL_ID')
    
    # لیست کانال‌های عضویت اجباری (Force Join)
    # از فایل .env خوانده شده و به لیست تبدیل می‌شود (جداکننده: ویرگول)
    _raw_channels = os.getenv('MANDATORY_CHANNELS', '')
    MANDATORY_CHANNELS = [c.strip() for c in _raw_channels.split(',') if c.strip()]

    # --- 3. لایه پخش و استریم (Media Engines) ---
    # تنظیم کیفیت پیش‌فرض صدا (رشته‌ای: '128', '192', '320')
    AUDIO_QUALITY = os.getenv('AUDIO_QUALITY', '192')
    
    # آدرس عمومی سرور (برای لینک‌های پلیر و وب‌هوک)
    BASE_URL = os.getenv('BASE_URL', 'http://127.0.0.1:5000').rstrip('/')
    
    # --- 4. زیرساخت و فایل‌سیستم (Infrastructure) ---
    # مسیر پایه پوشه instance (محل دیتابیس، لاگ‌ها و فایل‌های موقت)
    INSTANCE_PATH = os.path.join(os.getcwd(), 'instance')
    
    # ساختن پوشه instance در صورت عدم وجود (برای جلوگیری از خطای File Not Found)
    if not os.path.exists(INSTANCE_PATH):
        os.makedirs(INSTANCE_PATH)
        
    DB_NAME = os.getenv('DB_NAME', 'database.db')
    DATABASE_URI = os.path.join(INSTANCE_PATH, DB_NAME)
    
    # مسیر فایل کوکی یوتیوب (برای دور زدن محدودیت‌های ربات)
    YT_COOKIES_PATH = os.path.join(INSTANCE_PATH, 'cookies.txt')
    
    # پوشه کش دانلودها (موقت)
    DOWNLOAD_CACHE_PATH = os.path.join(INSTANCE_PATH, 'yt_cache')
    if not os.path.exists(DOWNLOAD_CACHE_PATH):
        os.makedirs(DOWNLOAD_CACHE_PATH)