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
    """ارسال پیام به کانتینر Web برای آپدیت کردن رابط کاربری تلویزیون‌ها"""
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

def generate_progress_bar(current, total, length=12):
    """تولید نوار پیشرفت بصری برای پیام تلگرام"""
    percent = current / total
    filled_length = int(length * percent)
    bar = '█' * filled_length + '░' * (length - filled_length)
    return f"`[{bar}]` {int(percent * 100)}%"


# ==========================================
# 🎧 MAIN TASK: SINGLE TRACK DOWNLOAD
# ==========================================

@huey.task()
def download_and_process_track(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality=None):
    """ورکر تسک برای دانلود و پردازش یک آهنگ (Non-blocking)"""
    logger.info(f"🚀 Task Started: {title} ({video_id})")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality)
        )
    finally:
        loop.close()

async def _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality=None, is_batch=False):
    """
    is_batch: اگر True باشد، پیام وضعیتی که در هندلر ساخته شده (message_id) پاک نمی‌شود، 
    چون آن پیام قرار است به عنوان نوار پیشرفت کل پلی‌لیست عمل کند. اما فایل صوتی حتماً ارسال می‌شود.
    """
    path = None
    local_bot = Bot(token=Config.BOT_TOKEN)
    
    try:
        path = await yt_service.download(video_id, quality=quality)
        if not path:
            if message_id and not is_batch:
                await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Download Failed.")
            return False

        tg_audio = await upload_to_telegram(local_bot, path, title, artist, video_id)
        if not tg_audio:
            if message_id and not is_batch:
                await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Failed to archive track.")
            return False

        actual_bitrate = int(quality) if quality else int(Config.AUDIO_QUALITY if hasattr(Config, 'AUDIO_QUALITY') else 192)

        track_meta = {
            'file_unique_id': tg_audio.file_unique_id,
            'file_id': tg_audio.file_id,
            'title': title, 'performer': artist,
            'duration': tg_audio.duration, 'file_size': tg_audio.file_size,
            'thumb_id': tg_audio.thumbnail.file_id if tg_audio.thumbnail else None,
            'youtube_id': video_id, 'bitrate': actual_bitrate
        }

        sql = """
            INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, youtube_id, bitrate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_id) DO UPDATE SET file_id=excluded.file_id, bitrate=excluded.bitrate
        """
        bot_db_exec(sql, (
            track_meta['file_unique_id'], track_meta['file_id'], track_meta['title'], 
            track_meta['performer'], track_meta['duration'], track_meta['file_size'], 
            track_meta['thumb_id'], track_meta['youtube_id'], track_meta['bitrate']
        ))

        # اگر دانلود تکی است، پیام "در حال جستجو/دانلود" را پاک کن تا فقط فایل صوتی بماند
        if message_id and not is_batch:
            try: await local_bot.delete_message(chat_id=chat_id, message_id=message_id)
            except: pass

        # --- Hub Injection ---
        reply_markup = None
        d_name = "Hub"
        if session_token:
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                res = conn.execute("SELECT device_name, admin_id FROM sessions WHERE token=?", (session_token,)).fetchone()
                d_name = res[0] if res and res[0] else f"Hub-{session_token[:4]}"
                hub_admin_id = res[1] if res else None

            base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Open Player", url=f"{base_url}/live/{session_token}")]])

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

                notify_web_bridge({
                    'type': 'new_track', 'title': title, 'performer': artist,
                    'file_unique_id': track_meta['file_unique_id'], 'duration': track_meta['duration'],
                    'added_by': user_first_name, 'session_token': session_token,
                    'sync_timestamp': time.time() 
                })
        
        # 🔥 ارسال فایل صوتی به چت کاربر (همیشه اجرا می‌شود، چه تکی چه در پلی‌لیست)
        user_caption = f"🎧 *{title}*\n👤 {artist}" + (f"\n📡 Added to: *{d_name}*" if session_token else "")
        try:
            await local_bot.send_audio(
                chat_id=chat_id, audio=track_meta['file_id'], caption=user_caption,
                title=title, performer=artist, parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to deliver audio: {e}")

        return track_meta

    except Exception as e:
        logger.error(f"Worker Task Failed: {e}")
        return False
    finally:
        if path: yt_service.cleanup(path)
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass


# ==========================================
# 🗂 BATCH PLAYLIST PROCESSING (V4.5 UX)
# ==========================================

@huey.task()
def download_playlist_batch(tracks, playlist_name, cover_url, user_id, user_first_name, session_token, chat_id, message_id, quality=None):
    """
    پردازشگر اصلی برای آلبوم‌ها و پلی‌لیست‌ها.
    """
    logger.info(f"🗂 Starting Batch Download: {playlist_name} ({len(tracks)} tracks)")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _async_batch_logic(tracks, playlist_name, cover_url, user_id, user_first_name, session_token, chat_id, message_id, quality)
        )
    finally:
        loop.close()

async def _async_batch_logic(tracks, playlist_name, cover_url, user_id, user_first_name, session_token, chat_id, message_id, quality):
    local_bot = Bot(token=Config.BOT_TOKEN)
    total = len(tracks)
    success_count = 0
    failed_count = 0

    try:
        # ۱. ارسال کاور پلی‌لیست در هماااان لحظه اول (قبل از شروع حلقه دانلود)
        if cover_url:
            try:
                await local_bot.send_photo(
                    chat_id=chat_id, 
                    photo=cover_url, 
                    caption=f"💽 *{playlist_name}*\n_Preparing {total} tracks for download..._", 
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.warning(f"Could not send cover photo: {e}")

        # ۲. حلقه پردازش ترک‌ها
        for i, track_info in enumerate(tracks, 1):
            search_query = track_info['search_query']
            title = track_info['title']
            artist = track_info['artist']

            # آپدیت پیام اولیه (message_id) به عنوان نوار پیشرفت
            # هر ۳ آهنگ یکبار آپدیت می‌کنیم تا خطای تلگرام نگیریم (یا در آهنگ اول)
            if i % 3 == 0 or i == 1:
                try:
                    progress = generate_progress_bar(i, total)
                    status_text = f"🗂 *{playlist_name}*\n\n{progress}\n📥 Processing: _{title}_\n✅ Done: {success_count} | ❌ Fail: {failed_count}"
                    await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=status_text, parse_mode=ParseMode.MARKDOWN)
                except Exception: pass

            results = yt_service.search(search_query)
            if not results:
                failed_count += 1
                continue
            
            vid = results[0].get('videoId')
            
            # بررسی کش برای جلوگیری از دانلود مجدد
            cached = None
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT * FROM tracks WHERE youtube_id=?", (vid,))
                cached = cur.fetchone()

            if cached:
                # ثبت در دیتابیس هاب
                reply_markup = None
                d_name = "Hub"
                
                if session_token:
                    with sqlite3.connect(Config.DATABASE_URI) as conn:
                        res = conn.execute("SELECT device_name, admin_id FROM sessions WHERE token=?", (session_token,)).fetchone()
                        d_name = res['device_name'] if res and res['device_name'] else f"Hub-{session_token[:4]}"
                        hub_admin_id = res['admin_id'] if res else None
                    
                    base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
                    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Open Player", url=f"{base_url}/live/{session_token}")]])
                    
                    internal_user_id = get_user_id(user_id)
                    safe_owner_id = hub_admin_id if hub_admin_id else internal_user_id
                    
                    bot_db_exec("""
                        INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                        VALUES (?, ?, ?, ?)
                    """, (safe_owner_id, cached['id'], internal_user_id, session_token))

                    notify_web_bridge({
                        'type': 'new_track', 'title': title, 'performer': artist,
                        'file_unique_id': cached['file_unique_id'], 'duration': cached['duration'],
                        'added_by': user_first_name, 'session_token': session_token,
                        'sync_timestamp': time.time()
                    })
                
                # 🔥 ارسال فایل کش شده به کاربر
                user_caption = f"🎧 *{title}*\n👤 {artist}" + (f"\n📡 Added to: *{d_name}*" if session_token else "")
                try:
                    await local_bot.send_audio(
                        chat_id=chat_id, audio=cached['file_id'], caption=user_caption,
                        title=title, performer=artist, parse_mode=ParseMode.MARKDOWN,
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Failed to deliver cached audio: {e}")
                    
                success_count += 1
            else:
                # اجرای منطق دانلود اصلی (is_batch=True تا فایل ارسال شود اما پروگرس بار اصلی پاک نشود)
                result = await _async_logic(vid, title, artist, user_id, user_first_name, session_token, chat_id, message_id=message_id, quality=quality, is_batch=True)
                
                if result: success_count += 1
                else: failed_count += 1
            
            # فاصله استراتژیک برای جلوگیری از بن شدن توسط یوتیوب
            await asyncio.sleep(random.uniform(1.0, 2.5))

        # ۳. آپدیت نهایی پیام پروگرس‌بار
        final_text = f"🎉 *{playlist_name} Complete!*\n\n{generate_progress_bar(total, total)}\n✅ Added: {success_count}\n❌ Failed: {failed_count}"
        try:
            await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text, parse_mode=ParseMode.MARKDOWN)
        except: pass

    except Exception as e:
        logger.error(f"Batch Logic Error: {e}")
    finally:
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass