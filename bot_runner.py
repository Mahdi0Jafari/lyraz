# bot_runner.py
import sys
import asyncio
from core.services.bot import run_bot_service
from core.logger import setup_logger

# 🔥 اتصال به سیستم لاگینگ متمرکز (ذخیره در bot.log)
logger = setup_logger('BOT_SERVICE', 'bot.log')

if __name__ == "__main__":
    logger.info("🚀 Starting Dedicated Bot Container...")
    try:
        # اجرای لوپ اصلی ربات
        run_bot_service()
    except KeyboardInterrupt:
        logger.warning("🛑 Bot Service Stopped by User.")
    except Exception as e:
        logger.error(f"💀 Bot Service Crashed: {e}", exc_info=True)