# core/services/bot/logic.py

import json
import logging
import sqlite3
import os
import requests  # برای ارتباط با کانتینر وب
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError

from core.config import Config
# ❌ announcer حذف شد چون در کانتینر جدا کار نمی‌کند
from core.services.youtube import YouTubeService
from .database import (
    bot_db_exec, get_user_id, get_user_current_session, 
    get_session_info, get_settings, get_channel_template,
    get_track_by_youtube_id
)

logger = logging.getLogger(__name__)

# سرویس یوتیوب برای استفاده در منطق داخلی (دانلودر)
yt_service = YouTubeService()

# --- 🔥 تابع حیاتی: پل ارتباطی با کانتینر وب (Container Bridge) ---
def notify_web_container(data_dict):
    """
    ارسال دیتا به کانتینر وب برای پخش در سیستم SSE.
    ربات (در کانتینر Bot) پیام را به وب (در کانتینر Web) می‌فرستد.
    """
    try:
        # نام 'web' همان نام سرویس در docker-compose.yml است
        # پورت 5000 پورت داخلی کانتینر وب است
        url = "http://web:5000/internal/announce"
        
        # فرمت استاندارد SSE: "data: ... \n\n"
        sse_msg = f"data: {json.dumps(data_dict)}\n\n"
        
        # ارسال درخواست با تایم‌اوت کوتاه (۱ ثانیه) که اگر وب قطع بود ربات گیر نکند
        requests.post(url, json={'message': sse_msg}, timeout=1)
    except Exception as e:
        logger.error(f"Failed to bridge message to Web container: {e}")

async def activate_session_and_notify(session_token, user_id, user_first_name, context):
    """
    ۱. سشن را در دیتابیس فعال می‌کند.
    ۲. از طریق پل ارتباطی به وب خبر می‌دهد تا تلویزیون لاگین شود.
    """
    internal_user_id = get_user_id(user_id)
    
    # ۱. بررسی وجود سشن
    session = get_session_info(session_token)
    if not session:
        return None  # سشن وجود ندارد یا نامعتبر است

    is_new_admin = False
    
    # سناریو ۱: سشن هنوز ادمین ندارد -> کاربر فعلی ادمین می‌شود
    if session['admin_id'] is None:
        bot_db_exec(
            "UPDATE sessions SET status='active', admin_id=? WHERE token=?", 
            (internal_user_id, session_token)
        )
        is_new_admin = True
        
    # سناریو ۲: کاربر فعلی همان ادمین است -> فقط وضعیت اکتیو می‌شود
    elif session['admin_id'] == internal_user_id:
        bot_db_exec(
            "UPDATE sessions SET status='active' WHERE token=?", 
            (session_token,)
        )
    
    # ۲. 🔥 ارسال سیگنال به وب (جایگزین Polling قدیمی) 🔥
    announcement = {
        "type": "session_activated",
        "session_token": session_token,
        "admin": {
            "name": user_first_name,
            "id": user_id,
            # اگر کاربر هنوز اسم نگذاشته، ۴ حرف اول توکن نمایش داده شود
            "device_display_name": session['device_name'] or session_token[:4]
        }
    }
    
    # استفاده از تابع پل (Bridge)
    notify_web_container(announcement)
    
    return is_new_admin

async def ensure_track_and_process(update, context, video_id, title, artist):
    """
    مدیریت هوشمند دریافت موزیک (Archive-First Strategy):
    ۱. ابتدا آرشیو (دیتابیس) را چک می‌کند.
    ۲. اگر بود -> همان فایل را پردازش می‌کند (بدون دانلود مجدد).
    ۳. اگر نبود -> دانلود، آپلود به آرشیو، و سپس پردازش.
    """
    chat_id = update.effective_chat.id
    
    # ۱. بررسی وجود در آرشیو (Cache Hit)
    cached = get_track_by_youtube_id(video_id)
    
    if cached:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
        await update.message.reply_text(f"🚀 Found in Archive: *{title}*", parse_mode=ParseMode.MARKDOWN)
        
        track_meta = {
            'file_unique_id': cached['file_unique_id'], 
            'file_id': cached['file_id'],
            'title': cached['title'], 
            'performer': cached['performer'],
            'duration': cached['duration'], 
            'file_size': cached['file_size'],
            'thumb_id': cached['thumb_id'], 
            'youtube_id': video_id
        }
        await process_track_and_queue(update, context, track_meta)
        return

    # ۲. دانلود جدید (Cache Miss)
    status = await update.message.reply_text(f"📥 Downloading *{title}*...", parse_mode=ParseMode.MARKDOWN)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
    
    # انجام دانلود (اینجا هنوز در ترد ربات است، در فاز بعدی به Worker منتقل می‌شود)
    path = await yt_service.download(video_id)
    
    if path:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
            
            if Config.STORAGE_CHANNEL_ID:
                # ارسال به کانال آرشیو برای دریافت File ID تلگرام
                with open(path, 'rb') as f:
                    sent = await context.bot.send_audio(
                        chat_id=Config.STORAGE_CHANNEL_ID,
                        audio=f,
                        title=title,
                        performer=artist,
                        caption=f"YT: {video_id}",
                        write_timeout=300,
                        read_timeout=300
                    )
                
                track_meta = {
                    'file_unique_id': sent.audio.file_unique_id,
                    'file_id': sent.audio.file_id,
                    'title': title,
                    'performer': artist,
                    'duration': sent.audio.duration,
                    'file_size': sent.audio.file_size,
                    'thumb_id': sent.audio.thumbnail.file_id if sent.audio.thumbnail else None,
                    'youtube_id': video_id
                }
                
                await status.delete()
                # ادامه پروسه با متادیتای جدید
                await process_track_and_queue(update, context, track_meta)
            else:
                await status.edit_text("❌ Configuration Error: STORAGE_CHANNEL_ID missing.")
                
        except TelegramError as e:
            logger.error(f"Telegram Upload Error: {e.message}")
            if "chat not found" in str(e).lower():
                 await status.edit_text(
                     f"❌ Error: Channel not found.\nID Used: `{Config.STORAGE_CHANNEL_ID}`\n"
                     "Make sure bot is Admin and ID starts with -100.", 
                     parse_mode=ParseMode.MARKDOWN
                 )
            else:
                 await status.edit_text(f"❌ Telegram Error: {e.message}")

        except Exception as e:
            logger.error(f"General Upload Error: {e}")
            await status.edit_text("❌ Upload failed.")
        finally:
            # پاکسازی فایل از دیسک برای جلوگیری از پر شدن سرور
            yt_service.cleanup(path)
    else:
        await status.edit_text("❌ Download failed.")

async def process_track_and_queue(update, context, track_meta):
    """
    هسته مرکزی پردازش موزیک:
    ۱. ثبت در دیتابیس.
    ۲. ارسال فایل برای کاربر.
    ۳. اضافه کردن به صف پخش (اگر کاربر به دستگاهی متصل باشد).
    """
    user = update.effective_user
    internal_user_id = get_user_id(user.id)
    
    # 1. ثبت یا آپدیت در جدول tracks
    try:
        sql = """
            INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, youtube_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_id) DO UPDATE SET file_id=excluded.file_id
        """ if track_meta.get('youtube_id') else """
            INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_unique_id) DO UPDATE SET file_id=excluded.file_id
        """
        
        args = [
            track_meta['file_unique_id'], track_meta['file_id'], 
            track_meta['title'], track_meta['performer'], 
            track_meta['duration'], track_meta['file_size'], 
            track_meta['thumb_id']
        ]
        if track_meta.get('youtube_id'):
            args.append(track_meta['youtube_id'])
            
        bot_db_exec(sql, tuple(args))
    except Exception as e:
        logger.error(f"DB Insert Error: {e}")

    # 2. دریافت ID داخلی ترک برای جدول playlist_items
    with sqlite3.connect(Config.DATABASE_URI) as conn:
        key = 'youtube_id' if track_meta.get('youtube_id') else 'file_unique_id'
        val = track_meta.get('youtube_id') if track_meta.get('youtube_id') else track_meta['file_unique_id']
        res = conn.execute(f"SELECT id FROM tracks WHERE {key}=?", (val,)).fetchone()
        track_id = res[0] if res else None

    if not track_id:
        return

    # --- بخش اول: ارسال فایل برای کاربر (مستقل از دستگاه) ---
    if track_meta.get('youtube_id'):
        await context.bot.send_chat_action(chat_id=user.id, action=ChatAction.UPLOAD_VOICE)
        try:
            caption = f"🎧 *{track_meta['title']}*\n👤 {track_meta['performer']}"
            if Config.BOT_USERNAME:
                caption += f"\n🆔 @{Config.BOT_USERNAME}"
            
            await context.bot.send_audio(
                chat_id=user.id,
                audio=track_meta['file_id'],
                title=track_meta['title'],
                performer=track_meta['performer'],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send audio back to user: {e}")

    # --- بخش دوم: صف پخش دیوایس ---
    target_token = get_user_current_session(user.id)
    
    if not target_token:
        # اگر کاربر فایل دستی فرستاده ولی وصل نیست، هشدار بده
        if not track_meta.get('youtube_id'):
            await context.bot.send_message(user.id, "⚠️ To play this on TV, please select a device first (/devices).")
        return

    session = get_session_info(target_token)
    if session:
        # ثبت در پلی‌لیست
        bot_db_exec("""
            INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
            VALUES (?, ?, ?, ?)
        """, (internal_user_id, track_id, internal_user_id, target_token))
        
        # 🔥 خبر دادن به تلویزیون از طریق پل ارتباطی
        track_data = {
            'title': track_meta['title'], 'performer': track_meta['performer'],
            'file_unique_id': track_meta['file_unique_id'], 'duration': track_meta['duration'],
            'added_by': user.first_name, 'session_token': target_token
        }
        # استفاده از تابع notify_web_container
        notify_web_container(track_data)
        
        d_name = session['device_name'] or target_token[:4]
        await context.bot.send_message(
            user.id, 
            f"✅ Added *{track_meta['title']}* to Queue on *{d_name}*.", 
            parse_mode=ParseMode.MARKDOWN
        )

        await handle_broadcast(context, user, track_meta['file_id'], track_meta, session)
    else:
        await context.bot.send_message(user.id, "⚠️ Selected device is offline or invalid.")

async def handle_broadcast(context, user, file_id, meta, session):
    """
    برودکست کردن موزیک به کانال‌های متصل شده
    """
    settings = get_settings()
    target_channel_id = session['linked_channel_id']
    channel_tmpl = None
    
    if target_channel_id:
        channel_tmpl = get_channel_template(target_channel_id)
    elif settings and settings['is_auto_broadcast_enabled'] and settings['auto_broadcast_channel_id']:
        target_channel_id = settings['auto_broadcast_channel_id']
    
    if target_channel_id:
        final_tmpl = channel_tmpl if channel_tmpl else (settings['default_caption'] if settings else "{title} - {artist}")
        caption = final_tmpl.replace('{title}', meta['title']).replace('{artist}', meta['performer']).replace('{sender}', user.first_name)
        try:
            await context.bot.send_audio(chat_id=target_channel_id, audio=file_id, caption=caption)
        except Exception as e:
            logger.error(f"Broadcast Failed: {e}")