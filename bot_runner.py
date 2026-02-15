# bot_runner.py
import logging
import sys
import asyncio
from core.services.bot import run_bot_service

# تنظیمات لاگ اختصاصی ربات
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [BOT_CONTAINER] - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

if __name__ == "__main__":
    print("🚀 Starting Dedicated Bot Container...")
    try:
        # اجرای لوپ اصلی ربات
        run_bot_service()
    except KeyboardInterrupt:
        print("🛑 Bot Service Stopped by User.")
    except Exception as e:
        print(f"💀 Bot Service Crashed: {e}")