# app.py

import os
import logging
import threading
import sys

# ایمپورت‌های پروژه
from core import create_app 
from core.services.bot import run_bot_service

# تنظیمات لاگینگ
# نکته: در Gunicorn لاگ‌ها توسط خود Gunicorn مدیریت می‌شوند، اما این خط برای حالت Local مفید است.
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- 1. ایجاد اپلیکیشن فلاسک ---
# این متغیر 'app' همان چیزی است که Gunicorn دنبالش می‌گردد (app:app)
app = create_app()

# --- 2. مدیریت اجرای ربات (هم‌روندی ایمن) ---
def start_bot_service():
    """
    اجرای سرویس ربات در پس‌زمینه.
    با توجه به اینکه در gunicorn_config.py از gevent استفاده کردیم،
    threading.Thread در واقع یک Greenlet سبک خواهد بود که عالی است.
    """
    # بررسی اینکه آیا ربات قبلاً اجرا شده است یا خیر
    # این کار با بررسی نام تردها انجام می‌شود
    current_threads = [t.name for t in threading.enumerate()]
    if "TelegramBotThread" in current_threads:
        logger.info("🤖 Bot Service is already active (Skipping start).")
        return

    logger.info("🚀 Initializing Telegram Bot Service...")
    
    bot_thread = threading.Thread(
        target=run_bot_service, 
        name="TelegramBotThread", 
        daemon=True
    )
    bot_thread.start()
    logger.info("✅ Bot Service Started in Background Thread.")

# ==========================================
# 🚀 ENTRY POINTS (نقاط شروع برنامه)
# ==========================================

# سناریوی ۱: اجرا توسط Gunicorn (Production)
# وقتی دستور `gunicorn -c gunicorn_config.py app:app` اجرا می‌شود:
# 1. Gunicorn این فایل را ایمپورت می‌کند.
# 2. متغیر __name__ برابر با 'app' است (نه '__main__').
# 3. Gunicorn خودش Monkey Patching را برای Gevent انجام داده است.
if __name__ != '__main__':
    # استفاده از هوک 'before_first_request' دیگر در نسخه‌های جدید فلاسک توصیه نمی‌شود.
    # بهترین جا برای استارت ترد پس‌زمینه در Gunicorn همینجاست (Global Scope Execution).
    
    # نکته امنیتی: اگر Gunicorn با چند ورکر اجرا شود، این کد در هر ورکر اجرا می‌شود.
    # اما چون در gunicorn_config.py مقدار workers=1 است، مشکلی پیش نمی‌آید.
    try:
        start_bot_service()
    except Exception as e:
        logger.error(f"❌ Failed to start bot service: {e}")

# سناریوی ۲: اجرا به صورت دستی (Local Development)
# دستور: python app.py
if __name__ == '__main__':
    print("⚠️  Running in LOCAL DEVELOPMENT mode.")
    print("⚠️  For Production, use: gunicorn -c gunicorn_config.py app:app")
    
    # استارت ربات
    start_bot_service()
    
    port = int(os.environ.get("PORT", 5000))
    
    # اجرای فلاسک
    # use_reloader=False حیاتی است تا ربات دو بار اجرا نشود
    app.run(
        debug=True, 
        use_reloader=False, 
        host='0.0.0.0', 
        port=port,
        threaded=True 
    )