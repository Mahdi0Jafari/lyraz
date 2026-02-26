# core/services/bot/__init__.py

import os
import asyncio
import time
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, InlineQueryHandler, ChatMemberHandler, filters
)
from core.config import Config
from core.models import init_db

# هندلرها
from .handlers import (
    start, list_devices, handle_callbacks, handle_text, 
    handle_audio, inline_music_search, youtube_dl, handle_my_chat_member
)

logger = logging.getLogger(__name__)

def run_bot_service():
    """
    Main Entry Point for the Bot Service (Running in 'bot' container)
    Reverted to POLLING mode for immediate stability and debugging.
    """
    
    # ۱. تضمین وجود دیتابیس قبل از شروع (مستقل از کانتینر وب)
    init_db()

    while True:
        try:
            # ایجاد لوپ جدید برای هر بار تلاش (در صورت ریستارت پس از کرش)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            app = ApplicationBuilder().token(Config.BOT_TOKEN).build()
            
            # --- Register Handlers (به ترتیب اولویت) ---
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("devices", list_devices))
            app.add_handler(CommandHandler("dl", youtube_dl))
            
            app.add_handler(InlineQueryHandler(inline_music_search))
            app.add_handler(CallbackQueryHandler(handle_callbacks))
            
            # هندلر فایل صوتی (بالاتر از متن)
            app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
            
            app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
            
            # هندلر متن (آخرین اولویت برای گرفتن سایر پیام‌ها)
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            
            # ==========================================
            # 🚀 RECOVERY PROTOCOL: STABLE POLLING
            # ==========================================
            # حالت Webhook موقتاً غیرفعال شد تا تداخل‌های شبکه و Nginx حذف شوند.
            
            logger.info("🤖 Starting Bot in POLLING mode (Stable Recovery)...")
            
            app.run_polling(
                poll_interval=1.0, 
                timeout=30,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True, # نادیده گرفتن پیام‌های انباشته شده زمان خرابی
                stop_signals=None 
            )
            
        except Exception as e:
            logger.error(f"❌ Bot Critical Crash: {e}", exc_info=True)
            
            try: 
                if loop.is_running(): loop.close()
            except: pass
            
            logger.info("♻️ Restarting Bot Process in 5 seconds...")
            time.sleep(5)