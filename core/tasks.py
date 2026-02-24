# core/tasks.py

import os
import json
import logging
import asyncio
import requests
import sqlite3
import random
import time
from huey import SqliteHuey
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode

from core.config import Config
from core.services.youtube import YouTubeService
from core.services.bot.database import bot_db_exec, get_user_id

logger = logging.getLogger("huey.consumer")

# 1. Initialize Task Queue (Huey)
huey = SqliteHuey(
    name='fanus_tasks',
    filename=os.path.join(Config.INSTANCE_PATH, 'queue.db')
)

# 2. Services
yt_service = YouTubeService()

# ==========================================
# 🛠 HELPER FUNCTIONS
# ==========================================

def notify_web_bridge(data_dict):
    """
    ارسال پیام به کانتینر Web برای آپدیت کردن رابط کاربری تلویزیون‌ها 
    """
    try:
        url = "http://web:5000/internal/announce"
        sse_msg = f"data: {json.dumps(data_dict)}\n\n"
        requests.post(url, json={'message': sse_msg}, timeout=2)
    except Exception as e:
        logger.error(f"Bridge notification failed: {e}")

async def upload_to_telegram(local_bot, file_path, title, artist, video_id):
    """آپلود فایل دانلود شده به کانال آرشیو تلگرام و دریافت File ID"""
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
# 🎧 MAIN TASK: SINGLE TRACK DOWNLOAD
# ==========================================

@huey.task()
def download_and_process_track(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality=None):
    """ورکر تسک برای دانلود و پردازش یک آهنگ (Non-blocking)"""
    logger.info(f"🚀 Task Started: {title} ({video_id}) | Target Quality: {quality}")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(
            _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality)
        )
    finally:
        loop.close()

async def _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality=None):
    path = None
    local_bot = Bot(token=Config.BOT_TOKEN)
    
    try:
        # 1. Download File
        path = await yt_service.download(video_id, quality=quality)
        
        if not path:
            if message_id:
                await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Download Failed. Source might be unavailable.")
            return

        # 2. Upload to Telegram Storage
        tg_audio = await upload_to_telegram(local_bot, path, title, artist, video_id)
        
        if not tg_audio:
            if message_id:
                await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Failed to archive the track to cloud.")
            return

        # 3. Store in Database (V4 Schema Support)
        actual_bitrate = int(quality) if quality else int(Config.AUDIO_QUALITY if hasattr(Config, 'AUDIO_QUALITY') else 192)

        track_meta = {
            'file_unique_id': tg_audio.file_unique_id,
            'file_id': tg_audio.file_id,
            'title': title,
            'performer': artist,
            'duration': tg_audio.duration,
            'file_size': tg_audio.file_size,
            'thumb_id': tg_audio.thumbnail.file_id if tg_audio.thumbnail else None,
            'youtube_id': video_id,
            'bitrate': actual_bitrate
        }

        sql = """
            INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, youtube_id, bitrate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_id) DO UPDATE SET file_id=excluded.file_id, bitrate=excluded.bitrate
        """
        bot_db_exec(sql, (
            track_meta['file_unique_id'], track_meta['file_id'], 
            track_meta['title'], track_meta['performer'], 
            track_meta['duration'], track_meta['file_size'], 
            track_meta['thumb_id'], track_meta['youtube_id'],
            track_meta['bitrate']
        ))

        # 4. Clean up status message
        if message_id:
            try: await local_bot.delete_message(chat_id=chat_id, message_id=message_id)
            except: pass

        # 5. Delivery Logic (V4 Collaborative Hub)
        user_caption = f"🎧 *{title}*\n👤 {artist}"
        reply_markup = None
        
        if session_token:
            # پیدا کردن اطلاعات دقیق هاب
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                res = conn.execute("SELECT device_name, admin_id FROM sessions WHERE token=?", (session_token,)).fetchone()
                d_name = res[0] if res and res[0] else f"Hub-{session_token[:4]}"
                hub_admin_id = res[1] if res else None

            user_caption += f"\n📡 Added to: *{d_name}*"
            
            # تولید دکمه شیشه‌ای برای پلیر
            base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("▶️ Open Player", url=f"{base_url}/live/{session_token}")
            ]])

            # استخراج ID آهنگ از دیتابیس
            track_db_id = None
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                cur = conn.execute("SELECT id FROM tracks WHERE youtube_id=?", (video_id,))
                t_res = cur.fetchone()
                if t_res: track_db_id = t_res[0]

            if track_db_id:
                internal_user_id = get_user_id(user_id)
                safe_owner_id = hub_admin_id if hub_admin_id else internal_user_id
                
                bot_db_exec("""
                    INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                    VALUES (?, ?, ?, ?)
                """, (safe_owner_id, track_db_id, internal_user_id, session_token))

                track_data = {
                    'type': 'new_track',
                    'title': title, 'performer': artist,
                    'file_unique_id': track_meta['file_unique_id'], 'duration': track_meta['duration'],
                    'added_by': user_first_name, 'session_token': session_token,
                    'sync_timestamp': time.time() 
                }
                notify_web_bridge(track_data)
        
        try:
            await local_bot.send_audio(
                chat_id=chat_id,
                audio=track_meta['file_id'],
                caption=user_caption,
                title=title,
                performer=artist,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to deliver audio to user: {e}")

    except Exception as e:
        logger.error(f"Worker Task Failed: {e}")
        if message_id:
            try: await local_bot.send_message(chat_id=chat_id, text="❌ System error occurred during processing.")
            except: pass
    finally:
        if path: yt_service.cleanup(path)
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass


# ==========================================
# 🗂 PLAYLIST PROCESSING TASK
# ==========================================

@huey.task()
def process_spotify_playlist_item(search_query, expected_title, expected_artist, user_id, user_first_name, session_token, chat_id, quality=None):
    logger.info(f"🔎 Playlist Task: {search_query}")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _async_spotify_logic(search_query, expected_title, expected_artist, user_id, user_first_name, session_token, chat_id, quality)
        )
    finally:
        loop.close()

async def _async_spotify_logic(search_query, expected_title, expected_artist, user_id, user_first_name, session_token, chat_id, quality=None):
    local_bot = Bot(token=Config.BOT_TOKEN)
    try:
        await asyncio.sleep(random.uniform(1.0, 3.0))

        results = yt_service.search(search_query)
        if not results:
            logger.warning(f"No YT match found for: {search_query}")
            return
        
        vid = results[0].get('videoId')
        title = expected_title
        artist = expected_artist

        cached = None
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM tracks WHERE youtube_id=?", (vid,))
            cached = cur.fetchone()

        if cached:
            user_caption = f"🎧 *{title}*\n👤 {artist}"
            reply_markup = None
            
            if session_token:
                with sqlite3.connect(Config.DATABASE_URI) as conn:
                    res = conn.execute("SELECT device_name, admin_id FROM sessions WHERE token=?", (session_token,)).fetchone()
                    d_name = res[0] if res and res[0] else f"Hub-{session_token[:4]}"
                    hub_admin_id = res[1] if res else None
                    
                user_caption += f"\n📡 Added to: *{d_name}*"
                base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
                reply_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton("▶️ Open Player", url=f"{base_url}/live/{session_token}")
                ]])
                
                internal_user_id = get_user_id(user_id)
                safe_owner_id = hub_admin_id if hub_admin_id else internal_user_id
                
                bot_db_exec("""
                    INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                    VALUES (?, ?, ?, ?)
                """, (safe_owner_id, cached['id'], internal_user_id, session_token))

                track_data = {
                    'type': 'new_track',
                    'title': title, 'performer': artist,
                    'file_unique_id': cached['file_unique_id'], 'duration': cached['duration'],
                    'added_by': user_first_name, 'session_token': session_token,
                    'sync_timestamp': time.time()
                }
                notify_web_bridge(track_data)
                
            try:
                await local_bot.send_audio(
                    chat_id=chat_id, audio=cached['file_id'], caption=user_caption,
                    title=title, performer=artist, parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            except Exception: pass
            return

        # Cache Miss -> Trigger Full Download Protocol
        await _async_logic(vid, title, artist, user_id, user_first_name, session_token, chat_id, message_id=None, quality=quality)

    except Exception as e:
        logger.error(f"Playlist Logic Error: {e}")
    finally:
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass