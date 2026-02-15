# core/services/bot/__init__.py

import asyncio
import time
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, InlineQueryHandler, ChatMemberHandler, filters
)
from core.config import Config
from core.models import init_db  # 🔥 اضافه شد: استقلال دیتابیسی ربات

# هندلرها (چون در همین پوشه هستند، ایمپورت نسبی .handlers درست است)
from .handlers import (
    start, list_devices, handle_callbacks, handle_text, 
    handle_audio, inline_music_search, youtube_dl, handle_my_chat_member
)

logger = logging.getLogger(__name__)

def run_bot_service():
    """Main Entry Point for the Bot Service (Running in 'bot' container)"""
    
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
            
            logger.info("🤖 Bot Service Started (Modular v3.0 - Containerized)...")
            
            # اجرای Polling با قابلیت دریافت همه آپدیت‌ها
            app.run_polling(
                poll_interval=1.0, 
                timeout=30,
                allowed_updates=Update.ALL_TYPES,
                stop_signals=None # جلوگیری از تداخل سیگنال با داکر
            )
            
        except Exception as e:
            logger.error(f"❌ Bot Critical Crash: {e}")
            
            try: 
                if loop.is_running(): loop.close()
            except: pass
            
            logger.info("♻️ Restarting Bot Process in 5 seconds...")
            time.sleep(5)