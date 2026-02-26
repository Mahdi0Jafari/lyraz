# core/services/bot/logic.py

import json
import logging
import sqlite3
import aiohttp
import asyncio
import time
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError

from core.config import Config
from core.services.youtube import YouTubeService
from .database import (
    bot_db_exec, get_user_id, get_user_current_session, 
    get_session_info, get_settings, get_channel_template,
    get_track_by_youtube_id
)

logger = logging.getLogger(__name__)

# YouTube service for internal logic fallback
yt_service = YouTubeService()

# ==========================================
# 🌉 CONTAINER BRIDGE (Async)
# ==========================================

async def notify_web_container(data_dict):
    """
    Sends data to the Web container to be broadcasted via SSE.
    Optimized: Now fully asynchronous using aiohttp to prevent event loop blocking.
    """
    try:
        url = "http://web:5000/internal/announce"
        sse_msg = f"data: {json.dumps(data_dict)}\n\n"
        
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json={'message': sse_msg}) as response:
                if response.status != 200:
                    logger.warning(f"Bridge returned status: {response.status}")
    except Exception as e:
        logger.error(f"Failed to bridge message to Web container: {e}")


# ==========================================
# 🔑 SESSION MANAGEMENT (Live Hubs)
# ==========================================

async def activate_session_and_notify(session_token, user_id, user_first_name, context):
    """
    1. Activates the Hub in the database (via thread).
    2. Notifies the web container to trigger Live Player UI asynchronously.
    """
    # 1. Database Operations in Thread
    def db_ops():
        internal_user_id = get_user_id(user_id)
        session = get_session_info(session_token)
        if not session:
            return None, False, None
            
        is_new_admin = False
        
        # Scenario 1: Hub has no admin yet -> Current user becomes admin
        if session['admin_id'] is None:
            bot_db_exec(
                "UPDATE sessions SET status='active', admin_id=?, last_active_at=CURRENT_TIMESTAMP WHERE token=?", 
                (internal_user_id, session_token)
            )
            is_new_admin = True
            
        # Scenario 2: Current user is already the admin -> Just activate & bump timestamp
        elif session['admin_id'] == internal_user_id:
            bot_db_exec(
                "UPDATE sessions SET status='active', last_active_at=CURRENT_TIMESTAMP WHERE token=?", 
                (session_token,)
            )
            
        return session, is_new_admin, internal_user_id

    session, is_new_admin, internal_user_id = await asyncio.to_thread(db_ops)

    if not session:
        return None
    
    # 2. Notify the Web Player Async
    announcement = {
        "type": "session_activated",
        "session_token": session_token,
        "admin": {
            "name": user_first_name,
            "id": user_id,
            "device_display_name": session['device_name'] or f"Hub-{session_token[:4]}"
        }
    }
    
    # Fire and forget
    asyncio.create_task(notify_web_container(announcement))
    
    return is_new_admin


# ==========================================
# 🎵 TRACK PROCESSING LOGIC
# ==========================================

async def ensure_track_and_process(update, context, video_id, title, artist):
    """
    Archive-First Strategy:
    1. Check if track exists in DB cache.
    2. If Yes -> Process immediately without downloading.
    3. If No -> Download, archive to Telegram, then process.
    """
    chat_id = update.effective_chat.id
    
    # 1. Cache Hit (DB call threaded)
    cached = await asyncio.to_thread(get_track_by_youtube_id, video_id)
    
    if cached:
        # ChatAction is light, but still an API call. We keep it for UX.
        # await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
        await update.message.reply_text(f"⚡️ *Loaded from Archive:* {title}", parse_mode=ParseMode.MARKDOWN)
        
        track_meta = {
            'file_unique_id': cached['file_unique_id'], 
            'file_id': cached['file_id'],
            'title': cached['title'], 
            'performer': cached['performer'],
            'duration': cached['duration'], 
            'file_size': cached['file_size'],
            'thumb_id': cached['thumb_id'], 
            'youtube_id': video_id,
            'bitrate': cached['bitrate']
        }
        await process_track_and_queue(update, context, track_meta)
        return

    # 2. Cache Miss (Fallback Download)
    status = await update.message.reply_text(f"📥 Downloading *{title}*...", parse_mode=ParseMode.MARKDOWN)
    # await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
    
    # Download is heavy I/O -> thread it!
    path = await asyncio.to_thread(yt_service.download, video_id)
    
    if path:
        try:
            # await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
            
            if Config.STORAGE_CHANNEL_ID:
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
                    'youtube_id': video_id,
                    'bitrate': Config.AUDIO_QUALITY 
                }
                
                try: await status.delete()
                except: pass
                
                await process_track_and_queue(update, context, track_meta)
            else:
                await status.edit_text("❌ Configuration Error: STORAGE_CHANNEL_ID is missing.")
                
        except TelegramError as e:
            logger.error(f"Telegram Upload Error: {e.message}")
            if "chat not found" in str(e).lower():
                 await status.edit_text(
                     f"❌ Error: Storage Channel not found.\nID Used: `{Config.STORAGE_CHANNEL_ID}`\n"
                     "Make sure the bot is an Admin and the ID starts with -100.", 
                     parse_mode=ParseMode.MARKDOWN
                 )
            else:
                 await status.edit_text(f"❌ Telegram API Error: {e.message}")

        except Exception as e:
            logger.error(f"General Upload Error: {e}")
            await status.edit_text("❌ Upload to cloud failed.")
        finally:
            await asyncio.to_thread(yt_service.cleanup, path)
    else:
        await status.edit_text("❌ Download from source failed.")


async def process_track_and_queue(update, context, track_meta):
    """
    Core Processing Hub:
    1. Register track in DB.
    2. Add to Hub queue & Broadcast Sync Signal.
    3. Send audio file back to the user.
    """
    user = update.effective_user
    
    # ==========================
    # Phase 1: Database Logic (Threaded)
    # ==========================
    def db_processing():
        internal_uid = get_user_id(user.id)
        
        # 1. Insert/Update Tracks Table
        try:
            sql = """
                INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, youtube_id, bitrate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(youtube_id) DO UPDATE SET file_id=excluded.file_id
            """ if track_meta.get('youtube_id') else """
                INSERT INTO tracks (file_unique_id, file_id, title, performer, duration, file_size, thumb_id, bitrate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                
            args.append(track_meta.get('bitrate', 192))
                
            bot_db_exec(sql, tuple(args))
        except Exception as e:
            logger.error(f"DB Insert Error: {e}")

        # 2. Retrieve Internal Track ID
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            key = 'youtube_id' if track_meta.get('youtube_id') else 'file_unique_id'
            val = track_meta.get('youtube_id') if track_meta.get('youtube_id') else track_meta['file_unique_id']
            res = conn.execute(f"SELECT id FROM tracks WHERE {key}=?", (val,)).fetchone()
            track_id = res[0] if res else None
            
        target_token = get_user_current_session(user.id)
        session = get_session_info(target_token) if target_token else None
        
        if track_id and session:
            hub_owner_id = session['admin_id'] if session['admin_id'] else internal_uid
            bot_db_exec("""
                INSERT INTO playlist_items (owner_id, track_id, added_by, session_token) 
                VALUES (?, ?, ?, ?)
            """, (hub_owner_id, track_id, internal_uid, target_token))
            
        return internal_uid, track_id, target_token, session

    # Execute DB phase in background thread
    internal_user_id, track_id, target_token, session = await asyncio.to_thread(db_processing)

    if not track_id:
        return

    # ==========================
    # Phase 2: User Messaging & Routing
    # ==========================
    if not target_token:
        # User not connected to Hub -> just return file
        if track_meta.get('youtube_id'):
            # await context.bot.send_chat_action(chat_id=user.id, action=ChatAction.UPLOAD_VOICE)
            try:
                caption = f"🎧 *{track_meta['title']}*\n👤 {track_meta['performer']}"
                if Config.BOT_USERNAME: caption += f"\n🆔 @{Config.BOT_USERNAME}"
                await context.bot.send_audio(
                    chat_id=user.id, audio=track_meta['file_id'],
                    title=track_meta['title'], performer=track_meta['performer'],
                    caption=caption, parse_mode=ParseMode.MARKDOWN
                )
            except Exception: pass
            
        await context.bot.send_message(user.id, "⚠️ Track saved. To play this synchronously, connect to a Live Hub first from the menu.")
        return

    if session:
        # Dispatch to Web Player via Bridge Async
        track_data = {
            'type': 'new_track',
            'title': track_meta['title'], 'performer': track_meta['performer'],
            'file_unique_id': track_meta['file_unique_id'], 'duration': track_meta['duration'],
            'added_by': user.first_name, 'session_token': target_token,
            'sync_timestamp': time.time()
        }
        asyncio.create_task(notify_web_container(track_data))
        
        # Deliver File with Hub Context
        d_name = session['device_name'] or f"Hub-{target_token[:4]}"
        caption = f"🎧 *{track_meta['title']}*\n👤 {track_meta['performer']}\n📡 Added to: *{d_name}*"
        
        base_url = Config.BASE_URL.rstrip('/') if Config.BASE_URL else "http://localhost:5000"
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("▶️ Open Player", url=f"{base_url}/live/{target_token}")
        ]])

        if track_meta.get('youtube_id'):
            # await context.bot.send_chat_action(chat_id=user.id, action=ChatAction.UPLOAD_VOICE)
            try:
                await context.bot.send_audio(
                    chat_id=user.id, audio=track_meta['file_id'],
                    title=track_meta['title'], performer=track_meta['performer'],
                    caption=caption, parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Failed to deliver audio: {e}")
        else:
            await context.bot.send_message(
                chat_id=user.id, text=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup
            )

        # Trigger broadcast async
        asyncio.create_task(handle_broadcast(context, user, track_meta['file_id'], track_meta, session))
    else:
        await context.bot.send_message(user.id, "⚠️ The selected Hub is currently offline or invalid.")


async def handle_broadcast(context, user, file_id, meta, session):
    """
    Broadcasts the processed track to attached public/private channels if configured.
    """
    # DB calls inside threaded function
    def fetch_bc_data():
        settings = get_settings()
        target_channel_id = session.get('linked_channel_id')
        channel_tmpl = None
        
        if target_channel_id:
            channel_tmpl = get_channel_template(target_channel_id)
        elif settings and settings['is_auto_broadcast_enabled'] and settings['auto_broadcast_channel_id']:
            target_channel_id = settings['auto_broadcast_channel_id']
            
        return settings, target_channel_id, channel_tmpl

    settings, target_channel_id, channel_tmpl = await asyncio.to_thread(fetch_bc_data)
    
    if target_channel_id:
        final_tmpl = channel_tmpl if channel_tmpl else (settings['default_caption'] if settings else "{title} - {artist}")
        caption = final_tmpl.replace('{title}', meta['title']).replace('{artist}', meta['performer']).replace('{sender}', user.first_name)
        try:
            await context.bot.send_audio(chat_id=target_channel_id, audio=file_id, caption=caption)
        except Exception as e:
            logger.error(f"Channel Broadcast Failed: {e}")