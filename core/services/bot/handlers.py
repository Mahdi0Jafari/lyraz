# core/services/bot/handlers.py

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
# استفاده از کیبوردها (مطمئن شوید فایل keyboards.py هم انگلیسی شده باشد)
from .keyboards import get_main_menu_keyboard, get_smart_buttons, get_onboarding_keyboard

from .logic import (
    process_track_and_queue, 
    ensure_track_and_process, 
    activate_session_and_notify
)

logger = logging.getLogger(__name__)
yt_service = YouTubeService()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main Entry Point (Fully English & Onboarding Optimized)
    """
    user = update.effective_user
    args = context.args
    
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    # Register User
    if user:
        bot_db_exec("INSERT OR IGNORE INTO users (telegram_id, first_name, username) VALUES (?, ?, ?)", 
                   (user.id, user.first_name, user.username))
    
    if not user: return

    # --- Scenario 1: Connect via QR Code ---
    if args and args[0].startswith('session_'):
        token = args[0].split('_')[1]
        
        # 1. Update Session & Activate
        update_user_session(user.id, token)
        is_new_admin = await activate_session_and_notify(token, user.id, user.first_name, context)
        
        if is_new_admin is None:
            await update.message.reply_text("❌ Invalid or Expired QR code.")
            return

        session = get_session_info(token)
        d_name = session['device_name'] or f"`{token[:4]}`"

        if is_new_admin:
            # New Admin Flow: Ask for Name
            context.user_data['renaming_token'] = token
            await update.message.reply_text(
                f"🎉 *Connected Successfully!*\nYou are now the Admin of this device.\n\n✍️ Please enter a *Name* for this screen:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ForceReply(selective=True)
            )
        else:
            # Standard Welcome
            msg = (
                f"✅ *Connected to {d_name}*\n"
                f"You can now search and play music on this device."
            )
            await update.message.reply_text(
                msg, 
                reply_markup=get_onboarding_keyboard(token), 
                parse_mode=ParseMode.MARKDOWN
            )

    else:
        # --- Scenario 2: Normal Start ---
        current_token = get_user_current_session(user.id)
        
        if current_token:
            sess = get_session_info(current_token)
            d_name = sess['device_name'] if sess else "Unknown"
            msg = (
                f"🟢 *Welcome Back, {user.first_name}!*\n"
                f"Connected to: *{d_name}*\n\n"
                f"👇 Tap *Search Music* to find songs on YouTube."
            )
        else:
            msg = (
                f"👋 *Hi {user.first_name}!*\n\n"
                "⛔️ *No Device Connected.*\n\n"
                "To start playing music:\n"
                "1. Open the player website on your TV/Laptop.\n"
                "2. Scan the QR code on the screen.\n"
                "3. Press Start here."
            )
            
        await update.message.reply_text(
            msg, 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=get_onboarding_keyboard(current_token)
        )

async def list_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show List of Connected Devices"""
    user = update.effective_user
    internal_uid = get_user_id(user.id)
    current_token = get_user_current_session(user.id)
    
    sessions = get_active_sessions(internal_uid)
    session_list = [dict(s) for s in sessions]
    
    # Add Guest Device if active
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
        
        label = f"👤 {d_name} (Guest)" if sess.get('is_guest_entry') else f"📺 {d_name}"
        if is_cur: label = f"🟢 {d_name} (Selected)"
            
        await update.message.reply_text(label, reply_markup=get_smart_buttons(token, is_cur))

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Inline Buttons"""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    
    # --- Help Buttons ---
    if data == "help_connect":
        await context.bot.send_message(user.id, "💡 *How to Connect:*\nOpen the website on your TV/PC and scan the QR code displayed there.", parse_mode=ParseMode.MARKDOWN)
        return
    elif data == "help_upload":
        await context.bot.send_message(user.id, "🎙 *Upload Music:*\nSimply forward any MP3 file from other chats to this bot, or upload a file directly.")
        return

    # --- Device Management ---
    if data.startswith("select_"):
        target = data.split("_")[1]
        update_user_session(user.id, target)
        sess = get_session_info(target)
        d_name = sess['device_name'] or target[:4]
        
        await query.edit_message_reply_markup(reply_markup=get_smart_buttons(target, True))
        await context.bot.send_message(user.id, f"✅ Active Device: *{d_name}*", parse_mode=ParseMode.MARKDOWN)

    # 🔥🔥🔥 FIX: Manage Button Handler Added 🔥🔥🔥
    elif data.startswith("manage_"):
        token = data.split("_")[1]
        current_token = get_user_current_session(user.id)
        is_cur = (token == current_token)
        
        # Show device options (Rename, Remote, Select)
        await query.edit_message_reply_markup(reply_markup=get_smart_buttons(token, is_cur))

    elif data.startswith("rename_"):
        token = data.split("_")[1]
        sess = get_session_info(token)
        if sess['admin_id'] != get_user_id(user.id):
            await context.bot.send_message(user.id, "⛔️ Access Denied: Only Admin can rename.")
            return
        context.user_data['renaming_token'] = token
        await context.bot.send_message(
            user.id, 
            f"✍️ Enter new name for `{sess['device_name']}`:", 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=ForceReply(selective=True)
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Check English Menu Buttons
    if text == "📱 My Devices": 
        await list_devices(update, context); return
    if text == "❓ Help": 
        await update.message.reply_text("📚 *Guide:* Scan QR on TV to connect. Use 'Search' to find music on YouTube."); return

    # Renaming Logic
    if 'renaming_token' in context.user_data:
        token = context.user_data['renaming_token']
        set_device_name(token, text.strip())
        del context.user_data['renaming_token']
        await update.message.reply_text(
            f"✅ Device renamed to: *{text.strip()}*", 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=get_onboarding_keyboard(token) # Return to main menu
        )
        return
    
    # Generic Text -> Assume Search Intent
    current = get_user_current_session(update.effective_user.id)
    await update.message.reply_text(
        "👇 Tap the button below to search YouTube:", 
        reply_markup=get_onboarding_keyboard(current)
    )

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message.audio: return
    
    # Direct processing for uploaded files (No Huey needed for local)
    audio = update.message.audio
    meta = {
        'file_unique_id': audio.file_unique_id, 'file_id': audio.file_id,
        'title': audio.title or "Unknown Track", 'performer': audio.performer or "Unknown Artist",
        'duration': audio.duration, 'file_size': audio.file_size,
        'thumb_id': audio.thumbnail.file_id if audio.thumbnail else None,
        'youtube_id': None
    }
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
            cached = get_track_by_youtube_id(vid)
            prefix = "✅ " if cached else ""
            
            # Content sends the /dl command
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
    Download Handler (/dl):
    Offloads heavy download to Huey Worker.
    """
    # 🔥🔥🔥 Circular Import Fix: Local Import Here 🔥🔥🔥
    from core.tasks import download_and_process_track

    msg = update.message.text
    user = update.effective_user
    
    try:
        parts = msg.replace('/dl ', '').split('|')
        vid = parts[0].strip()
        meta_part = parts[1].strip() if len(parts) > 1 else "Unknown :: Unknown"
        
        if '::' in meta_part:
            title, artist = meta_part.split('::')
        else:
            title, artist = meta_part, "Unknown"
        
        title = title.strip()
        artist = artist.strip()

        # 1. Check Cache (Fast Path)
        cached = get_track_by_youtube_id(vid)
        if cached:
            await ensure_track_and_process(update, context, vid, title, artist)
            return

        # 2. Cache Miss -> Send to Worker
        status_msg = await update.message.reply_text(
            f"⏳ *{title}* added to download queue...", 
            parse_mode=ParseMode.MARKDOWN
        )
        
        current_token = get_user_current_session(user.id)
        
        # Call Huey Task
        download_and_process_track(
            video_id=vid, 
            title=title, 
            artist=artist, 
            user_id=user.id, 
            user_first_name=user.first_name, 
            session_token=current_token,
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id
        )

    except Exception as e:
        logger.error(f"Handler Error: {e}")
        await update.message.reply_text("❌ Error processing request.")

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass