# core/tasks.py

import os
import io
import json
import logging
import asyncio
import requests
import sqlite3
import random
import time
import re
from huey import SqliteHuey, crontab
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

from core.config import Config
from core.services.youtube import YouTubeService
from core.services.bot.database import bot_db_exec, get_user_id
from core.services.metadata import metadata_service

def get_bot_instance():
    req = HTTPXRequest(connect_timeout=20.0, read_timeout=30.0, write_timeout=30.0)
    return Bot(token=Config.BOT_TOKEN, request=req)

logger = logging.getLogger("huey.consumer")

# 1. Initialize Task Queue (Huey)
huey = SqliteHuey(
    name='Lyraz_tasks',
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

async def upload_to_telegram(local_bot, file_path, title, artist, video_id, cover_bytes=None):
    """آپلود فایل دانلود شده به کانال آرشیو تلگرام و دریافت File ID به همراه تامنیل و شناسه پیام کانال"""
    if not Config.STORAGE_CHANNEL_ID:
        raise Exception("STORAGE_CHANNEL_ID is not set in env vars.")

    try:
        with open(file_path, 'rb') as f:
            caption = (
                f"🎵 Lyraz Cloud Vault\n"
                f"🏷 Sig: {Config.VAULT_SIGNATURE}\n"
                f"🆔 YT: {video_id}\n"
                f"👤 Artist: {artist}\n"
                f"💽 Title: {title}"
            )
            thumb = io.BytesIO(cover_bytes) if cover_bytes else None
            sent_msg = await local_bot.send_audio(
                chat_id=Config.STORAGE_CHANNEL_ID,
                audio=f,
                title=title,
                performer=artist,
                caption=caption,
                thumbnail=thumb,
                read_timeout=300,
                write_timeout=300
            )
            return sent_msg.audio, sent_msg.message_id
    except Exception as e:
        logger.error(f"Telegram Upload Error: {e}")
        return None, None

def generate_progress_bar(current, total, length=12):
    """تولید نوار پیشرفت بصری برای پیام تلگرام"""
    percent = current / total
    filled_length = int(length * percent)
    bar = '█' * filled_length + '░' * (length - filled_length)
    return f"`[{bar}]` {int(percent * 100)}%"

async def deliver_audio_safe(local_bot, chat_id, track_row, title, artist, user_caption, reply_markup=None):
    """ارسال فایل صوتی با قابلیت خودترمیمی در صورت تغییر توکن ربات و فال‌بک امن کپشن"""
    file_id = track_row['file_id']
    try:
        try:
            return await local_bot.send_audio(
                chat_id=chat_id, audio=file_id, caption=user_caption,
                title=title, performer=artist, parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        except Exception as md_err:
            if "can't find end of the entity" in str(md_err).lower() or "can't parse entities" in str(md_err).lower():
                # حذف فرمت مارک‌داون در صورت وجود کاراکترهای خاص در نام آهنگ
                clean_caption = user_caption.replace('*', '').replace('_', '').replace('`', '').replace('[', '').replace(']', '')
                return await local_bot.send_audio(
                    chat_id=chat_id, audio=file_id, caption=clean_caption,
                    title=title, performer=artist, parse_mode=None,
                    reply_markup=reply_markup
                )
            raise md_err
    except Exception as send_err:
        storage_msg_id = track_row['storage_message_id'] if ('storage_message_id' in track_row.keys() and track_row['storage_message_id']) else None
        if storage_msg_id and Config.STORAGE_CHANNEL_ID:
            logger.warning(f"File ID delivery failed. Attempting self-healing via storage msg {storage_msg_id}...")
            try:
                fwd = await local_bot.forward_message(
                    chat_id=Config.STORAGE_CHANNEL_ID,
                    from_chat_id=Config.STORAGE_CHANNEL_ID,
                    message_id=storage_msg_id
                )
                if fwd and fwd.audio:
                    new_file_id = fwd.audio.file_id
                    bot_db_exec("UPDATE tracks SET file_id=? WHERE id=?", (new_file_id, track_row['id']))
                    try:
                        await local_bot.delete_message(chat_id=Config.STORAGE_CHANNEL_ID, message_id=fwd.message_id)
                    except Exception:
                        pass
                    return await local_bot.send_audio(
                        chat_id=chat_id, audio=new_file_id, caption=user_caption,
                        title=title, performer=artist, parse_mode=ParseMode.MARKDOWN,
                        reply_markup=reply_markup
                    )
            except Exception as heal_err:
                logger.error(f"Self-healing failed: {heal_err}")
        raise send_err

async def sync_vault_from_channel(bot=None):
    """
    موتور بازیابی خودکار و همگام‌سازی سریع مخزن تلگرام (Vault Auto-Sync)
    پیام‌های کانال ذخیره‌سازی را اسکن کرده و دیتابیس را پر می‌کند تا در صورت
    پاک شدن دیتابیس، حتی یک آهنگ هم نیاز به دانلود مجدد از یوتیوب نداشته باشد.
    """
    if not Config.STORAGE_CHANNEL_ID:
        logger.warning("Vault sync skipped: STORAGE_CHANNEL_ID is not configured.")
        return 0

    channel_id = Config.STORAGE_CHANNEL_ID
    local_bot = bot
    should_close_bot = False
    if not local_bot:
        local_bot = get_bot_instance()
        await local_bot.initialize()
        should_close_bot = True

    try:
        def get_max_mid():
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                res = conn.execute("SELECT MAX(storage_message_id) FROM tracks").fetchone()
                return res[0] if res and res[0] else Config.MIN_STORAGE_MESSAGE_ID

        start_mid = await asyncio.to_thread(get_max_mid)
        if not start_mid or start_mid < Config.MIN_STORAGE_MESSAGE_ID:
            start_mid = Config.MIN_STORAGE_MESSAGE_ID

        logger.info(f"🔄 Starting Vault Auto-Sync from message ID: {start_mid}")
        current_mid = start_mid + 1 if start_mid > Config.MIN_STORAGE_MESSAGE_ID else Config.MIN_STORAGE_MESSAGE_ID
        synced_count = 0
        batch_size = 15
        consecutive_empty_batches = 0

        async def check_msg(mid):
            try:
                fwd = await local_bot.forward_message(chat_id=channel_id, from_chat_id=channel_id, message_id=mid)
                meta = None
                if fwd and fwd.audio:
                    caption = fwd.caption or ''
                    if any(sig in caption for sig in [Config.VAULT_SIGNATURE, '#lyraz_vault', '#lyraz_verified_vault_2026']):
                        yt_match = re.search(r'🆔 YT: ([^\s]+)', caption)
                        if yt_match:
                            vid = yt_match.group(1).strip()
                            audio = fwd.audio
                            actual_bitrate = int(Config.AUDIO_QUALITY if hasattr(Config, 'AUDIO_QUALITY') else 192)
                            meta = (
                                audio.file_unique_id,
                                audio.file_id,
                                audio.title or 'Unknown Track',
                                audio.performer or 'Unknown Artist',
                                audio.duration or 0,
                                audio.file_size or 0,
                                audio.thumbnail.file_id if audio.thumbnail else None,
                                vid,
                                actual_bitrate,
                                mid
                            )
                if fwd:
                    try:
                        await local_bot.delete_message(chat_id=channel_id, message_id=fwd.message_id)
                    except Exception:
                        pass
                return meta
            except Exception:
                return None

        while consecutive_empty_batches < 2 and current_mid < start_mid + 2000:
            batch_mids = list(range(current_mid, current_mid + batch_size))
            results = await asyncio.gather(*[check_msg(m) for m in batch_mids])
            valid_tracks = [r for r in results if r is not None]

            if valid_tracks:
                consecutive_empty_batches = 0
                sql = """
                    INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, youtube_id, bitrate, storage_message_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(youtube_id) DO UPDATE SET 
                        file_unique_id=excluded.file_unique_id,
                        file_id=excluded.file_id,
                        title=excluded.title,
                        performer=excluded.performer,
                        duration=excluded.duration,
                        file_size=excluded.file_size,
                        thumb_id=excluded.thumb_id,
                        bitrate=excluded.bitrate,
                        storage_message_id=excluded.storage_message_id
                """
                def save_batch():
                    with sqlite3.connect(Config.DATABASE_URI) as conn:
                        conn.executemany(sql, valid_tracks)
                        conn.commit()
                await asyncio.to_thread(save_batch)
                synced_count += len(valid_tracks)
            else:
                consecutive_empty_batches += 1

            current_mid += batch_size

        logger.info(f"✅ Vault Auto-Sync completed: {synced_count} tracks synchronized into database.")
        return synced_count

    except Exception as e:
        logger.error(f"Error during vault sync: {e}")
        return 0
    finally:
        if should_close_bot and local_bot:
            try:
                await local_bot.shutdown()
            except Exception:
                pass

async def process_auto_broadcast(local_bot, file_id, title, artist, user_first_name):
    """
    موتور انتشار خودکار (Auto-Broadcast Engine).
    بررسی می‌کند که آیا در پنل ادمین انتشار خودکار روشن است یا خیر.
    """
    try:
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            
            if not settings or not settings['is_auto_broadcast_enabled'] or not settings['auto_broadcast_channel_id']:
                return # انتشار خودکار خاموش است یا کانالی ست نشده
                
            channel_id = settings['auto_broadcast_channel_id']
            base_caption = settings['default_caption'] or "{title} - {artist}"
            
            # پیدا کردن کپشن اختصاصی کانال (در صورت وجود)
            channel_info = conn.execute("SELECT caption_template FROM channels WHERE chat_id = ?", (channel_id,)).fetchone()
            if channel_info and channel_info['caption_template'] and channel_info['caption_template'].strip():
                base_caption = channel_info['caption_template']
                
            # جایگذاری متغیرها
            final_caption = base_caption.replace('{title}', title or 'Unknown')\
                                        .replace('{artist}', artist or 'Unknown')\
                                        .replace('{sender}', user_first_name or 'User')
            
            await local_bot.send_audio(
                chat_id=channel_id,
                audio=file_id,
                caption=final_caption,
                title=title,
                performer=artist,
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"✅ Auto-Broadcast sent to {channel_id}: {title}")
    except Exception as e:
        logger.error(f"❌ Auto-Broadcast Engine Error: {e}")

# ==========================================
# 🎧 MAIN TASK: SINGLE TRACK DOWNLOAD
# ==========================================

@huey.task()
def download_and_process_track(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality=None, cover_url=None, duration=None, log_id=None, target_channel_id=None):
    """ورکر تسک برای دانلود و پردازش یک آهنگ (Non-blocking)"""
    logger.info(f"🚀 Task Started: {title} ({video_id})")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality, cover_url=cover_url, duration=duration, log_id=log_id, target_channel_id=target_channel_id)
        )
    finally:
        loop.close()

async def _async_logic(video_id, title, artist, user_id, user_first_name, session_token, chat_id, message_id, quality=None, is_batch=False, cover_url=None, duration=None, log_id=None, target_channel_id=None):
    """
    is_batch: اگر True باشد، پیام وضعیتی که در هندلر ساخته شده (message_id) پاک نمی‌شود، 
    چون آن پیام قرار است به عنوان نوار پیشرفت کل پلی‌لیست عمل کند. اما فایل صوتی حتماً ارسال می‌شود.
    """
    path = None
    local_bot = get_bot_instance()
    
    if log_id:
        try:
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.execute("UPDATE ingestion_logs SET status = 'downloading' WHERE id = ?", (log_id,))
                conn.commit()
        except Exception:
            pass

    try:
        # ۱. تثبیت عنوان و خواننده اصلی (اگر از قبل مشخص است، قفل می‌شود و نباید تغییر کند)
        is_known_title = bool(title and title not in ['Unknown Track', 'YouTube Track'])
        is_known_artist = bool(artist and artist not in ['Unknown', 'Unknown Artist'])

        final_title = title if is_known_title else None
        final_artist = artist if is_known_artist else None

        # ۲. واکشی متادیتای غنی با اولویت کاور ارسالی (از اسپاتیفای) یا کاور یوتیوب
        yt_thumb = cover_url or (f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg" if video_id else None)
        rich_metadata = metadata_service.get_full_metadata(artist, title, duration=duration, thumbnail_url=yt_thumb)
        
        if not final_title:
            final_title = rich_metadata.get('title') or title or 'Unknown Track'
        if not final_artist:
            final_artist = rich_metadata.get('artist') or artist or 'Unknown Artist'

        # تضمین تطابق متادیتا برای تزریق به Mutagen با نام‌های قطعی
        rich_metadata['title'] = final_title
        rich_metadata['artist'] = final_artist

        # ۲.۵. ⚡️ بررسی وجود ترک در مخزن (Cache Vault)
        cached_track = None
        try:
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.row_factory = sqlite3.Row
                c_row = conn.execute("SELECT * FROM tracks WHERE youtube_id = ?", (video_id,)).fetchone()
                if c_row:
                    cached_track = dict(c_row)
        except Exception as c_err:
            logger.warning(f"Cache check error: {c_err}")

        if cached_track:
            logger.info(f"⚡️ Vault Hit for '{final_title}' ({video_id})! Skipping YouTube download.")
            track_meta = cached_track
            path = None
        else:
            # ۳. دانلود از یوتیوب یا ساندکلاد و تزریق متادیتا با Mutagen
            path = await yt_service.download(video_id, quality=quality, metadata=rich_metadata)
            
            if not path:
                if log_id:
                    try:
                        with sqlite3.connect(Config.DATABASE_URI) as conn:
                            conn.execute("UPDATE ingestion_logs SET status = 'failed', error_msg = 'Download failed' WHERE id = ?", (log_id,))
                            conn.commit()
                    except Exception: pass
                
                try:
                    with sqlite3.connect(Config.DATABASE_URI) as conn:
                        conn.execute("UPDATE campaign_tracks SET status = 'failed', error_msg = 'Download failed' WHERE youtube_id = ?", (video_id,))
                        conn.commit()
                except Exception: pass

                if message_id and not is_batch:
                    await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Download Failed.")
                return False

            # ۴. آپلود به کانال آرشیو خاموش (Storage) با تزریق تامنیل و دریافت شناسه پیام
            tg_audio, storage_msg_id = await upload_to_telegram(local_bot, path, final_title, final_artist, video_id, cover_bytes=rich_metadata.get('cover_bytes'))
            if not tg_audio:
                if log_id:
                    try:
                        with sqlite3.connect(Config.DATABASE_URI) as conn:
                            conn.execute("UPDATE ingestion_logs SET status = 'failed', error_msg = 'Telegram storage archive failed' WHERE id = ?", (log_id,))
                            conn.commit()
                    except Exception: pass
                
                try:
                    with sqlite3.connect(Config.DATABASE_URI) as conn:
                        conn.execute("UPDATE campaign_tracks SET status = 'failed', error_msg = 'Telegram archive failed' WHERE youtube_id = ?", (video_id,))
                        conn.commit()
                except Exception: pass

                if message_id and not is_batch:
                    await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Failed to archive track.")
                return False

            actual_bitrate = int(quality) if quality else int(Config.AUDIO_QUALITY if hasattr(Config, 'AUDIO_QUALITY') else 192)

            # ۴. ذخیره در دیتابیس
            track_meta = {
                'file_unique_id': tg_audio.file_unique_id,
                'file_id': tg_audio.file_id,
                'title': final_title, 'performer': final_artist,
                'duration': tg_audio.duration, 'file_size': tg_audio.file_size,
                'thumb_id': tg_audio.thumbnail.file_id if tg_audio.thumbnail else None,
                'youtube_id': video_id, 'bitrate': actual_bitrate,
                'storage_message_id': storage_msg_id
            }

            sql = """
                INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, youtube_id, bitrate, storage_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(youtube_id) DO UPDATE SET 
                    file_unique_id=excluded.file_unique_id,
                    file_id=excluded.file_id,
                    title=excluded.title,
                    performer=excluded.performer,
                    duration=excluded.duration,
                    file_size=excluded.file_size,
                    thumb_id=excluded.thumb_id,
                    bitrate=excluded.bitrate,
                    storage_message_id=excluded.storage_message_id
            """
            bot_db_exec(sql, (
                track_meta['file_unique_id'], track_meta['file_id'], track_meta['title'], 
                track_meta['performer'], track_meta['duration'], track_meta['file_size'], 
                track_meta['thumb_id'], track_meta['youtube_id'], track_meta['bitrate'],
                storage_msg_id
            ))
        
        if log_id:
            try:
                with sqlite3.connect(Config.DATABASE_URI) as conn:
                    conn.execute("UPDATE ingestion_logs SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?", (log_id,))
                    conn.commit()
            except Exception: pass

        if rich_metadata.get('lyrics'):
             try:
                 with sqlite3.connect(Config.DATABASE_URI) as conn:
                     conn.execute("INSERT OR REPLACE INTO lyrics_cache (file_unique_id, lyrics, source, updated_at) VALUES (?, ?, ?, ?)",
                                (track_meta['file_unique_id'], rich_metadata['lyrics'], "lrclib", int(time.time())))
                     conn.commit()
             except: pass

        if message_id and not is_batch:
            try: await local_bot.delete_message(chat_id=chat_id, message_id=message_id)
            except: pass

        # ۵. ثبت دانلود کاربر در جدول اختصاصی user_downloads (فقط برای کاربران واقعی، نه عملیات سیستمی)
        track_db_id = None
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            cur = conn.execute("SELECT id FROM tracks WHERE youtube_id=?", (video_id,))
            t_res = cur.fetchone()
            if t_res: track_db_id = t_res[0]

        if track_db_id and user_id and user_id != 0:
            from core.services.bot.database import log_user_download
            source_tag = 'spotify_playlist' if is_batch else 'bot'
            log_user_download(telegram_id=user_id, track_id=track_db_id, source=source_tag, first_name=user_first_name)

        # ۶. تزریق به هاب (در صورت فعال بودن سشن وب‌پلیر)
        reply_markup = None
        d_name = "Hub"
        if session_token:
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.row_factory = sqlite3.Row  
                res = conn.execute("SELECT device_name, admin_id FROM sessions WHERE token=?", (session_token,)).fetchone()
                d_name = res['device_name'] if res and res['device_name'] else f"Hub-{session_token[:4]}"
                hub_admin_id = res['admin_id'] if res else None

            base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Open Player", url=f"{base_url}/live/{session_token}")]])

            if track_db_id:
                internal_user_id = get_user_id(user_id)
                safe_owner_id = hub_admin_id if hub_admin_id else internal_user_id
                
                bot_db_exec("""
                    INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                    VALUES (?, ?, ?, ?)
                """, (safe_owner_id, track_db_id, internal_user_id, session_token))

                notify_web_bridge({
                    'type': 'new_track', 'title': final_title, 'performer': final_artist,
                    'file_unique_id': track_meta['file_unique_id'], 'duration': track_meta['duration'],
                    'added_by': user_first_name, 'session_token': session_token,
                    'sync_timestamp': time.time() 
                })
        
        # ۶. ارسال فایل صوتی به چت درخواست‌کننده با قابلیت خودترمیمی (در صورت وجود کاربر)
        user_caption = f"🎧 *{final_title}*\n👤 {final_artist}" + (f"\n📡 Added to: *{d_name}*" if session_token else "")
        if not is_batch and chat_id:
            try:
                await deliver_audio_safe(
                    local_bot=local_bot, chat_id=chat_id, track_row=track_meta,
                    title=final_title, artist=final_artist, user_caption=user_caption,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Failed to deliver audio to user: {e}")

            # ۷. 🔥 اجرای موتور Automation Rules 🔥
            await process_auto_broadcast(local_bot, track_meta['file_id'], final_title, final_artist, user_first_name)

        # ۸. 📻 ارسال به کانال اختصاصی آرتیست (Target Channel) در صورت تعیین
        if target_channel_id:
            try:
                await deliver_audio_safe(
                    local_bot=local_bot,
                    chat_id=target_channel_id,
                    track_row=track_meta,
                    title=final_title,
                    artist=final_artist,
                    user_caption=f"🎵 *{final_title}*\n👤 {final_artist}\n\n📻 @lyraz_ir"
                )
                logger.info(f"✅ Delivered track {final_title} to target channel {target_channel_id}")
            except Exception as ch_err:
                logger.error(f"Failed to deliver track to target channel {target_channel_id}: {ch_err}")

        # ۹. به‌روزرسانی وضعیت کمپین آرتیست در دیتابیس (در صورت وجود)
        try:
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.execute("""
                    UPDATE campaign_tracks 
                    SET status = 'completed', delivered_at = CURRENT_TIMESTAMP 
                    WHERE youtube_id = ?
                """, (video_id,))
                
                # دریافت campaign_id
                c_row = conn.execute("SELECT campaign_id FROM campaign_tracks WHERE youtube_id = ?", (video_id,)).fetchone()
                if c_row:
                    cid = c_row[0]
                    conn.execute("""
                        UPDATE artist_campaigns 
                        SET completed_tracks = (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = ? AND status = 'completed'),
                            status = CASE 
                                WHEN (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = ? AND status = 'completed') >= total_tracks 
                                THEN 'completed' 
                                ELSE 'processing' 
                            END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (cid, cid, cid))
                conn.commit()
        except Exception as camp_err:
            logger.warning(f"Campaign status update error: {camp_err}")

        if not is_batch:
            return track_meta
        else:
            return (track_meta, final_title, final_artist, user_caption, reply_markup)

    except Exception as e:
        logger.error(f"Worker Task Failed: {e}")
        return False
    finally:
        if path: yt_service.cleanup(path)
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass


# ==========================================
# 🗂 BATCH PLAYLIST PROCESSING
# ==========================================

@huey.task()
def download_playlist_batch(tracks, playlist_name, cover_url, user_id, user_first_name, session_token, chat_id, message_id, quality=None):
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
    local_bot = get_bot_instance()
    total = len(tracks)
    success_count = 0
    failed_count = 0
    processed_count = 0

    state_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(5)
    last_edit_time = 0

    # ۱. بازیابی سریع از مخزن تلگرام در صورت خالی بودن دیتابیس (Cold-Start Vault Sync)
    def check_db_empty():
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            row = conn.execute("SELECT count(*) FROM tracks").fetchone()
            return (row[0] if row else 0) == 0

    if await asyncio.to_thread(check_db_empty):
        logger.info("Database empty on batch start. Running fast Vault Auto-Sync from channel...")
        await sync_vault_from_channel(local_bot)

    # ۲. صف تحویل ترتیبی (Order-Preserved Delivery Queue)
    ready_to_deliver = {}
    delivery_signal = asyncio.Event()
    next_deliver_index = 0
    all_workers_finished = False

    async def delivery_consumer():
        nonlocal next_deliver_index
        while next_deliver_index < total:
            if next_deliver_index in ready_to_deliver:
                item = ready_to_deliver[next_deliver_index]
                if item is not None:
                    track_row, t_title, t_artist, t_caption, t_markup = item
                    try:
                        await deliver_audio_safe(
                            local_bot=local_bot, chat_id=chat_id, track_row=track_row,
                            title=t_title, artist=t_artist, user_caption=t_caption,
                            reply_markup=t_markup
                        )
                        await asyncio.sleep(0.8)
                    except Exception as e:
                        logger.error(f"Sequential delivery error on track {next_deliver_index}: {e}")
                next_deliver_index += 1
            else:
                if all_workers_finished:
                    # اگر تمام دانلودها تمام شده بود و این ایندکس در دیکشنری نبود، رد شویم
                    next_deliver_index += 1
                    continue
                delivery_signal.clear()
                try:
                    await asyncio.wait_for(delivery_signal.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

    consumer_task = asyncio.create_task(delivery_consumer())

    async def update_progress_safe(current_title=""):
        nonlocal last_edit_time
        now = time.time()
        if now - last_edit_time >= 2.5:
            last_edit_time = now
            try:
                progress = generate_progress_bar(processed_count, total)
                status_text = f"🗂 *{playlist_name}*\n\n{progress}\n📥 Processing: _{current_title[:25]}_\n✅ Done: {success_count} | ❌ Fail: {failed_count}"
                await local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=status_text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

    async def process_single_batch_track(idx, track_info):
        nonlocal success_count, failed_count, processed_count
        search_query = track_info['search_query']
        title = track_info['title']
        artist = track_info['artist']
        track_duration = track_info.get('duration')

        async with semaphore:
            await update_progress_safe(title)
            try:
                results = await asyncio.to_thread(yt_service.search, search_query)
                if not results:
                    async with state_lock:
                        failed_count += 1
                        processed_count += 1
                    ready_to_deliver[idx] = None
                    delivery_signal.set()
                    return False

                vid = results[0].get('videoId')

                def check_cache():
                    with sqlite3.connect(Config.DATABASE_URI) as conn:
                        conn.row_factory = sqlite3.Row
                        cur = conn.execute("SELECT * FROM tracks WHERE youtube_id=?", (vid,))
                        return cur.fetchone()

                cached = await asyncio.to_thread(check_cache)

                if cached:
                    d_name = "Hub"
                    reply_markup = None

                    if user_id:
                        from core.services.bot.database import log_user_download
                        await asyncio.to_thread(log_user_download, user_id, cached['id'], 'spotify_playlist', user_first_name)

                    if session_token:
                        def update_hub():
                            with sqlite3.connect(Config.DATABASE_URI) as conn:
                                conn.row_factory = sqlite3.Row  
                                res = conn.execute("SELECT device_name, admin_id FROM sessions WHERE token=?", (session_token,)).fetchone()
                                d_n = res['device_name'] if res and res['device_name'] else f"Hub-{session_token[:4]}"
                                h_admin_id = res['admin_id'] if res else None
                            
                            int_uid = get_user_id(user_id)
                            safe_owner = h_admin_id if h_admin_id else int_uid
                            bot_db_exec("INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) VALUES (?, ?, ?, ?)",
                                        (safe_owner, cached['id'], int_uid, session_token))
                            return d_n

                        d_name = await asyncio.to_thread(update_hub)
                        base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
                        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Open Player", url=f"{base_url}/live/{session_token}")]])

                        notify_web_bridge({
                            'type': 'new_track', 'title': title, 'performer': artist,
                            'file_unique_id': cached['file_unique_id'], 'duration': cached['duration'],
                            'added_by': user_first_name, 'session_token': session_token,
                            'sync_timestamp': time.time()
                        })

                    user_caption = f"🎧 *{title}*\n👤 {artist}" + (f"\n📡 Added to: *{d_name}*" if session_token else "")
                    ready_to_deliver[idx] = (cached, title, artist, user_caption, reply_markup)
                    delivery_signal.set()

                    await process_auto_broadcast(local_bot, cached['file_id'], title, artist, user_first_name)
                    async with state_lock:
                        success_count += 1
                        processed_count += 1
                    return True
                else:
                    result = await _async_logic(vid, title, artist, user_id, user_first_name, session_token, chat_id, message_id=message_id, quality=quality, is_batch=True, duration=track_duration)
                    if result and isinstance(result, tuple):
                        ready_to_deliver[idx] = result
                        delivery_signal.set()
                        async with state_lock:
                            success_count += 1
                            processed_count += 1
                        return True
                    else:
                        ready_to_deliver[idx] = None
                        delivery_signal.set()
                        async with state_lock:
                            failed_count += 1
                            processed_count += 1
                        return False

            except Exception as e:
                logger.error(f"Error processing track {title}: {e}")
                ready_to_deliver[idx] = None
                delivery_signal.set()
                async with state_lock:
                    failed_count += 1
                    processed_count += 1
                return False

    try:
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

        # اجرای موازی تا ۵ ترک همزمان با حفظ ترتیب تحویل
        tasks = [process_single_batch_track(idx, t) for idx, t in enumerate(tracks)]
        await asyncio.gather(*tasks, return_exceptions=True)

        all_workers_finished = True
        delivery_signal.set()
        await consumer_task

        try:
            await local_bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id, 
                text=f"🗂 *{playlist_name}*\n\n{generate_progress_bar(total, total)}\n✅ Processing finished. See summary below.", 
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to update final progress bar: {e}")

        try:
            final_text = f"🎉 *{playlist_name} Download Complete!*\n\n✅ Successfully added: {success_count}\n❌ Failed: {failed_count}"
            await local_bot.send_message(
                chat_id=chat_id,
                text=final_text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send final completion message: {e}")

    except Exception as e:
        logger.error(f"Batch Logic Error: {e}")
    finally:
        try: await local_bot.initialize() ; await local_bot.shutdown()
        except: pass


# ==========================================
# 📣 ADMIN TOOLS: BULK BROADCASTING
# ==========================================

@huey.task()
def send_bulk_message_task(target_telegram_ids, message_text):
    logger.info(f"📣 Starting Bulk Broadcast to {len(target_telegram_ids)} users.")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _async_bulk_broadcast(target_telegram_ids, message_text)
        )
    finally:
        loop.close()

async def _async_bulk_broadcast(target_telegram_ids, message_text):
    local_bot = Bot(token=Config.BOT_TOKEN)
    success_count = 0
    failed_count = 0
    
    for user_id in target_telegram_ids:
        try:
            await local_bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            success_count += 1
        except Exception as e:
            logger.warning(f"[-] Broadcast failed for {user_id}: {e}")
            failed_count += 1
        
        await asyncio.sleep(0.05)
        
    logger.info(f"✅ Broadcast Completed. Success: {success_count} | Failed: {failed_count}")
    try: await local_bot.initialize() ; await local_bot.shutdown()
    except: pass


# ==========================================
# ⏰ AUTONOMOUS CATALOG PRE-WARMER SCHEDULER
# ==========================================

@huey.periodic_task(crontab(minute='0'))
def check_crawler_schedule():
    """
    تسک زمان‌بندی‌شده دوره‌ای: بررسی اجرای کرولر خودکار بر اساس ساعت تنظیم‌شده در پنل ادمین
    """
    try:
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            if not settings or 'crawler_enabled' not in settings.keys() or not settings['crawler_enabled']:
                return

            import datetime
            now_str = datetime.datetime.now().strftime("%H:00")
            sched_str = settings['crawler_schedule_hour'] or '04:00'
            
            # تطابق ساعت اجرای مشخص‌شده
            if now_str[:2] == sched_str[:2]:
                logger.info(f"🚀 [Auto-Prewarmer] Triggering Scheduled Crawler for {sched_str}...")
                from core.services.crawler import crawler_service
                source = settings['crawler_source'] or 'global_top_50'
                max_tracks = settings['crawler_max_tracks'] or 15
                
                if source == 'persian_trending':
                    tracks = crawler_service.get_persian_trending(limit=max_tracks * 2)
                else:
                    chart_res = crawler_service.get_spotify_chart(source)
                    tracks = chart_res.get('tracks', [])
                    
                res = crawler_service.ingest_tracks_one_by_one(tracks, source_label=f"auto_{source}", max_limit=max_tracks)
                logger.info(f"✅ [Auto-Prewarmer] Dispatched: {res['queued']} queued, {res['skipped']} skipped.")
    except Exception as e:
        logger.error(f"Error in check_crawler_schedule: {e}")