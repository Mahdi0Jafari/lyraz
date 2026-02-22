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


@app.route('/system/backup-and-nuke', methods=['GET'])
def backup_and_nuke():
    """
    📦 Backup -> ☢️ Nuke -> 🏗️ Rebuild
    1. محتویات پوشه instance را ZIP می‌کند.
    2. پوشه را کاملاً پاکسازی می‌کند.
    3. اسکیما دیتابیس V4 را از نو می‌سازد.
    4. فایل ZIP را برای کاربر ارسال می‌کند.
    """
    import os
    import shutil
    import tempfile
    from flask import send_file
    from datetime import datetime
    from core.config import Config
    from core.models import init_db

    instance_path = Config.INSTANCE_PATH
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"fanus_v3_full_backup_{timestamp}"
    
    # ایجاد یک مسیر موقت خارج از پوشه instance برای ذخیره فایل ZIP
    tmp_dir = tempfile.gettempdir()
    zip_path = os.path.join(tmp_dir, zip_name)

    try:
        if not os.path.exists(instance_path):
            return "❌ Instance folder not found.", 404

        # ۱. عملیات پشتیبان‌گیری (Backup)
        # ساخت فایل ZIP از کل پوشه instance
        archive_path = shutil.make_archive(zip_path, 'zip', instance_path)
        logger.info(f"📦 Backup created at: {archive_path}")

        # ۲. عملیات انهدام (Nuke)
        for item in os.listdir(instance_path):
            item_path = os.path.join(instance_path, item)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                logger.warning(f"🔥 Purged during backup: {item}")
            except Exception as e:
                logger.error(f"Failed to delete {item}: {e}")

        # ۳. بازسازی فوری (Rebuild)
        # حالا که پوشه خالی شد، دیتابیس V4 را تمیز و پاکیزه می‌سازیم
        init_db()
        logger.info("✅ Database V4 rebuilt from scratch.")

        # ۴. ارسال فایل برای کاربر (Download)
        # استفاده از after_this_request برای پاک کردن فایل زیپ از /tmp پس از ارسال (اختیاری)
        return send_file(
            archive_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"{zip_name}.zip"
        )

    except Exception as e:
        logger.error(f"❌ Backup & Nuke Failed: {e}")
        return {"status": "error", "message": str(e)}, 500

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