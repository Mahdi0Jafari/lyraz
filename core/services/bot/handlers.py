# core/services/bot/handlers.py

import asyncio
import uuid
import re
import html
import urllib.parse
import logging
from telegram import Update, ForceReply, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction

from core.config import Config
from core.services.youtube import YouTubeService
from core.services.spotify_official import spotify_keyless 
from core.services.soundcloud import soundcloud_service 
from .database import (
    bot_db_exec, get_user_id, update_user_session, get_session_info,
    get_user_current_session, get_user_session_token, get_db_connection,
    set_device_name, get_active_sessions, get_track_by_youtube_id,
    get_user_role, check_user_quota_status, register_referral, get_user_referral_stats
)
from .keyboards import (
    get_main_menu_keyboard, get_smart_buttons, get_onboarding_keyboard,
    get_queue_keyboard, build_search_keyboard
)
from .logic import (
    process_track_and_queue, 
    ensure_track_and_process, 
    activate_session_and_notify,
    notify_web_container
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

        remote_btn = InlineKeyboardButton("🎛 Remote Control", web_app=WebAppInfo(url=remote_url)) if remote_url.startswith('https') else InlineKeyboardButton("🎛 Remote Control", url=remote_url)
        buttons = [
            [InlineKeyboardButton("▶️ Open Web Player", url=live_url), remote_btn],
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
    # Scenario 3: Referral Deep Link
    # ---------------------------------------------------------
    elif args and args[0].startswith('ref_'):
        try:
            referrer_tg_id = int(args[0].replace('ref_', ''))
            success, total_refs, became_pro, referrer_name = await asyncio.to_thread(
                register_referral, referrer_tg_id, user.id
            )
            if success:
                try:
                    ref_reward_msg = (
                        f"🎉 *New Friend Joined Lyraz!*\n\n"
                        f"Your friend *{user.first_name}* just joined using your personal invite link!\n\n"
                        f"🎁 *Reward:* +25 Extra Daily Downloads added!\n"
                        f"📊 Total Friends Invited: *{total_refs} / 3*"
                    )
                    if became_pro:
                        ref_reward_msg += "\n\n👑 *PRO STATUS UNLOCKED!*\nYou have invited 3 friends! You now have permanent *Unlimited Downloads* at 320kbps!"
                        
                    await context.bot.send_message(chat_id=referrer_tg_id, text=ref_reward_msg, parse_mode=ParseMode.MARKDOWN)
                except Exception as e:
                    logger.warning(f"Could not notify referrer {referrer_tg_id}: {e}")

                inviter_display = referrer_name or "a friend"
                welcome_msg = (
                    f"👋 *Welcome to Lyraz, {user.first_name}!*\n"
                    f"🎉 You were invited by *{inviter_display}*!\n\n"
                    f"🎁 *Welcome Bonus:* +5 Extra Daily Downloads unlocked!\n\n"
                    "🎼 *What can I do?*\n"
                    "📥 *Download:* Paste a Spotify/YouTube link to get tracks at 320kbps.\n"
                    "🔍 *Search:* Instantly find any song from our catalog.\n"
                    "📡 *Live Sync:* Stream music live on multiple screens with our Web Player.\n\n"
                    "👇 *Choose an option below to get started:*"
                )
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
                return
        except Exception as e:
            logger.error(f"Referral processing error: {e}")

    # ---------------------------------------------------------
    # Scenario 4: Normal Start (Welcome Message)
    # ---------------------------------------------------------
    else:
        is_admin = False
        if current_token and session:
            d_name = session['device_name'] or "Hub"
            is_admin = (session['admin_id'] == internal_uid)
            role_text = "Admin" if is_admin else "Guest"
            welcome_msg = (
                f"🟢 <b>Connected to Hub: {html.escape(d_name)}</b> ({role_text})\n"
                f"──────────────────────\n"
                f"📡 <i>Your phone is actively paired with this Hub.</i>\n\n"
                f"👇 <i>Paste any YouTube/Spotify link, or choose an action below:</i>"
            )
        else:
            base_url = Config.BASE_URL if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "https://lyraz.ir"
            welcome_msg = (
                f"👋 <b>Welcome to Lyraz Hubs, {html.escape(user.first_name)}!</b>\n"
                f"──────────────────────\n"
                f"🎼 <i>Centralized Live Audio & Social Playback</i>\n\n"
                f"👇 <i>Open <a href='{base_url}'>Lyraz Web Player</a> on your TV/screen to connect, or choose an action:</i>"
            )

        await update.message.reply_text(
            welcome_msg, 
            parse_mode=ParseMode.HTML, 
            reply_markup=get_main_menu_keyboard(),
            disable_web_page_preview=True
        )
        await update.message.reply_text(
            "⚡️ <b>Quick Actions:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_onboarding_keyboard(current_token, is_admin=is_admin)
        )

# ==========================================
# 📡 LINK PARSERS & DISPATCHERS
# ==========================================

async def check_quota_and_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    بررسی سهمیه دانلود کاربر طبق استراتژی رشد لایراز (docs/growth_and_quota_strategy.md).
    اگر سهمیه تمام شده باشد و عضو کانال نباشد، پیام ترغیب به عضویت نمایش داده می‌شود.
    """
    user = update.effective_user
    if not user: return True

    allowed, current_count, max_quota, role = await asyncio.to_thread(check_user_quota_status, user.id)
    if allowed:
        return True

    # اگر سهمیه پایه تمام شده و کانال اجباری تعریف شده باشد
    if Config.MANDATORY_CHANNELS:
        is_member = True
        for ch in Config.MANDATORY_CHANNELS:
            try:
                member = await context.bot.get_chat_member(chat_id=ch, user_id=user.id)
                if member.status in ['left', 'kicked', 'restricted']:
                    is_member = False
                    break
            except Exception as e:
                logger.warning(f"Failed to check membership for {ch}: {e}")
                is_member = True
                break

        if is_member:
            return True

        # کاربر عضو نیست -> ارسال پیام ترغیب محترمانه
        channel_name = Config.MANDATORY_CHANNELS[0]
        channel_clean = channel_name.lstrip('@')
        channel_url = f"https://t.me/{channel_clean}"

        quota_msg = (
            f"🎧 *Daily High-Performance Quota Reached!* ({current_count}/{max_quota})\n\n"
            f"To keep our audio servers lightning-fast and ensure fair bandwidth, "
            f"free quotas refresh automatically every midnight.\n\n"
            f"⚡️ *Want to continue downloading right now?*\n"
            f"Join our official music channel to unlock extended access:"
        )

        buttons = [
            [InlineKeyboardButton("🦊 Join Lyraz Music", url=channel_url)],
            [InlineKeyboardButton("🔄 Verify Membership", callback_data="verify_channel_membership")]
        ]

        if update.message:
            await update.message.reply_text(quota_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
        return False

    return True

async def start_youtube_playlist_download(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_url_or_id: str, status_msg):
    """دانلود خودکار و دسته‌ای پلی‌لیست‌های یوتیوب با استفاده از ماژول چندرشته‌ای"""
    if not await check_quota_and_channel_membership(update, context):
        return

    pl_data = await asyncio.to_thread(yt_service.get_playlist_info, playlist_url_or_id, max_tracks=50)
    
    if pl_data.get('status') == 'error':
        await status_msg.edit_text(f"❌ {pl_data.get('message', 'Failed to fetch YouTube playlist.')}")
        return

    tracks = pl_data.get('tracks', [])
    playlist_name = pl_data.get('name', 'YouTube Playlist')
    cover_url = pl_data.get('cover')

    if not tracks:
        await status_msg.edit_text("❌ No playable tracks found in this YouTube playlist.")
        return

    user = update.effective_user
    chat = update.effective_chat

    await status_msg.edit_text(
        f"📥 Found *{len(tracks)}* tracks in *{playlist_name}*.\nInitializing download engine...",
        parse_mode=ParseMode.MARKDOWN
    )

    from core.tasks import download_playlist_batch
    
    def fetch_meta_sync():
        return get_user_current_session(user.id), get_user_role(user.id)
        
    current_token, role = await asyncio.to_thread(fetch_meta_sync)
    from core.services.bot.database import get_user_referral_stats
    ref_count, _, _ = await asyncio.to_thread(get_user_referral_stats, user.id)
    playlist_prio = 90 if (role == 'admin' or ref_count >= 3) else 45

    download_playlist_batch(
        tracks=tracks,
        playlist_name=playlist_name,
        cover_url=cover_url,
        user_id=user.id,
        user_first_name=user.first_name,
        session_token=current_token,
        chat_id=chat.id,
        message_id=status_msg.message_id,
        quality=Config.AUDIO_QUALITY,
        priority=playlist_prio
    )

async def handle_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    if not await check_quota_and_channel_membership(update, context):
        return

    # ۱. بررسی اینکه آیا لینک ارسالی یک پلی‌لیست یوتیوب است
    is_pure_playlist = ('/playlist' in url) or ('list=' in url and 'v=' not in url and 'youtu.be/' not in url)
    pl_match = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)

    if is_pure_playlist and pl_match:
        playlist_id = pl_match.group(1)
        status_msg = await update.message.reply_text("🔎 Analyzing YouTube Playlist...")
        await start_youtube_playlist_download(update, context, playlist_id, status_msg)
        return

    # ۲. استخراج شناسه ویدیو برای تک‌ترک
    match = re.search(r'(?:v=|/)([0-9A-Za-z_-]{11})', url)
    if not match:
        if pl_match:
            playlist_id = pl_match.group(1)
            status_msg = await update.message.reply_text("🔎 Analyzing YouTube Playlist...")
            await start_youtube_playlist_download(update, context, playlist_id, status_msg)
            return
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

    # ۳. اگر ویدیو عضوی از یک پلی‌لیست بود، دکمه دانلود اختیاری کل پلی‌لیست ارسال شود
    if pl_match:
        playlist_id = pl_match.group(1)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗂 Download Full Playlist", callback_data=f"dl_yt_pl:{playlist_id}")]
        ])
        await update.message.reply_text(
            f"💡 *This track is part of a playlist!*\nTap below to download all tracks from this playlist:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )


async def handle_spotify_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    if not await check_quota_and_channel_membership(update, context):
        return

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
        from telegram.constants import ChatAction
        try: await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        except Exception: pass
        vid = await asyncio.to_thread(yt_service.find_best_match, sp_data['search_query'], title, artist, duration)
        if not vid:
            await status_msg.edit_text("❌ Could not find an authentic match for this specific track.")
            return

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
        from core.services.bot.database import get_user_referral_stats
        ref_count, _, _ = await asyncio.to_thread(get_user_referral_stats, update.effective_user.id)
        playlist_prio = 90 if (role == 'admin' or ref_count >= 3) else 45
        
        download_playlist_batch(
            tracks=tracks,
            playlist_name=playlist_name,
            cover_url=cover_url,
            user_id=update.effective_user.id,
            user_first_name=update.effective_user.first_name,
            session_token=current_token,
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
            quality=Config.AUDIO_QUALITY,
            priority=playlist_prio
        )


async def handle_soundcloud_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """پردازش هوشمند و استخراج مستقیم آهنگ‌ها و مجموعه‌های ساندکلاد"""
    if not await check_quota_and_channel_membership(update, context):
        return

    status_msg = await update.message.reply_text("🔎 Analyzing SoundCloud link...")
    
    # واکشی متادیتا با سرویس ساندکلاد
    sc_data = await asyncio.to_thread(soundcloud_service.extract_info, url)
    
    if sc_data.get('status') == 'error':
        await status_msg.edit_text(f"❌ {sc_data.get('message', 'Failed to fetch SoundCloud link.')}")
        return

    # --- Case 1: Single Track ---
    if sc_data.get('type') == 'track':
        title = sc_data['title']
        artist = sc_data['artist']
        cover_url = sc_data.get('cover')
        duration = sc_data.get('duration')
        vid = sc_data['id']

        await status_msg.edit_text(f"📥 Found *{title}* on SoundCloud.\nAdding to queue...", parse_mode=ParseMode.MARKDOWN)
        await dispatch_to_huey(update, context, vid, title, artist, status_msg, cover_url=cover_url, duration=duration)

    # --- Case 2: Playlist / Set ---
    elif sc_data.get('type') == 'playlist':
        tracks = sc_data.get('tracks', [])
        set_name = sc_data.get('title', 'SoundCloud Collection')
        cover_url = sc_data.get('cover')

        if not tracks:
            await status_msg.edit_text("❌ No playable tracks found in this SoundCloud set.")
            return

        await status_msg.edit_text(
            f"📥 Found *{len(tracks)}* tracks in *{set_name}*.\nInitializing download engine...",
            parse_mode=ParseMode.MARKDOWN
        )

        from core.tasks import download_playlist_batch
        
        def fetch_meta_sync():
            return get_user_current_session(update.effective_user.id), get_user_role(update.effective_user.id)
            
        current_token, role = await asyncio.to_thread(fetch_meta_sync)
        from core.services.bot.database import get_user_referral_stats
        ref_count, _, _ = await asyncio.to_thread(get_user_referral_stats, update.effective_user.id)
        playlist_prio = 90 if (role == 'admin' or ref_count >= 3) else 45

        download_playlist_batch(
            tracks=tracks,
            playlist_name=set_name,
            cover_url=cover_url,
            user_id=update.effective_user.id,
            user_first_name=update.effective_user.first_name,
            session_token=current_token,
            chat_id=update.effective_chat.id,
            message_id=status_msg.message_id,
            quality=Config.AUDIO_QUALITY,
            priority=playlist_prio
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

    # 2. Dynamic Smart Priority System & Unified Quality
    # محاسبه اولویت صف بر اساس وفاداری، دعوت‌ها و نقش کاربر
    from core.services.bot.database import get_user_referral_stats
    ref_count, _, _ = await asyncio.to_thread(get_user_referral_stats, user.id)

    if role == 'admin':
        user_prio = 100
        prio_label = "👑 VIP Admin"
    elif role == 'pro' or ref_count >= 3:
        user_prio = 80
        prio_label = "⚡️ Fast-Track VIP"
    else:
        user_prio = 40
        prio_label = "Standard Queue"

    await status_msg.edit_text(f"⏳ *{title}* added to queue ({prio_label})...", parse_mode=ParseMode.MARKDOWN)
    
    download_and_process_track(
        video_id=vid, title=title, artist=artist, 
        user_id=user.id, user_first_name=user.first_name, 
        session_token=current_token, chat_id=update.effective_chat.id, message_id=status_msg.message_id,
        quality=Config.AUDIO_QUALITY,
        cover_url=cover_url,
        duration=duration,
        priority=user_prio
    )

async def show_referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    کارت معرفی و سیستم دعوت دوستان (Viral Referral Dashboard).
    نمایش لینک اختصاصی، آمار دعوت‌ها و دکمه اشتراک‌گذاری مستقیم در چت‌های تلگرام.
    """
    user = update.effective_user
    if not user: return
    
    count, quota, role = await asyncio.to_thread(get_user_referral_stats, user.id)
    bot_username = Config.BOT_USERNAME
    invite_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    share_text = f"🎵 Listen and download high-quality 320kbps music with full lyrics on Lyraz!\nJoin here: {invite_link}"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(invite_link)}&text={urllib.parse.quote(share_text)}"
    
    if role in ['admin', 'pro']:
        role_badge = "👑 *PRO (Unlimited Access)*"
    else:
        role_badge = f"⚡️ *{quota} Tracks / Day*"
        
    remaining = max(0, 3 - count)
    if remaining > 0 and role not in ['admin', 'pro']:
        target_info = f"Invite *{remaining} more friend{'s' if remaining > 1 else ''}* to unlock lifetime PRO status!"
    else:
        target_info = "🎉 You've unlocked permanent PRO status!"

    card_text = (
        "🎁 *Invite Friends & Get Unlimited Music!*\n\n"
        "Share your personal invite link with friends. For every friend who joins:\n"
        "• ⚡️ *+25 Extra Daily Downloads* permanently added to your quota\n"
        "• 👑 *Invite 3 Friends* to unlock lifetime *PRO Status* (Unlimited downloads at 320kbps!)\n\n"
        f"📊 *Your Referral Stats:*\n"
        f"• Friends Invited: *{count} / 3*\n"
        f"• Current Status: {role_badge}\n"
        f"💡 {target_info}\n\n"
        f"🔗 *Your Personal Invite Link:*\n"
        f"`{invite_link}`"
    )
    
    buttons = [
        [InlineKeyboardButton("🚀 Share with Friends", url=share_url)],
        [InlineKeyboardButton("🌐 Open Web Player", url=Config.BASE_URL or "https://lyraz.ir")]
    ]
    
    await update.message.reply_text(
        card_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=True
    )

async def render_queue_info(user_id, token):
    """Fetches queue tracks and builds bulletproof formatted HTML message and interactive inline controls."""
    def get_data():
        internal_uid = get_user_id(user_id)
        session = get_session_info(token)
        is_admin = (session['admin_id'] == internal_uid) if session else False
        d_name = (session['device_name'] or f"Hub-{token[:4]}") if session else f"Hub-{token[:4]}"
        
        conn = get_db_connection()
        if not conn:
            return [], is_admin, d_name
        try:
            rows = conn.execute("""
                SELECT pi.id as item_id, t.title, t.performer, pi.is_played 
                FROM playlist_items pi
                JOIN tracks t ON pi.track_id = t.id
                WHERE pi.session_token = ?
                ORDER BY pi.id ASC
            """, (token,)).fetchall()
            return [dict(r) for r in rows], is_admin, d_name
        except Exception as e:
            logger.error(f"Error fetching queue items from DB: {e}")
            return [], is_admin, d_name

    items, is_admin, d_name = await asyncio.to_thread(get_data)

    def safe_text(val, fallback="Unknown"):
        if not val:
            return fallback
        return html.escape(str(val))

    safe_hub = safe_text(d_name, f"Hub-{token[:4]}")
    active_items = [it for it in items if not it.get('is_played')]
    played_items = [it for it in items if it.get('is_played')]
    has_active = len(active_items) > 0

    if not items:
        text = (
            f"📋 <b>Live Queue: {safe_hub}</b>\n"
            f"──────────────────────\n"
            f"📭 <i>The queue is currently empty.</i>\n\n"
            f"💡 <i>Tip: Send any song name, Spotify, or YouTube link to start playing live!</i>"
        )
        markup = get_queue_keyboard(token, is_admin=is_admin, total_items=0, has_active=False)
        return text, markup

    text = f"📋 <b>Live Queue: {safe_hub}</b> ({len(items)} tracks)\n"
    text += "──────────────────────\n"

    if active_items:
        current = active_items[0]
        cur_title = safe_text(current.get('title'), 'Unknown Track')
        cur_artist = safe_text(current.get('performer'), 'Unknown Artist')
        text += f"▶️ <b>Now Playing:</b>\n   🎵 <b>{cur_title}</b> — <i>{cur_artist}</i>\n\n"
        
        upcoming = active_items[1:10]
        if upcoming:
            text += "⏳ <b>Up Next:</b>\n"
            for i, it in enumerate(upcoming, 1):
                t_title = safe_text(it.get('title'), 'Unknown Track')
                t_artist = safe_text(it.get('performer'), 'Unknown Artist')
                text += f"   {i}. <b>{t_title}</b> — <i>{t_artist}</i>\n"
            if len(active_items) > 10:
                text += f"\n<i>... and {len(active_items) - 10} more in queue</i>\n"
    else:
        text += "⏹ <b>Queue Playback Finished</b>\n"
        text += "<i>All songs in the queue have finished playing.</i>\n\n"

    if played_items:
        recent_played = played_items[-5:]
        text += "📜 <b>Recently Played:</b>\n"
        for i, it in enumerate(recent_played, 1):
            p_title = safe_text(it.get('title'), 'Unknown Track')
            p_artist = safe_text(it.get('performer'), 'Unknown Artist')
            text += f"   ✔️ <s>{p_title}</s> — <i>{p_artist}</i>\n"
        if len(played_items) > 5:
            text += f"   <i>(+ {len(played_items) - 5} earlier tracks)</i>\n"

    if not active_items:
        text += "\n💡 <i>Send any song or tap <b>Search Music</b> below to queue more!</i>"

    markup = get_queue_keyboard(token, is_admin=is_admin, total_items=len(items), has_active=has_active)
    return text, markup

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restores the persistent bottom reply keyboard immediately"""
    await update.message.reply_text(
        "👇 *Main Navigation Menu:*\nUse the buttons below to control your Hub, search, or download music:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard()
    )

async def handle_queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /queue command"""
    user = update.effective_user
    if not user: return
    token = await asyncio.to_thread(get_user_session_token, user.id)
    if not token:
        await update.message.reply_text(
            "❌ <b>You are not connected to any Hub yet.</b>\nOpen the Web Player on your screen and scan the QR code.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_keyboard()
        )
        return
    q_text, q_markup = await render_queue_info(user.id, token)
    try:
        await update.message.reply_text(q_text, parse_mode=ParseMode.HTML, reply_markup=q_markup)
    except Exception as e:
        logger.warning(f"Failed to send queue message with HTML: {e}")
        plain = re.sub(r'<[^>]+>', '', q_text)
        await update.message.reply_text(plain, reply_markup=q_markup)

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

    if text in ["🎁 Invite Friends", "🎁 Invite", "🎁 Referral"]:
        await show_referral_info(update, context)
        return

    if text in ["🎛 Remote Control", "🎛 Remote"]:
        token = await asyncio.to_thread(get_user_session_token, user.id)
        if not token:
            await update.message.reply_text("❌ You are not connected to any Hub yet. Scan the QR code on your Web Player to get started.", reply_markup=get_main_menu_keyboard())
            return
        base_url = Config.BASE_URL.rstrip('/') if hasattr(Config, 'BASE_URL') and Config.BASE_URL else "https://lyraz.ir"
        remote_url = f"{base_url}/remote/{token}"
        remote_btn = InlineKeyboardButton("📱 Open Remote Control", web_app=WebAppInfo(url=remote_url)) if remote_url.startswith('https') else InlineKeyboardButton("📱 Open Remote Control", url=remote_url)
        await update.message.reply_text(
            "🎛 *Hub Mobile Remote Control*\n\n"
            "Tap below to manage playback, volume, and the playlist queue from your phone:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[remote_btn]])
        )
        return

    if text in ["📋 Queue", "📋 Playlist", "/queue"] or text.lower().strip() == "queue":
        token = await asyncio.to_thread(get_user_session_token, user.id)
        if not token:
            await update.message.reply_text(
                "❌ <b>You are not connected to any Hub yet.</b>\nScan the QR code on your Web Player to get started.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu_keyboard()
            )
            return
        q_text, q_markup = await render_queue_info(user.id, token)
        try:
            await update.message.reply_text(q_text, parse_mode=ParseMode.HTML, reply_markup=q_markup)
        except Exception as e:
            logger.warning(f"Failed to send queue message with HTML: {e}")
            plain = re.sub(r'<[^>]+>', '', q_text)
            await update.message.reply_text(plain, reply_markup=q_markup)
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
            
            # 🔥 Fix: Always restore the persistent reply keyboard after ForceReply completion
            await update.message.reply_text(
                f"✅ Hub successfully renamed to: *{text}*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_main_menu_keyboard()
            )
            await update.message.reply_text(
                "⚡️ Hub links:",
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

    if re.match(r'(https?://)?((www\.|m\.|on\.)?soundcloud\.com)/.+', text, re.IGNORECASE):
        await handle_soundcloud_link(update, context, text)
        return

    # --- Interactive Search Results with Pagination (Reddit UX Best Practice) ---
    status_msg = await update.message.reply_text(f"🔎 Searching for *{text}*...", parse_mode=ParseMode.MARKDOWN)
    try:
        results = await asyncio.to_thread(yt_service.search, text)
        if not results:
            await status_msg.edit_text("❌ No matching songs found. Try a different keyword.")
            return

        # Store in user session cache for pagination
        context.user_data['search_cache'] = {
            'query': text,
            'results': results
        }

        msg_text, markup = build_search_keyboard(results, text, page=0, page_size=4)
        await status_msg.edit_text(
            msg_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=markup
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

    if data == "verify_channel_membership":
        is_member = True
        for ch in Config.MANDATORY_CHANNELS:
            try:
                member = await context.bot.get_chat_member(chat_id=ch, user_id=user.id)
                if member.status in ['left', 'kicked', 'restricted']:
                    is_member = False
                    break
            except Exception:
                is_member = True
                break

        if is_member:
            await query.answer("✅ Verified! Your quota has been extended.", show_alert=True)
            await query.edit_message_text(
                "🎉 *Membership Verified!*\n\nYour download access has been extended. Paste any Spotify or YouTube link to start!",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.answer("❌ You haven't joined yet! Please join the channel first.", show_alert=True)
        return

    elif data.startswith("select_"):
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

    elif data.startswith("dl_yt_pl:"):
        pl_id = data.split(":", 1)[1]
        try:
            await query.answer("📥 Initializing playlist download...", show_alert=False)
            status_msg = await query.message.reply_text("🔎 Fetching playlist tracks from YouTube...")
            await start_youtube_playlist_download(update, context, pl_id, status_msg)
        except Exception as e:
            logger.error(f"Error starting playlist callback download: {e}")
            await query.message.reply_text("❌ Failed to initiate playlist download.")

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

    elif data == "noop":
        await query.answer()

    elif data.startswith("sp_"):
        try:
            target_page = int(data.replace("sp_", ""))
            search_cache = context.user_data.get('search_cache')
            if not search_cache or not search_cache.get('results'):
                await query.answer("⚠️ Search expired. Please type a new search.", show_alert=True)
                return

            msg_text, markup = build_search_keyboard(
                search_cache['results'], 
                search_cache['query'], 
                page=target_page, 
                page_size=4
            )
            await query.edit_message_text(msg_text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
            await query.answer()
        except Exception as e:
            logger.error(f"Search pagination callback error: {e}")
            await query.answer()

    elif data.startswith("q_skip_"):
        target_token = data.replace("q_skip_", "")
        def do_skip_db():
            internal_uid = get_user_id(user.id)
            session = get_session_info(target_token)
            is_admin = (session['admin_id'] == internal_uid) if session else False
            if not is_admin:
                return False
            conn = get_db_connection()
            if not conn:
                return False
            try:
                with conn:
                    row = conn.execute("SELECT id FROM playlist_items WHERE session_token = ? AND is_played = 0 ORDER BY id ASC LIMIT 1", (target_token,)).fetchone()
                    if row:
                        conn.execute("UPDATE playlist_items SET is_played = 1 WHERE id = ?", (row[0],))
                return True
            except Exception as e:
                logger.error(f"Error skipping track in DB: {e}")
                return False

        allowed = await asyncio.to_thread(do_skip_db)
        if not allowed:
            await query.answer("⛔️ Only Hub Admin can skip tracks.", show_alert=True)
            return

        await notify_web_container({
            'type': 'command',
            'action': 'next',
            'session_token': target_token
        })
        await query.answer("⏭ Skipped to next track!")
        q_text, q_markup = await render_queue_info(user.id, target_token)
        try:
            await query.edit_message_text(q_text, parse_mode=ParseMode.HTML, reply_markup=q_markup)
        except Exception:
            plain = re.sub(r'<[^>]+>', '', q_text)
            try:
                await query.edit_message_text(plain, reply_markup=q_markup)
            except Exception: pass

    elif data.startswith("q_refresh_"):
        target_token = data.replace("q_refresh_", "")
        await query.answer("🔄 Queue refreshed!")
        q_text, q_markup = await render_queue_info(user.id, target_token)
        try:
            await query.edit_message_text(q_text, parse_mode=ParseMode.HTML, reply_markup=q_markup)
        except Exception:
            plain = re.sub(r'<[^>]+>', '', q_text)
            try:
                await query.edit_message_text(plain, reply_markup=q_markup)
            except Exception: pass

    elif data.startswith("q_clear_"):
        target_token = data.replace("q_clear_", "")
        def do_clear_db():
            internal_uid = get_user_id(user.id)
            session = get_session_info(target_token)
            is_admin = (session['admin_id'] == internal_uid) if session else False
            if not is_admin:
                return False
            conn = get_db_connection()
            if not conn:
                return False
            try:
                with conn:
                    conn.execute("DELETE FROM playlist_items WHERE session_token = ?", (target_token,))
                return True
            except Exception as e:
                logger.error(f"Error clearing queue in DB: {e}")
                return False

        allowed = await asyncio.to_thread(do_clear_db)
        if not allowed:
            await query.answer("⛔️ Only Hub Admin can clear the queue.", show_alert=True)
            return

        await notify_web_container({
            'type': 'command',
            'action': 'clear_queue',
            'session_token': target_token
        })
        await query.answer("🗑 Queue cleared!", show_alert=True)
        q_text, q_markup = await render_queue_info(user.id, target_token)
        try:
            await query.edit_message_text(q_text, parse_mode=ParseMode.HTML, reply_markup=q_markup)
        except Exception:
            plain = re.sub(r'<[^>]+>', '', q_text)
            try:
                await query.edit_message_text(plain, reply_markup=q_markup)
            except Exception: pass

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
    await process_track_and_queue(update, context, meta, is_upload=True)

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

async def vault_repair_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to check vault repair progress and trigger background healing"""
    user = update.effective_user
    if user and user.id != Config.ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔️ Access restricted to administrator.")
        return

    from scripts.vault_repair import get_status_summary, scan_mismatches

    args = context.args or []
    if args and args[0].lower() in ['start', 'run', 'scan']:
        await update.message.reply_text("🔍 Scanning database and queuing repair jobs...")
        new_count = await asyncio.to_thread(scan_mismatches)
        await update.message.reply_text(f"🚀 Started repair process! {new_count} total suspicious tracks indexed.\nUse /repair or /vault_status to monitor progress.")
        return

    s = await asyncio.to_thread(get_status_summary)
    if s['total'] == 0:
        await asyncio.to_thread(scan_mismatches)
        s = await asyncio.to_thread(get_status_summary)

    filled = max(0, min(10, int(s['percent'] / 10)))
    bar = "█" * filled + "░" * (10 - filled)

    msg = (
        f"🛠 *گزارش وضعیت بازسازی مخزن (Vault Repair)*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 *کل قطعات نیازمند اصلاح:* `{s['total']}`\n"
        f"✅ *اصلاح و تکمیل شده:* `{s['completed']}` ({s['percent']}%)\n"
        f"⏳ *در صف یا در حال پردازش:* `{s['in_progress']}`\n"
        f"❌ *ناموفق:* `{s['failed']}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *پیشرفت:* `[{bar}]` *{s['percent']}%*\n\n"
        f"💡 _این تسک‌ها با اولویت پایین (Priority 5) در پس‌زمینه اجرا می‌شوند و دانلودهای کاربران زنده همیشه اولویت بالاتر دارند._\n\n"
        f"دستورات:\n"
        f"• `/repair` یا `/vault_status` - مشاهده درصد پیشرفت زنده\n"
        f"• `/repair start` - اسکن مجدد و شارژ صف"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)