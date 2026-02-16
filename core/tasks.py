# core/tasks.py

import os
import json
import logging
import asyncio
import requests
import sqlite3
from huey import SqliteHuey
from telegram import Bot
from telegram.constants import ParseMode

from core.config import Config
from core.services.youtube import YouTubeService
from core.services.bot.database import bot_db_exec, get_user_id

# تنظیمات لاگینگ برای ورکر
logger = logging.getLogger("huey.consumer")

# 1. تعریف صف وظایف (Huey)
# فایل دیتابیس صف در پوشه instance ذخیره می‌شود تا بین کانتینرها مشترک باشد
huey = SqliteHuey(
    name='fanus_tasks',
    filename=os.path.join(Config.INSTANCE_PATH, 'queue.db')
)

# 2. سرویس‌ها
yt_service = YouTubeService()
# بات تلگرام برای آپلود فایل در کانال آرشیو و ارسال به کاربر
bot = Bot(token=Config.BOT_TOKEN)

# --- توابع کمکی ---

def notify_web_bridge(data_dict):
    """ارسال پیام به کانتینر وب (Bridge) برای آپدیت SSE"""
    try:
        url = "http://web:5000/internal/announce"
        sse_msg = f"data: {json.dumps(data_dict)}\n\n"
        requests.post(url, json={'message': sse_msg}, timeout=2)
    except Exception as e:
        logger.error(f"Bridge notification failed: {e}")

async def upload_to_telegram(file_path, title, artist, video_id):
    """آپلود فایل دانلود شده به کانال آرشیو و دریافت شناسه فایل"""
    if not Config.STORAGE_CHANNEL_ID:
        raise Exception("STORAGE_CHANNEL_ID is not set in env vars.")

    try:
        with open(file_path, 'rb') as f:
            caption = f"YT: {video_id}\nTitle: {title}"
            sent_msg = await bot.send_audio(
                chat_id=Config.STORAGE_CHANNEL_ID,
                audio=f,
                title=title,
                performer=artist,
                caption=caption,
                read_timeout=300,
                write_timeout=300
            )
            return sent_msg.audio
    except Exception as e:
        logger.error(f"Telegram Upload Error: {e}")
        return None

# --- تسک اصلی (Heavy Lifting) ---

@huey.task()
def download_and_process_track(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id):
    """
    این تابع توسط Worker اجرا می‌شود.
    ورودی‌ها: اطلاعات آهنگ و اطلاعات کاربر برای پاسخگویی.
    """
    logger.info(f"🚀 Task Started: {title} ({video_id})")
    
    # چون Huey سینک است و تلگرام/دانلودر Async هستند، باید لوپ بسازیم
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # اجرای منطق اصلی داخل لوپ Async
        loop.run_until_complete(
            _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id)
        )
    finally:
        loop.close()

async def _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id):
    path = None
    try:
        # 1. دانلود فایل (سنگین‌ترین بخش)
        path = await yt_service.download(video_id)
        
        if not path:
            await bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id, 
                text="❌ Download Failed. Please try again."
            )
            return

        # 2. آپلود به تلگرام (کانال آرشیو)
        tg_audio = await upload_to_telegram(path, title, artist, video_id)
        
        if not tg_audio:
            await bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id, 
                text="❌ Failed to archive track. Check Storage Channel ID."
            )
            return

        # 3. ذخیره در دیتابیس (Tracks Table)
        track_meta = {
            'file_unique_id': tg_audio.file_unique_id,
            'file_id': tg_audio.file_id,
            'title': title,
            'performer': artist,
            'duration': tg_audio.duration,
            'file_size': tg_audio.file_size,
            'thumb_id': tg_audio.thumbnail.file_id if tg_audio.thumbnail else None,
            'youtube_id': video_id
        }

        sql = """
            INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, youtube_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_id) DO UPDATE SET file_id=excluded.file_id
        """
        bot_db_exec(sql, (
            track_meta['file_unique_id'], track_meta['file_id'], 
            track_meta['title'], track_meta['performer'], 
            track_meta['duration'], track_meta['file_size'], 
            track_meta['thumb_id'], track_meta['youtube_id']
        ))

        # 4. پاکسازی پیام "در حال دانلود..."
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except:
            pass

        # 5. 🔥 ارسال فایل برای کاربر (همیشه) 🔥
        # اگر به تلویزیون وصل باشد، در کپشن ذکر می‌شود
        user_caption = f"🎧 *{title}*\n👤 {artist}"
        if session_token:
            user_caption += "\n📺 *Added to Queue*"
        
        await bot.send_audio(
            chat_id=chat_id,
            audio=track_meta['file_id'],
            caption=user_caption,
            title=title,
            performer=artist,
            parse_mode=ParseMode.MARKDOWN
        )

        # 6. پردازش اتصال به تلویزیون (اگر سشن وجود دارد)
        if session_token:
            # دریافت ID داخلی ترک
            track_db_id = None
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                cur = conn.execute("SELECT id FROM tracks WHERE youtube_id=?", (video_id,))
                res = cur.fetchone()
                if res: track_db_id = res[0]

            if track_db_id:
                internal_user_id = get_user_id(user_id)
                
                # افزودن به playlist_items
                bot_db_exec("""
                    INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                    VALUES (?, ?, ?, ?)
                """, (internal_user_id, track_db_id, internal_user_id, session_token))

                # --- 🔥 اطلاع‌رسانی به Web (تلویزیون) ---
                track_data = {
                    'title': title, 
                    'performer': artist,
                    'file_unique_id': track_meta['file_unique_id'], 
                    'duration': track_meta['duration'],
                    'added_by': user_first_name, 
                    'session_token': session_token
                }
                # ارسال سیگنال از طریق Bridge
                notify_web_bridge(track_data)

    except Exception as e:
        logger.error(f"Task Failed: {e}")
        try:
            await bot.send_message(
                chat_id=chat_id, 
                text="❌ System Error occurred during processing."
            )
        except: pass
        
    finally:
        # پاکسازی فایل موقت
        if path: yt_service.cleanup(path)