# app.py

import threading
import os
import logging
from core import create_app 
from core.services.bot import run_bot_service

# تنظیمات لاگینگ برای دیدن وضعیت در کنسول
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ساخت نمونه اپلیکیشن فلاسک
app = create_app()

def start_bot_safe():
    """
    این تابع بررسی می‌کند که آیا ربات قبلاً اجرا شده یا نه.
    اگر اجرا نشده بود، آن را در یک ترد پس‌زمینه روشن می‌کند.
    """
    # بررسی لیست تردها برای جلوگیری از اجرای تکراری
    for t in threading.enumerate():
        if t.name == "TelegramBotThread":
            logger.info("🤖 Bot is already running.")
            return

    logger.info("🚀 Starting Telegram Bot Service...")
    bot_thread = threading.Thread(
        target=run_bot_service, 
        name="TelegramBotThread", 
        daemon=True
    )
    bot_thread.start()

# ----------------------------------------------------------------
# سناریوی ۱: اجرا توسط Gunicorn (روی سرور)
# وقتی Gunicorn فایل را ایمپورت می‌کند، نام آن __main__ نیست.
# بنابراین باید اینجا ربات را استارت بزنیم.
# ----------------------------------------------------------------
if __name__ != '__main__':
    # این خط باعث می‌شود وقتی Gunicorn با دستور gunicorn app:app اجرا می‌شود،
    # ربات هم داخل همان Worker روشن شود.
    start_bot_safe()

# ----------------------------------------------------------------
# سناریوی ۲: اجرا به صورت دستی (Local Development)
# دستور: python app.py
# ----------------------------------------------------------------
if __name__ == '__main__':
    # استارت ربات قبل از بالا آمدن سرور
    start_bot_safe()
    
    print("🚀 Fanus Player Platform Starting Local Server...")
    
    port = int(os.environ.get("PORT", 5000))
    
    # نکته مهم: use_reloader=False باشد تا ربات دو بار اجرا نشود
    app.run(
        debug=True, 
        use_reloader=False, 
        host='0.0.0.0', 
        port=port
    )