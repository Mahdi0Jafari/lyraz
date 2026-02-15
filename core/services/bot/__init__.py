# core/routes/services/bot/__init__.py

import asyncio
import time
import logging
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, InlineQueryHandler, ChatMemberHandler, filters
)
from core.config import Config
from .handlers import (
    start, list_devices, handle_callbacks, handle_text, 
    handle_audio, inline_music_search, youtube_dl, handle_my_chat_member
)

logger = logging.getLogger(__name__)

def run_bot_service():
    """Main Entry Point for the Bot Service"""
    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            app = ApplicationBuilder().token(Config.BOT_TOKEN).build()
            
            # --- Register Handlers ---
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("devices", list_devices))
            app.add_handler(CommandHandler("dl", youtube_dl))
            
            app.add_handler(InlineQueryHandler(inline_music_search))
            app.add_handler(CallbackQueryHandler(handle_callbacks))
            
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
            app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
            
            logger.info("🤖 Bot Service Started (Modular v3.0)...")
            app.run_polling(stop_signals=None, poll_interval=1.0, timeout=30)
            
        except Exception as e:
            logger.error(f"Bot Crash: {e}")
            try: loop.close()
            except: pass
            time.sleep(5)