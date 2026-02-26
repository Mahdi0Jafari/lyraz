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
    Optimized: Smart Webhook/Polling Switch based on Environment
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
            # 🚀 THE IQ 170+ PROTOCOL: SMART ROUTING
            # ==========================================
            base_url = getattr(Config, 'BASE_URL', '').rstrip('/')
            
            # اگر دامین تنظیم شده باشد و با https شروع شود، وب‌هوک فعال می‌شود
            if base_url.startswith('https'):
                # پورت داخلی کانتینر بات (پیش‌فرض 8443)
                bot_port = int(os.getenv('BOT_PORT', 8443))
                
                # توکن بات به عنوان URL Path برای امنیت (جلوگیری از حملات خارجی) استفاده می‌شود
                webhook_url = f"{base_url}/{Config.BOT_TOKEN}"
                
                logger.info(f"🚀 Starting Bot in WEBHOOK mode (Zero-Latency)")
                logger.info(f"🔗 Internal Port: {bot_port} | Public Endpoint: {webhook_url}")
                
                app.run_webhook(
                    listen="0.0.0.0",
                    port=bot_port,
                    url_path=Config.BOT_TOKEN,
                    webhook_url=webhook_url,
                    allowed_updates=Update.ALL_TYPES,
                    stop_signals=None # جلوگیری از تداخل سیگنال با داکر
                )
            else:
                # Fallback برای محیط لوکال (Development)
                logger.info("🤖 Starting Bot in POLLING mode (Local/Dev)...")
                app.run_polling(
                    poll_interval=1.0, 
                    timeout=30,
                    allowed_updates=Update.ALL_TYPES,
                    stop_signals=None
                )
            
        except Exception as e:
            logger.error(f"❌ Bot Critical Crash: {e}", exc_info=True)
            
            try: 
                if loop.is_running(): loop.close()
            except: pass
            
            logger.info("♻️ Restarting Bot Process in 5 seconds...")
            time.sleep(5)