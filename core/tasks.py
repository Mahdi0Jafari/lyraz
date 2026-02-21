# core/tasks.py

import os
import json
import logging
import asyncio
import requests
import sqlite3
import random
from huey import SqliteHuey
from telegram import Bot
from telegram.constants import ParseMode

from core.config import Config
from core.services.youtube import YouTubeService
from core.services.bot.database import bot_db_exec, get_user_id

logger = logging.getLogger("huey.consumer")

# 1. تعریف صف وظایف (Huey)
huey = SqliteHuey(
    name='fanus_tasks',
    filename=os.path.join(Config.INSTANCE_PATH, 'queue.db')
)

# 2. سرویس‌ها
yt_service = YouTubeService()

# ==========================================
# 🛠 توابع کمکی
# ==========================================

def notify_web_bridge(data_dict):
    """ارسال پیام به کانتینر وب (Bridge) برای آپدیت SSE"""
    try:
        url = "http://web:5000/internal/announce"
        sse_msg = f"data: {json.dumps(data_dict)}\n\n"
        requests.post(url, json={'message': sse_msg}, timeout=2)
    except Exception as e:
        logger.error(f"Bridge notification failed: {e}")

async def upload_to_telegram(local_bot, file_path, title, artist, video_id):
    """آپلود فایل دانلود شده به کانال آرشیو و دریافت شناسه فایل"""
    if not Config.STORAGE_CHANNEL_ID:
        raise Exception("STORAGE_CHANNEL_ID is not set in env vars.")

    try:
        with open(file_path, 'rb') as f:
            caption = f"YT: {video_id}\nTitle: {title}"
            sent_msg = await local_bot.send_audio(
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


# ==========================================
# 🎧 تسک اصلی: دانلود تک‌آهنگ
# ==========================================

@huey.task()
def download_and_process_track(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id):
    """تسک Worker برای دانلود و پردازش یک تک‌آهنگ"""
    logger.info(f"🚀 Task Started: {title} ({video_id})")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(
            _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id)
        )
    finally:
        loop.close()

async def _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id):
    path = None
    # 🔥 ساخت یک نمونه بات مستقل فقط برای همین تسک برای جلوگیری از کرش Event Loop
    local_bot = Bot(token=Config.BOT_TOKEN)
    
    try:
        # 1. دانلود فایل
        path = await yt_service.download(video_id)
        
        if not path:
            if message_id:
                await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Download Failed. Please try again.")
            return

        # 2. آپلود به تلگرام (پاس دادن بات لوکال)
        tg_audio = await upload_to_telegram(local_bot, path, title, artist, video_id)
        
        if not tg_audio:
            if message_id:
                await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Failed to archive track.")
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

        # 4. پاکسازی پیام وضعیت (در صورت وجود)
        if message_id:
            try: await local_bot.delete_message(chat_id=chat_id, message_id=message_id)
            except: pass

        # 5. 🔥 ارسال فایل برای کاربر 🔥
        user_caption = f"🎧 *{title}*\n👤 {artist}"
        if session_token:
            user_caption += "\n📺 *Added to Queue*"
        
        try:
            await local_bot.send_audio(
                chat_id=chat_id,
                audio=track_meta['file_id'],
                caption=user_caption,
                title=title,
                performer=artist,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send audio to user: {e}")

        # 6. پردازش اتصال به تلویزیون
        if session_token:
            track_db_id = None
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                cur = conn.execute("SELECT id FROM tracks WHERE youtube_id=?", (video_id,))
                res = cur.fetchone()
                if res: track_db_id = res[0]

            if track_db_id:
                internal_user_id = get_user_id(user_id)
                bot_db_exec("""
                    INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                    VALUES (?, ?, ?, ?)
                """, (internal_user_id, track_db_id, internal_user_id, session_token))

                track_data = {
                    'title': title, 'performer': artist,
                    'file_unique_id': track_meta['file_unique_id'], 'duration': track_meta['duration'],
                    'added_by': user_first_name, 'session_token': session_token
                }
                notify_web_bridge(track_data)

    except Exception as e:
        logger.error(f"Task Failed: {e}")
        if message_id:
            try: await local_bot.send_message(chat_id=chat_id, text="❌ System Error occurred.")
            except: pass
    finally:
        if path: yt_service.cleanup(path)
        # 🔥 بستن ایمن کانکشن بات بعد از پایان تسک
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass


# ==========================================
# 🗂 تسک جدید: پردازش آیتم‌های پلی‌لیست اسپاتیفای
# ==========================================

@huey.task()
def process_spotify_playlist_item(search_query, expected_title, expected_artist, user_id, user_first_name, session_token, chat_id):
    """
    تسک مخصوص پلی‌لیست: ابتدا در یوتیوب سرچ می‌کند، اگر در دیتابیس بود از کش می‌خواند، 
    در غیر این صورت آن را برای دانلود به جریان اصلی می‌فرستد.
    """
    logger.info(f"🔎 Spotify Item Task: {search_query}")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _async_spotify_logic(search_query, expected_title, expected_artist, user_id, user_first_name, session_token, chat_id)
        )
    finally:
        loop.close()

async def _async_spotify_logic(search_query, expected_title, expected_artist, user_id, user_first_name, session_token, chat_id):
    local_bot = Bot(token=Config.BOT_TOKEN)
    try:
        # ۱. تاخیر تصادفی (Jitter) بین 1 تا 3 ثانیه برای جلوگیری از بن شدن توسط یوتیوب در لیست‌های طولانی
        await asyncio.sleep(random.uniform(1.0, 3.0))

        # ۲. جستجو در یوتیوب
        results = yt_service.search(search_query)
        if not results:
            logger.warning(f"No YT match found for: {search_query}")
            return
        
        vid = results[0].get('videoId')
        title = expected_title
        artist = expected_artist

        # ۳. بررسی وجود فایل در آرشیو (Cache Hit)
        cached = None
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM tracks WHERE youtube_id=?", (vid,))
            cached = cur.fetchone()

        if cached:
            # اگر فایل در سرور موجود است، فقط آن را به کاربر بفرست و به صف تلویزیون اضافه کن
            user_caption = f"🎧 *{title}*\n👤 {artist}\n📺 *Added to Queue*" if session_token else f"🎧 *{title}*\n👤 {artist}"
            try:
                await local_bot.send_audio(
                    chat_id=chat_id, audio=cached['file_id'], caption=user_caption,
                    title=title, performer=artist, parse_mode=ParseMode.MARKDOWN
                )
            except Exception: pass

            if session_token:
                internal_user_id = get_user_id(user_id)
                bot_db_exec("""
                    INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                    VALUES (?, ?, ?, ?)
                """, (internal_user_id, cached['id'], internal_user_id, session_token))

                track_data = {
                    'title': title, 'performer': artist,
                    'file_unique_id': cached['file_unique_id'], 'duration': cached['duration'],
                    'added_by': user_first_name, 'session_token': session_token
                }
                notify_web_bridge(track_data)
            return

        # ۴. اگر فایل نبود (Cache Miss)، پروسه دانلود کامل را اجرا کن
        # پیام وضعیت (Downloading...) را برای پلی‌لیست‌ها رد می‌کنیم تا چت کاربر اسپم نشود
        await _async_logic(vid, title, artist, user_id, user_first_name, session_token, chat_id, message_id=None)

    except Exception as e:
        logger.error(f"Spotify Playlist Logic Error: {e}")
    finally:
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass