# core/services/bot/handlers.py

import uuid
import re
import logging
# 🔥 FIXED: Added missing imports (InlineKeyboardMarkup, InlineKeyboardButton)
from telegram import Update, ForceReply, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction

from core.config import Config
from core.services.youtube import YouTubeService
from core.services.spotify_official import spotify_keyless 
from .database import (
    bot_db_exec, get_user_id, update_user_session, get_session_info,
    get_user_current_session, set_device_name, get_active_sessions,
    get_track_by_youtube_id
)
from .keyboards import get_main_menu_keyboard, get_smart_buttons, get_onboarding_keyboard
from .logic import (
    process_track_and_queue, 
    ensure_track_and_process, 
    activate_session_and_notify
)

logger = logging.getLogger(__name__)
yt_service = YouTubeService()

# ==========================================
# 🚀 CORE COMMANDS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main Entry Point & Onboarding Hub"""
    user = update.effective_user
    args = context.args
    
    if update.effective_chat:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    if user:
        bot_db_exec("INSERT OR IGNORE INTO users (telegram_id, first_name, username) VALUES (?, ?, ?)", 
                   (user.id, user.first_name, user.username))
    if not user: return

    # --- Scenario 1: Connect via QR Code ---
    if args and args[0].startswith('session_'):
        token = args[0].split('_')[1]
        
        update_user_session(user.id, token)
        is_new_admin = await activate_session_and_notify(token, user.id, user.first_name, context)
        
        if is_new_admin is None:
            await update.message.reply_text("❌ Invalid or Expired QR code.")
            return

        session = get_session_info(token)
        d_name = session['device_name'] or f"`{token[:4]}`"

        if is_new_admin:
            context.user_data['renaming_token'] = token
            await update.message.reply_text(
                f"🎉 *Connection Successful!*\n\nYou are now the Admin of this device.\n✍️ Please enter a *Name* for this screen (e.g., Living Room TV):",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=ForceReply(selective=True)
            )
        else:
            await update.message.reply_text(
                f"✅ *Connected to {d_name}*\n\nEverything you search or download here will now play on your screen.", 
                reply_markup=get_main_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN
            )

    else:
        # --- Scenario 2: Normal Start (Discovery Engine UX) ---
        current_token = get_user_current_session(user.id)
        
        welcome_msg = (
            f"👋 *Welcome to Fanus, {user.first_name}!*\n"
            "Your personal bridge for seamless music streaming.\n\n"
            "🎼 *What can I do?*\n"
            "📥 *Download:* Paste a Spotify/YouTube link to save tracks.\n"
            "🔍 *Search:* Instantly find any song from the global database.\n"
            "📺 *Stream:* Connect to your TV/PC for a synchronized experience.\n\n"
        )
        
        if current_token:
            sess = get_session_info(current_token)
            d_name = sess['device_name'] if sess else "Unknown"
            welcome_msg += f"🟢 *Status:* Currently synced with *{d_name}*.\n\n👇 *Get started:* Use the menu below or send a music link."
        else:
            welcome_msg += "👇 *Get started:* Use the menu below, or tap to open the Web Player."

        await update.message.reply_text(
            welcome_msg, 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=get_main_menu_keyboard()
        )
        
        await update.message.reply_text(
            "⚡️ *Quick Actions:*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_onboarding_keyboard(current_token)
        )

# ==========================================
# 📡 LINK PARSERS & DIRECT DOWNLOADS
# ==========================================

async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Extracts YouTube Video ID and dispatches to Huey"""
    match = re.search(r'(?:v=|/)([0-9A-Za-z_-]{11}).*', url)
    if not match:
        await update.message.reply_text("❌ Invalid YouTube link format.")
        return
        
    vid = match.group(1)
    status_msg = await update.message.reply_text("⏳ Processing YouTube track...")
    
    try:
        results = yt_service.search(vid)
        title = results[0]['title'] if results else "YouTube Track"
        artist = results[0]['artists'][0]['name'] if results and results[0].get('artists') else "Unknown"
    except:
        title, artist = "Unknown Track", "Unknown Artist"

    await dispatch_to_huey(update, context, vid, title, artist, status_msg)


async def handle_spotify_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Processes Spotify links (Tracks or Playlists) using the Embed Scraper"""
    status_msg = await update.message.reply_text("🔎 Analyzing Spotify link...")
    
    sp_data = spotify_keyless.parse_link(url)
    
    if sp_data.get('status') == 'error':
        await status_msg.edit_text(f"❌ {sp_data.get('message')}")
        return

    # --- Case 1: Single Track ---
    if sp_data['type'] == 'track':
        await status_msg.edit_text(f"🔎 Matching *{sp_data['title']}* on global database...", parse_mode=ParseMode.MARKDOWN)
        results = yt_service.search(sp_data['search_query'])
        if not results:
            await status_msg.edit_text("❌ Could not find a match for this specific track.")
            return
            
        vid = results[0]['videoId']
        await dispatch_to_huey(update, context, vid, sp_data['title'], sp_data['artist'], status_msg)

    # --- Case 2: Playlist or Album ---
    elif sp_data['type'] in ['playlist', 'album']:
        tracks = sp_data['tracks']
        await status_msg.edit_text(
            f"📥 Found *{len(tracks)}* tracks in this {sp_data['type']}.\nAdding to download queue...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        try: await status_msg.delete()
        except: pass
        
        from core.tasks import process_spotify_playlist_item
        current_token = get_user_current_session(update.effective_user.id)
        
        for track in tracks:
            process_spotify_playlist_item(
                search_query=track['search_query'],
                expected_title=track['title'],
                expected_artist=track['artist'],
                user_id=update.effective_user.id,
                user_first_name=update.effective_user.first_name,
                session_token=current_token,
                chat_id=update.effective_chat.id
            )
            
        await update.message.reply_text(f"✅ {len(tracks)} items have been successfully dispatched to the background worker.")

async def dispatch_to_huey(update: Update, context: ContextTypes.DEFAULT_TYPE, vid, title, artist, status_msg):
    """Helper function to route downloads to the background worker"""
    from core.tasks import download_and_process_track
    user = update.effective_user
    current_token = get_user_current_session(user.id)
    
    # 1. Check Cache Hit
    cached = get_track_by_youtube_id(vid)
    if cached:
        try: await status_msg.delete()
        except: pass
        await ensure_track_and_process(update, context, video_id=vid, title=title, artist=artist)
        return

    # 2. Cache Miss -> Send to Worker
    download_quality = Config.AUDIO_QUALITY if hasattr(Config, 'AUDIO_QUALITY') else '192'

    await status_msg.edit_text(f"⏳ *{title}* added to the queue...", parse_mode=ParseMode.MARKDOWN)
    download_and_process_track(
        video_id=vid, title=title, artist=artist, 
        user_id=user.id, user_first_name=user.first_name, 
        session_token=current_token, chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        quality=download_quality
    )

# ==========================================
# 💬 TEXT & NAVIGATION HANDLER
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # --- 1. Persistent Menu Routing ---
    if text == "📺 My Devices" or text == "📱 My Devices": 
        await list_devices(update, context)
        return
        
    if text == "📖 Setup Guide" or text == "❓ Help": 
        guide_text = (
            "🚀 *Quick Setup Guide:*\n\n"
            "1️⃣ *To Play on TV/PC:* Open the Fanus website on your screen and scan the QR code using your phone's camera.\n"
            "2️⃣ *Spotify Downloads:* Just copy any track or playlist link from Spotify and paste it here.\n"
            "3️⃣ *Direct Uploads:* Forward any MP3 file to me, and I'll add it to your synchronized queue.\n\n"
            "Need a specific song? Just use the *Search Music* button!"
        )
        await update.message.reply_text(guide_text, parse_mode=ParseMode.MARKDOWN)
        return
        
    if text == "🔍 Search Music":
        # Actionable instruction rather than a dead end
        await update.message.reply_text(
            "🔎 *How to Search:*\nSimply type `@naqoosbot [song name]` right here in the chat, or tap the button below!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Open Search Panel", switch_inline_query_current_chat="")]])
        )
        return
        
    if text == "📥 Download Link":
        await update.message.reply_text("🔗 Send me a valid *Spotify* or *YouTube* link, and I'll start downloading it immediately.", parse_mode=ParseMode.MARKDOWN)
        return

    # --- 2. Renaming Flow ---
    if 'renaming_token' in context.user_data:
        token = context.user_data['renaming_token']
        set_device_name(token, text)
        del context.user_data['renaming_token']
        await update.message.reply_text(f"✅ Device successfully renamed to: *{text}*", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard())
        return
    
    # --- 3. Smart Link Detection (Zero-Question Processing) ---
    if re.match(r'(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/.+', text):
        await handle_youtube_link(update, context, text)
        return
        
    if re.match(r'(https?://)?(open\.spotify\.com)/.+', text):
        await handle_spotify_link(update, context, text)
        return

    # --- 4. Search Fallback (Zero-Question Search Initiation) ---
    # If it's not a menu command and not a link, we assume it's a search query
    status_msg = await update.message.reply_text(f"🔎 Searching for *{text}*...", parse_mode=ParseMode.MARKDOWN)
    try:
        results = yt_service.search(text)
        if not results:
            await status_msg.edit_text("❌ No results found. Try a different keyword.")
            return
            
        vid = results[0]['videoId']
        title = results[0]['title']
        artist = results[0]['artists'][0]['name'] if results[0].get('artists') else "Unknown"
        
        await dispatch_to_huey(update, context, vid, title, artist, status_msg)
        
    except Exception as e:
        logger.error(f"Text Search Fallback Error: {e}")
        await status_msg.edit_text("❌ An error occurred during the search.")

# ==========================================
# 🎵 OTHER HANDLERS
# ==========================================

async def list_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    internal_uid = get_user_id(user.id)
    current_token = get_user_current_session(user.id)
    
    sessions = get_active_sessions(internal_uid)
    session_list = [dict(s) for s in sessions]
    
    if current_token:
        is_owned = any(s['token'] == current_token for s in session_list)
        if not is_owned:
            guest = get_session_info(current_token)
            if guest:
                fake = dict(guest)
                fake['is_guest_entry'] = True
                session_list.insert(0, fake)

    if not session_list:
        base_url = Config.BASE_URL if Config.BASE_URL else "the website"
        await update.message.reply_text(
            f"❌ *No active devices found.*\n\nOpen [Fanus Web Player]({base_url}) on your TV/PC and scan the QR code to connect.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        return

    await update.message.reply_text("📱 *Your Connected Devices:*", parse_mode=ParseMode.MARKDOWN)
    for sess in session_list:
        token = sess['token']
        d_name = sess['device_name'] or token[:4]
        is_cur = (token == current_token)
        label = f"👤 {d_name} (Guest Mode)" if sess.get('is_guest_entry') else f"📺 {d_name}"
        if is_cur: label = f"🟢 {d_name} (Currently Active)"
        await update.message.reply_text(label, reply_markup=get_smart_buttons(token, is_cur))

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    
    if data == "help_connect":
        base_url = Config.BASE_URL if Config.BASE_URL else "the Web Player"
        await context.bot.send_message(
            user.id, 
            f"💡 *How to Connect:*\nOpen [{base_url}]({base_url}) on your screen and scan the QR code displayed there.", 
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        return
    elif data == "help_upload":
        await context.bot.send_message(user.id, "🎙 *Upload Music:*\nSimply forward any MP3 file from other chats to this bot, or upload a file directly.")
        return

    if data.startswith("select_"):
        target = data.split("_")[1]
        update_user_session(user.id, target)
        sess = get_session_info(target)
        d_name = sess['device_name'] or target[:4]
        await query.edit_message_reply_markup(reply_markup=get_smart_buttons(target, True))
        await context.bot.send_message(user.id, f"✅ Active Device switched to: *{d_name}*", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("manage_"):
        token = data.split("_")[1]
        current_token = get_user_current_session(user.id)
        is_cur = (token == current_token)
        await query.edit_message_reply_markup(reply_markup=get_smart_buttons(token, is_cur))

    elif data.startswith("rename_"):
        token = data.split("_")[1]
        sess = get_session_info(token)
        if sess['admin_id'] != get_user_id(user.id):
            await context.bot.send_message(user.id, "⛔️ Access Denied. You are not the administrator of this device.")
            return
        context.user_data['renaming_token'] = token
        await context.bot.send_message(user.id, f"✍️ Enter a new name for `{sess['device_name']}`:", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply(selective=True))

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message.audio: return
    audio = update.message.audio
    meta = {
        'file_unique_id': audio.file_unique_id, 'file_id': audio.file_id,
        'title': audio.title or "Unknown Track", 'performer': audio.performer or "Unknown Artist",
        'duration': audio.duration, 'file_size': audio.file_size,
        'thumb_id': audio.thumbnail.file_id if audio.thumbnail else None,
        'youtube_id': None
    }
    await process_track_and_queue(update, context, meta)

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
            content = InputTextMessageContent(f"/dl {vid} | {song.get('title')} :: {song.get('artists', [{}])[0].get('name')}")
            articles.append(InlineQueryResultArticle(
                id=str(uuid.uuid4()), title=f"{prefix}{song.get('title')}",
                description=f"{song.get('artists', [{}])[0].get('name')}",
                thumbnail_url=song.get('thumbnails', [{}])[-1].get('url'),
                input_message_content=content
            ))
        await context.bot.answer_inline_query(update.inline_query.id, articles, cache_time=0)
    except Exception as e:
        logger.error(f"Inline Search Error: {e}")

async def youtube_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    try:
        parts = msg.replace('/dl ', '').split('|')
        vid = parts[0].strip()
        meta_part = parts[1].strip() if len(parts) > 1 else "Unknown :: Unknown"
        
        if '::' in meta_part: title, artist = meta_part.split('::')
        else: title, artist = meta_part, "Unknown"

        title, artist = title.strip(), artist.strip()
        status_msg = await update.message.reply_text(f"⏳ Processing track...")
        await dispatch_to_huey(update, context, vid, title, artist, status_msg)
    except Exception as e:
        await update.message.reply_text("❌ Error processing your request.")

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass