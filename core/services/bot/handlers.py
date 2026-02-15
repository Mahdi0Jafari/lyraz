# core/routes/services/bot/handlers.py

import uuid
import logging
from telegram import Update, ForceReply, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError

from core.config import Config
from core.services.youtube import YouTubeService
from .database import (
    bot_db_exec, get_user_id, update_user_session, get_session_info,
    get_user_current_session, set_device_name, get_active_sessions,
    get_track_by_youtube_id
)
from .keyboards import get_main_menu_keyboard, get_smart_buttons

# 🔥 ایمپورت توابع حیاتی از لاجیک
from .logic import (
    process_track_and_queue, 
    ensure_track_and_process, 
    activate_session_and_notify
)

logger = logging.getLogger(__name__)

# سرویس یوتیوب فقط برای سرچ اینلاین
yt_service = YouTubeService()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    # نمایش تایپینگ برای حس زنده بودن
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    # ثبت نام کاربر (اگر یوزر وجود داشته باشد)
    if user:
        try:
            bot_db_exec("INSERT OR IGNORE INTO users (telegram_id, first_name, username) VALUES (?, ?, ?)", 
                       (user.id, user.first_name, user.username))
        except: pass
    
    # اگر پیام سیستم باشد و یوزر نداشته باشد، ادامه نده
    if not user:
        return

    internal_user_id = get_user_id(user.id)

    # سناریوی اسکن QR
    if args and args[0].startswith('session_'):
        token = args[0].split('_')[1]
        
        # ۱. تنظیم سشن فعلی کاربر در ربات
        update_user_session(user.id, token)
        
        # ۲. 🔥 فعال‌سازی سشن و ارسال نوتیفیکیشن SSE به تلویزیون
        # این تابع هم دیتابیس را آپدیت می‌کند و هم به تلویزیون می‌گوید "لاگین شو"
        is_new_admin = await activate_session_and_notify(token, user.id, user.first_name, context)
        
        if is_new_admin is None:
            await update.message.reply_text("❌ Invalid or Expired QR code.")
            return

        # گرفتن اطلاعات تازه برای نمایش
        session = get_session_info(token)
        d_name = session['device_name'] or f"`{token[:4]}`"

        if is_new_admin:
            # اگر ادمین جدید است، درخواست نام‌گذاری بده
            context.user_data['renaming_token'] = token
            await update.message.reply_text(
                f"🚀 *Connected!*\nYou are now the Admin of this device.\n\n✍️ Please send a *Name* for it:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ForceReply(selective=True)
            )
        else:
            # اگر قبلاً ادمین داشته یا مهمان است
            role = "Admin" if session['admin_id'] == internal_user_id else "Guest"
            await update.message.reply_text(
                f"✅ *Connected to {d_name}*\n👤 Role: *{role}*", 
                reply_markup=get_main_menu_keyboard(), 
                parse_mode=ParseMode.MARKDOWN
            )

    else:
        # سناریوی دستور /start خالی
        current = get_user_current_session(user.id)
        msg = f"👋 *Welcome {user.first_name}!*\nScan a QR code on your TV to start."
        
        if current:
            sess = get_session_info(current)
            d_name = sess['device_name'] if sess else "Unknown"
            msg += f"\n\n🟢 Active Device: *{d_name}*"
        else:
            msg += "\n⚠️ No device connected."
            
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard())

async def list_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    user = update.effective_user
    internal_uid = get_user_id(user.id)
    current_token = get_user_current_session(user.id)
    
    sessions = get_active_sessions(internal_uid)
    session_list = [dict(s) for s in sessions]
    
    # اگر دیوایس فعلی انتخاب شده ولی مال کاربر نیست (Guest Mode)، به لیست اضافه کن
    if current_token:
        is_owned = any(s['token'] == current_token for s in session_list)
        if not is_owned:
            guest = get_session_info(current_token)
            if guest:
                fake = dict(guest)
                fake['is_guest_entry'] = True
                session_list.insert(0, fake)

    if not session_list:
        await update.message.reply_text("❌ No active devices found.")
        return

    await update.message.reply_text("📱 *Your Devices:*", parse_mode=ParseMode.MARKDOWN)
    for sess in session_list:
        token = sess['token']
        d_name = sess['device_name'] or token[:4]
        is_cur = (token == current_token)
        
        if sess.get('is_guest_entry'):
            label = f"👤 {d_name} (Guest)"
        else:
            label = f"📺 {d_name}"
            
        if is_cur: 
            label = f"🟢 {d_name} (Selected)"
            
        await update.message.reply_text(label, reply_markup=get_smart_buttons(token, is_cur))

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("select_"):
        target = data.split("_")[1]
        update_user_session(user_id, target)
        sess = get_session_info(target)
        d_name = sess['device_name'] or target[:4]
        
        # آپدیت دکمه‌ها
        await query.edit_message_reply_markup(reply_markup=get_smart_buttons(target, True))
        await context.bot.send_message(user_id, f"✅ Switched to: *{d_name}*", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("rename_"):
        token = data.split("_")[1]
        sess = get_session_info(token)
        
        if sess['admin_id'] != get_user_id(user_id):
            await context.bot.send_message(user_id, "⛔️ Access Denied: Only Admin can rename.")
            return
            
        context.user_data['renaming_token'] = token
        await context.bot.send_message(
            user_id, 
            f"✍️ Enter new name for `{sess['device_name']}`:", 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=ForceReply(selective=True)
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # دستورات منوی اصلی
    if text == "📱 My Devices": await list_devices(update, context); return
    if text == "❓ Help": await update.message.reply_text("📚 Guide: Scan QR code on TV, then search/send music."); return

    # سناریوی نام‌گذاری دستگاه
    if 'renaming_token' in context.user_data:
        token = context.user_data['renaming_token']
        set_device_name(token, text.strip())
        del context.user_data['renaming_token']
        
        await update.message.reply_text(
            f"✅ Device renamed to: *{text.strip()}*", 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    await update.message.reply_text("🎵 Use Inline Search (@bot music) or send an Audio file.")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    مدیریت فایل‌های صوتی ارسالی.
    شامل فیکس جلوگیری از کرش در صورت نبودن User (کانال‌ها).
    """
    # ۱. بررسی وجود پیام و فایل صوتی
    if not update.message or not update.message.audio:
        return

    # ۲. استفاده از chat.id به جای user.id برای جلوگیری از کرش
    # چون effective_user در کانال‌ها None است، اما effective_chat همیشه هست
    chat_id = update.effective_chat.id if update.effective_chat else None
    
    if chat_id:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
    
    # اگر یوزر مشخص نیست (مثلاً از کانال فوروارد شده بدون امضا)، لاجیک ممکن است نیاز به اصلاح داشته باشد
    # اما فعلاً برای جلوگیری از کرش، اگر یوزر نباشد ادامه نمی‌دهیم (یا می‌توانید یوزر پیش‌فرض بگذارید)
    if not update.effective_user:
        return

    audio = update.message.audio
    
    meta = {
        'file_unique_id': audio.file_unique_id, 'file_id': audio.file_id,
        'title': audio.title or "Unknown Track", 'performer': audio.performer or "Unknown Artist",
        'duration': audio.duration, 'file_size': audio.file_size,
        'thumb_id': audio.thumbnail.file_id if audio.thumbnail else None,
        'youtube_id': None
    }
    
    # ارسال به logic برای پردازش و پخش
    await process_track_and_queue(update, context, meta)

# --- YouTube Handlers ---

async def inline_music_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query: return
    try:
        results = yt_service.search(query)
        articles = []
        for song in results:
            vid = song.get('videoId')
            
            # چک کردن اینکه آیا قبلاً دانلود شده (برای نمایش ✅)
            cached = get_track_by_youtube_id(vid)
            prefix = "✅ " if cached else ""
            
            # ساخت محتوای پیام (فرمت /dl)
            content = InputTextMessageContent(f"/dl {vid} | {song.get('title')} :: {song.get('artists', [{}])[0].get('name')}")
            
            articles.append(InlineQueryResultArticle(
                id=str(uuid.uuid4()), 
                title=f"{prefix}{song.get('title')}",
                description=f"{song.get('artists', [{}])[0].get('name')}",
                thumbnail_url=song.get('thumbnails', [{}])[-1].get('url'),
                input_message_content=content
            ))
        await context.bot.answer_inline_query(update.inline_query.id, articles, cache_time=0)
    except Exception as e:
        logger.error(f"Inline Search Error: {e}")

async def youtube_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    هندلر دانلود (/dl):
    پیام را پارس می‌کند و به ensure_track_and_process می‌سپارد.
    """
    msg = update.message.text
    try:
        # پارس کردن فرمت: /dl video_id | Title :: Artist
        parts = msg.replace('/dl ', '').split('|')
        vid = parts[0].strip()
        
        meta_part = parts[1].strip() if len(parts) > 1 else "Unknown :: Unknown"
        if '::' in meta_part:
            title, artist = meta_part.split('::')
        else:
            title, artist = meta_part, "Unknown"
        
        # 🔥 ارسال به لاجیک مرکزی برای دانلود/کش/پخش
        await ensure_track_and_process(update, context, vid, title.strip(), artist.strip())

    except Exception as e:
        logger.error(f"Handler Error: {e}")
        await update.message.reply_text("❌ Error processing request.")

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # اینجا می‌توانید لاجیک اضافه شدن ربات به کانال‌ها را مدیریت کنید
    pass