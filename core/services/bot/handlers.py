# core/services/bot/handlers.py

import asyncio
import uuid
import re
import logging
from telegram import Update, ForceReply, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction

from core.config import Config
from core.services.youtube import YouTubeService
from core.services.spotify_official import spotify_keyless 
from .database import (
    bot_db_exec, get_user_id, update_user_session, get_session_info,
    get_user_current_session, set_device_name, get_active_sessions,
    get_track_by_youtube_id, get_user_role
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
# 🚀 CORE COMMANDS (V4 Live Hubs & Deep Links)
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main Entry Point, Optimized for Zero-Latency"""
    user = update.effective_user
    args = context.args
    if not user: return

    # تجمیع عملیات دیتابیس اولیه برای جلوگیری از فریز شدن لوپ (I/O Optimization)
    def init_db_ops():
        bot_db_exec("INSERT OR IGNORE INTO users (telegram_id, first_name, username) VALUES (?, ?, ?)", 
                   (user.id, user.first_name, user.username))
        token = get_user_current_session(user.id)
        session = get_session_info(token) if token else None
        internal_uid = get_user_id(user.id)
        return token, session, internal_uid

    current_token, session, internal_uid = await asyncio.to_thread(init_db_ops)

    # ---------------------------------------------------------
    # Scenario 1: Connect via QR Code (Hub Connection)
    # ---------------------------------------------------------
    if args and args[0].startswith('session_'):
        token = args[0].split('_')[1]
        
        await asyncio.to_thread(update_user_session, user.id, token)
        is_new_admin = await activate_session_and_notify(token, user.id, user.first_name, context)
        
        if is_new_admin is None:
            await update.message.reply_text("❌ Invalid or Expired Hub Link.")
            return

        session = await asyncio.to_thread(get_session_info, token)
        d_name = session['device_name'] or f"Hub-{token[:4]}"

        base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
        live_url = f"{base_url}/live/{token}"
        remote_url = f"{base_url}/remote/{token}"

        buttons = [
            [InlineKeyboardButton("▶️ Open Web Player", url=live_url), InlineKeyboardButton("🎛 Remote Control", url=remote_url)],
            [InlineKeyboardButton("✏️ Rename Hub", callback_data=f"rename_{token}")],
            [InlineKeyboardButton("🔍 Search & Play Music", switch_inline_query_current_chat="")]
        ]

        hub_role = "👑 *Admin*" if is_new_admin else "👤 *Connected*"
        await update.message.reply_text(
            f"🎉 *Hub Connected Successfully!*\n\n"
            f"📡 Hub Name: *{d_name}*\n"
            f"⚡ Access Level: {hub_role}\n\n"
            f"💡 *How to Use:*\n"
            f"1️⃣ Send any Spotify playlist or YouTube link here—it will download and play live on your Hub.\n"
            f"2️⃣ Tap *Remote Control* below to adjust volume, pause/play, or view the playlist.\n"
            f"3️⃣ Tap *Queue* in the menu below to view upcoming tracks.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN
        )

        # Persistent main menu keyboard
        await update.message.reply_text(
            "👇 Hub controls are pinned below for instant access:",
            reply_markup=get_main_menu_keyboard()
        )

    # ---------------------------------------------------------
    # Scenario 2: Admin User Inspection (Deep Link)
    # ---------------------------------------------------------
    elif args and args[0].startswith('view_'):
        if user.id != Config.ADMIN_TELEGRAM_ID:
             logger.warning(f"⚠️ Unauthorized access attempt by {user.id} to view user logs.")
             await update.message.reply_text("⛔️ Access Denied. Master Admin ID mismatch.")
             return

        target_telegram_id = args[0].replace('view_', '')
        
        try:
            await context.bot.send_contact(
                chat_id=user.id,
                phone_number="+00000000000",
                first_name="Intelligence Report",
                last_name=f"[ID: {target_telegram_id}]",
                vcard=f"BEGIN:VCARD\nVERSION:3.0\nN:;{target_telegram_id};;;\nFN:User {target_telegram_id}\nTEL;TYPE=cell:+00000000000\nEND:VCARD"
            )
            
            await update.message.reply_text(
                f"🔍 *Lyraz Intelligence Panel*\n\n"
                f"👤 Target ID: `{target_telegram_id}`\n\n"
                f"👉 If the contact card above doesn't open the profile, try this strict link: [View Profile](tg://user?id={target_telegram_id})",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to extract user via bot: {e}")
            await update.message.reply_text(f"❌ Error generating Intelligence Report: {e}")

    # ---------------------------------------------------------
    # Scenario 3: Normal Start (Welcome Message)
    # ---------------------------------------------------------
    else:
        welcome_msg = (
            f"👋 *Welcome to Lyraz V4, {user.first_name}!*\n"
            "Your centralized Live Audio infrastructure.\n\n"
            "🎼 *What can I do?*\n"
            "📥 *Download:* Paste a Spotify/YouTube link to archive tracks.\n"
            "🔍 *Search:* Instantly find any song from the global database.\n"
            "📡 *Live Sync:* Play music synchronously across multiple screens.\n\n"
        )
        
        is_admin = False
        if current_token and session:
            d_name = session['device_name'] or "Unknown Hub"
            is_admin = (session['admin_id'] == internal_uid)
            
            role_text = "(Admin)" if is_admin else "(Guest)"
            welcome_msg += f"🟢 *Status:* Currently connected to *{d_name}* {role_text}.\n\n👇 *Get started:* Use the menu below or send a music link."
        else:
            base_url = Config.BASE_URL if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "the website"
            welcome_msg += f"👇 *Get started:* Open [Lyraz Web Player]({base_url}) on a screen and scan the QR code to create your first Live Hub."

        await update.message.reply_text(
            welcome_msg, 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=get_main_menu_keyboard(),
            disable_web_page_preview=True
        )
        
        await update.message.reply_text(
            "⚡️ *Quick Actions:*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_onboarding_keyboard(current_token, is_admin=is_admin)
        )

# ==========================================
# 📡 LINK PARSERS & DISPATCHERS
# ==========================================

async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    match = re.search(r'(?:v=|/)([0-9A-Za-z_-]{11}).*', url)
    if not match:
        await update.message.reply_text("❌ Invalid YouTube link format.")
        return
        
    vid = match.group(1)
    status_msg = await update.message.reply_text("⏳ Processing YouTube track...")
    
    try:
        # دریافت مستقیم اطلاعات ترک به جای سرچ اشتباه روی هش ویدیو
        info = await asyncio.to_thread(yt_service.get_video_info, vid)
        title = info.get('title', 'YouTube Track')
        artist = info.get('artist', 'Unknown Artist')
    except:
        title, artist = "YouTube Track", "Unknown Artist"

    await dispatch_to_huey(update, context, vid, title, artist, status_msg)


async def handle_spotify_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    status_msg = await update.message.reply_text("🔎 Analyzing Spotify link...")
    
    # پردازش شبکه اسپاتیفای در پس‌زمینه
    sp_data = await asyncio.to_thread(spotify_keyless.parse_link, url)
    
    if sp_data.get('status') == 'error':
        await status_msg.edit_text(f"❌ {sp_data.get('message')}")
        return

    # --- Case 1: Single Track ---
    if sp_data['type'] == 'track':
        title = sp_data['title']
        artist = sp_data['artist']
        cover_url = sp_data.get('cover')
        duration = sp_data.get('duration')

        await status_msg.edit_text(f"🔎 Matching *{title}* on global database...", parse_mode=ParseMode.MARKDOWN)
        results = await asyncio.to_thread(yt_service.search, sp_data['search_query'])
        if not results:
            await status_msg.edit_text("❌ Could not find a match for this specific track.")
            return

        vid = results[0]['videoId']
        await dispatch_to_huey(update, context, vid, title, artist, status_msg, cover_url=cover_url, duration=duration)

    # --- Case 2: Playlist or Album (V4.5 Batch Process) ---
    elif sp_data['type'] in ['playlist', 'album']:
        tracks = sp_data['tracks']
        playlist_name = sp_data.get('name', 'Spotify Collection')
        cover_url = sp_data.get('cover')
        
        await status_msg.edit_text(
            f"📥 Found *{len(tracks)}* tracks in *{playlist_name}*.\nInitializing download engine...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        from core.tasks import download_playlist_batch
        
        def fetch_meta_sync():
            return get_user_current_session(update.effective_user.id), get_user_role(update.effective_user.id)
            
        current_token, role = await asyncio.to_thread(fetch_meta_sync)
        target_quality = '320' if role in ['admin', 'pro'] else Config.AUDIO_QUALITY
        
        download_playlist_batch(
            tracks=tracks,
            playlist_name=playlist_name,
            cover_url=cover_url,
            user_id=update.effective_user.id,
            user_first_name=update.effective_user.first_name,
            session_token=current_token,
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
            quality=target_quality
        )


async def dispatch_to_huey(update: Update, context: ContextTypes.DEFAULT_TYPE, vid, title, artist, status_msg, cover_url=None, duration=None):
    from core.tasks import download_and_process_track
    user = update.effective_user
    
    def fetch_dispatch_meta():
        c_token = get_user_current_session(user.id)
        c_track = get_track_by_youtube_id(vid)
        u_role = get_user_role(user.id)
        return c_token, c_track, u_role

    current_token, cached, role = await asyncio.to_thread(fetch_dispatch_meta)
    
    # 1. Check Cache Hit
    if cached:
        try: await status_msg.delete()
        except: pass
        await ensure_track_and_process(update, context, video_id=vid, title=title, artist=artist)
        return

    # 2. RBAC Quality Check
    download_quality = '320' if role in ['admin', 'pro'] else Config.AUDIO_QUALITY

    await status_msg.edit_text(f"⏳ *{title}* added to the queue...", parse_mode=ParseMode.MARKDOWN)
    
    download_and_process_track(
        video_id=vid, title=title, artist=artist, 
        user_id=user.id, user_first_name=user.first_name, 
        session_token=current_token, chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        quality=download_quality,
        cover_url=cover_url,
        duration=duration
    )

# ==========================================
# 💬 TEXT & NAVIGATION HANDLER
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message or not update.message.text: 
        return
    text = update.message.text.strip()
    
    if text in ["📺 My Devices", "📱 My Devices", "📺 My Hubs", "📺 Devices"]: 
        await list_devices(update, context)
        return
        
    if text in ["📖 Setup Guide", "❓ Help", "📖 Guide"]: 
        guide_text = (
            "🚀 *Lyraz Hubs Quick Guide:*\n\n"
            "1️⃣ *Connect Hub:* Open the Web Player on your screen/TV and scan the QR code with your phone.\n"
            "2️⃣ *Play Music:* Paste any Spotify playlist or YouTube link here—it will download and play live on your Hub.\n"
            "3️⃣ *Remote Control:* Tap 'Remote Control' in the menu to manage volume, seeking, and playback.\n"
            "4️⃣ *Track Queue:* Tap 'Queue' anytime to view upcoming tracks in your active session."
        )
        await update.message.reply_text(guide_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard())
        return

    if text in ["🔍 Search Music", "🔍 Search"]:
        bot_username = Config.BOT_USERNAME
        await update.message.reply_text(
            f"🔎 *How to Search:*\n"
            f"Simply type `@{bot_username} [song name/artist]` right here in the chat, or tap the button below!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Open Search Panel", switch_inline_query_current_chat="")]
            ])
        )
        return
        
    if text in ["📥 Download Link", "📥 Download"]:
        await update.message.reply_text("🔗 Send me any valid *Spotify* (track/playlist) or *YouTube* link to start playback.", parse_mode=ParseMode.MARKDOWN)
        return

    if text in ["🎛 Remote Control", "🎛 Remote"]:
        token = await asyncio.to_thread(get_user_current_session, user.id)
        if not token:
            await update.message.reply_text("❌ You are not connected to any Hub yet. Scan the QR code on your Web Player to get started.", reply_markup=get_main_menu_keyboard())
            return
        base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
        remote_url = f"{base_url}/remote/{token}"
        await update.message.reply_text(
            "🎛 *Hub Mobile Remote Control*\n\n"
            "Tap below to manage playback, volume, and the playlist queue from your phone:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Open Remote Control", url=remote_url)]])
        )
        return

    if text in ["📋 Queue", "📋 Playlist"]:
        token = await asyncio.to_thread(get_user_current_session, user.id)
        if not token:
            await update.message.reply_text("❌ You are not connected to any Hub yet.", reply_markup=get_main_menu_keyboard())
            return
        def get_queue_items():
            import sqlite3
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.row_factory = sqlite3.Row
                return conn.execute("""
                    SELECT t.title, t.performer, pi.is_played 
                    FROM playlist_items pi
                    JOIN tracks t ON pi.track_id = t.id
                    WHERE pi.session_token = ?
                    ORDER BY pi.id ASC
                """, (token,)).fetchall()
        items = await asyncio.to_thread(get_queue_items)
        if not items:
            await update.message.reply_text("📭 The queue for this Hub is currently empty. Send a song or playlist link to start playing!", reply_markup=get_main_menu_keyboard())
            return
        queue_text = "📋 *Current Hub Queue:*\n\n"
        for i, item in enumerate(items[:20], 1):
            status = "▶️ Playing" if not item['is_played'] and i == 1 else ("✅ Played" if item['is_played'] else "⏳ Queued")
            queue_text += f"{i}. *{item['title']}* - _{item['performer']}_\n   └ {status}\n"
        if len(items) > 20:
            queue_text += f"\n_... and {len(items)-20} more tracks in queue_"
        await update.message.reply_text(queue_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu_keyboard())
        return

    # --- Renaming Flow (Multi-layered: context.user_data + Reply-To-Message) ---
    is_reply_to_naming = False
    if update.message.reply_to_message:
        orig_text = update.message.reply_to_message.text or ""
        if any(k in orig_text.lower() for k in ["enter a name", "enter a new name", "rename", "hub activated"]):
            is_reply_to_naming = True

    if 'renaming_token' in context.user_data or is_reply_to_naming:
        token = context.user_data.get('renaming_token') or (get_user_current_session(user.id) if user else None)
        if token:
            await asyncio.to_thread(set_device_name, token, text)
            if 'renaming_token' in context.user_data:
                del context.user_data['renaming_token']
            
            base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "http://localhost:5000"
            live_url = f"{base_url}/live/{token}"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Open Web Player", url=live_url)]])
            await update.message.reply_text(
                f"✅ Hub successfully renamed to: *{text}*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            return

    # --- Smart Link Detection ---
    if re.match(r'(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/.+', text):
        await handle_youtube_link(update, context, text)
        return
        
    if re.match(r'(https?://)?(open\.spotify\.com)/.+', text):
        await handle_spotify_link(update, context, text)
        return

    # --- Interactive Search Results (NO Blind Auto-Downloading!) ---
    status_msg = await update.message.reply_text(f"🔎 Searching for *{text}*...", parse_mode=ParseMode.MARKDOWN)
    try:
        results = await asyncio.to_thread(yt_service.search, text)
        if not results:
            await status_msg.edit_text("❌ No matching songs found. Try a different keyword.")
            return

        buttons = []
        for i, song in enumerate(results[:4], 1):
            vid = song.get('videoId')
            s_title = song.get('title', 'Unknown Track')[:30]
            raw_artist = song.get('artists', [{'name': 'Unknown'}])[0]['name'] if song.get('artists') else "Unknown"
            s_artist = re.sub(r'\s*-\s*Topic$', '', raw_artist, flags=re.IGNORECASE).strip() or "Unknown"
            s_artist = s_artist[:20]

            cached = get_track_by_youtube_id(vid)
            prefix = "⚡ " if cached else "📥 "
            btn_text = f"{prefix}{i}. {s_title} — {s_artist}"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=f"dl_{vid}")])

        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_search")])

        await status_msg.edit_text(
            f"🎶 *Search Results for:* _{text}_\n"
            f"Select a track below to play on your Hub:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Text Search Error: {e}")
        await status_msg.edit_text("❌ An error occurred during search.")

# ==========================================
# 🎵 OTHER HANDLERS
# ==========================================

async def list_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    def fetch_devices_data():
        internal_uid = get_user_id(user.id)
        current_token = get_user_current_session(user.id)
        sessions = get_active_sessions(internal_uid)
        session_list = [dict(s) for s in sessions]
        
        if current_token:
            is_owned = any(s['token'] == current_token for s in session_list)
            if not is_owned:
                guest_session = get_session_info(current_token)
                if guest_session:
                    fake_session = dict(guest_session)
                    fake_session['is_guest_entry'] = True
                    session_list.insert(0, fake_session)
        return current_token, session_list

    current_token, session_list = await asyncio.to_thread(fetch_devices_data)

    if not session_list:
        base_url = Config.BASE_URL if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "the website"
        await update.message.reply_text(
            f"❌ *No connected Hubs found.*\n\nOpen [Lyraz Web Player]({base_url}) on your TV/PC and scan the QR code to create one.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        return

    await update.message.reply_text("📡 *Your Live Hubs:*\n_Select a Hub to make it active, or share its Live Player link._", parse_mode=ParseMode.MARKDOWN)
    for sess in session_list:
        token = sess['token']
        d_name = sess['device_name'] or f"Hub-{token[:4]}"
        is_cur = (token == current_token)
        
        is_guest = sess.get('is_guest_entry', False)
        is_admin = not is_guest
        
        label = f"👤 {d_name} (Guest Mode)" if is_guest else f"📡 {d_name}"
        if is_cur: label = f"🟢 {d_name} (Active Hub)"
        
        await update.message.reply_text(label, reply_markup=get_smart_buttons(token, is_cur, is_admin=is_admin))

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    
    def process_callback_db(target_token, is_select=False):
        if is_select:
            update_user_session(user.id, target_token)
            
        c_token = get_user_current_session(user.id)
        sess = get_session_info(target_token)
        internal_uid = get_user_id(user.id)
        is_admin = sess['admin_id'] == internal_uid if sess else False
        d_name = sess['device_name'] or f"Hub-{target_token[:4]}" if sess else "Unknown"
        return c_token, is_admin, d_name

    if data.startswith("select_"):
        target_token = data.split("_")[1]
        _, is_admin, d_name = await asyncio.to_thread(process_callback_db, target_token, True)
        
        await query.edit_message_reply_markup(reply_markup=get_smart_buttons(target_token, True, is_admin=is_admin))
        await context.bot.send_message(user.id, f"✅ Active Hub switched to: *{d_name}*", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("manage_"):
        token = data.split("_")[1]
        current_token, is_admin, _ = await asyncio.to_thread(process_callback_db, token, False)
        is_cur = (token == current_token)
        
        await query.edit_message_reply_markup(reply_markup=get_smart_buttons(token, is_cur, is_admin=is_admin))

    elif data.startswith("rename_"):
        token = data.split("_")[1]
        
        def check_admin():
            sess = get_session_info(token)
            return sess, sess['admin_id'] == get_user_id(user.id) if sess else False
            
        sess, is_admin = await asyncio.to_thread(check_admin)
        
        if not is_admin:
            await context.bot.send_message(user.id, "⛔️ Access Denied. You are not the administrator of this Hub.")
            return
            
        context.user_data['renaming_token'] = token
        await context.bot.send_message(
            user.id, 
            f"✍️ Enter a new name for `{sess['device_name'] or 'Hub'}`:", 
            parse_mode=ParseMode.MARKDOWN, 
            reply_markup=ForceReply(selective=True)
        )

    elif data.startswith("dl_"):
        vid = data.split("_")[1]
        try:
            await query.edit_message_text("⏳ Processing selected track...", parse_mode=ParseMode.MARKDOWN)
            info = await asyncio.to_thread(yt_service.get_video_info, vid)
            if info:
                await dispatch_to_huey(update, context, vid, info['title'], info['artist'], query.message)
            else:
                await query.edit_message_text("❌ Failed to fetch track information.")
        except Exception as e:
            logger.error(f"Callback dl_ error: {e}")
            await query.edit_message_text("❌ An error occurred while queuing the track.")

    elif data == "cancel_search":
        try: await query.message.delete()
        except Exception: pass

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
    
    # اجرای کامل سرچ و ساخت نتیجه در پس‌زمینه (Zero-Lag Inline)
    def run_inline_search():
        results = yt_service.search(query)
        articles = []
        for song in results:
            vid = song.get('videoId')
            cached = get_track_by_youtube_id(vid)
            prefix = "✅ " if cached else ""
            raw_art = song.get('artists', [{}])[0].get('name', 'Unknown')
            clean_art = re.sub(r'\s*-\s*Topic$', '', raw_art, flags=re.IGNORECASE).strip() or "Unknown"
            content = InputTextMessageContent(f"/dl {vid} | {song.get('title')} :: {clean_art}")
            articles.append(InlineQueryResultArticle(
                id=str(uuid.uuid4()), title=f"{prefix}{song.get('title')}",
                description=f"{clean_art}",
                thumbnail_url=song.get('thumbnails', [{}])[-1].get('url'),
                input_message_content=content
            ))
        return articles

    try:
        articles = await asyncio.to_thread(run_inline_search)
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

async def sync_vault_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to manually trigger vault recovery"""
    user = update.effective_user
    if user and user.id != Config.ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔️ Access restricted to administrator.")
        return

    msg = await update.message.reply_text("🔄 Synchronizing database with Telegram Cloud Vault...")
    from core.tasks import sync_vault_from_channel
    count = await sync_vault_from_channel(context.bot)
    await msg.edit_text(
        f"✅ *Vault Synchronization Complete!*\n\n"
        f"📦 Total verified tracks indexed: *{count}*",
        parse_mode=ParseMode.MARKDOWN
    )