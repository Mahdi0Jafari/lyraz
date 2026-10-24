# core/services/telegram_leech_service.py
"""
Lyraz Telegram Cloud Leech Service.
Supports direct MTProto web authentication via Admin Panel, dynamic StringSession
storage in SQLite settings table, and multi-bot pool with @DeezerMusicBot priority and @deezload2bot fallback.
"""

import os
import re
import time
import asyncio
import logging
import sqlite3
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    FloodWaitError
)

from core.config import Config

logger = logging.getLogger(__name__)

# Temporary in-memory state for pending auth requests before session finalization
_PENDING_AUTHS = {}

class TelegramLeechService:
    PRIMARY_BOT = "@DeezerMusicBot"
    BACKUP_BOT = "@deezload2bot"
    _active_client = None
    _active_session_str = None
    _active_loop = None
    _loop_locks = {}
    _loop_leech_locks = {}
    _bot_entity = None
    _is_user_busy = False

    @classmethod
    def _get_lock(cls):
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None
        lid = id(curr_loop)
        if lid not in cls._loop_locks:
            cls._loop_locks[lid] = asyncio.Lock()
        return cls._loop_locks[lid]

    @classmethod
    def _get_leech_lock(cls):
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None
        lid = id(curr_loop)
        if lid not in cls._loop_leech_locks:
            cls._loop_leech_locks[lid] = asyncio.Lock()
        return cls._loop_leech_locks[lid]

    @classmethod
    async def get_client(cls):
        """
        Returns a persistent connected TelegramClient singleton.
        Reuses warm MTProto connection, eliminating ~1.5s connection handshake per track.
        """
        settings = cls._get_db_settings()
        session_str = settings.get("userbot_session_string")
        api_id = settings.get("userbot_api_id")
        api_hash = settings.get("userbot_api_hash")

        if not session_str or not settings.get("userbot_enabled") or not api_id or not api_hash:
            return None, "Userbot not configured or disabled in settings."

        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if (cls._active_client and 
            cls._active_session_str == session_str and 
            cls._active_loop == curr_loop and 
            cls._active_client.is_connected()):
            try:
                if await cls._active_client.is_user_authorized():
                    return cls._active_client, None
            except Exception:
                pass

        lock = cls._get_lock()
        async with lock:
            if (cls._active_client and 
                cls._active_session_str == session_str and 
                cls._active_loop == curr_loop and 
                cls._active_client.is_connected()):
                return cls._active_client, None

            if cls._active_client:
                if cls._active_loop and cls._active_loop.is_running() and cls._active_loop != curr_loop:
                    cls._active_client = None
                    cls._bot_entity = None
                else:
                    try:
                        await cls._active_client.disconnect()
                    except Exception:
                        pass
                    cls._active_client = None
                    cls._bot_entity = None

            try:
                client = TelegramClient(StringSession(session_str), int(api_id), str(api_hash).strip())
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    cls._active_client = None
                    cls._bot_entity = None
                    return None, "Userbot session is unauthorized or expired."

                cls._active_client = client
                cls._active_session_str = session_str
                cls._active_loop = curr_loop
                return client, None
            except Exception as e:
                logger.error(f"[LeechEngine] Failed to connect persistent userbot client: {e}")
                return None, str(e)

    @staticmethod
    def _get_db_settings():
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            return dict(row) if row else {}

    @classmethod
    def get_status(cls):
        """بررسی وضعیت فعال بودن و اتصال یوزربات از روی دیتابیس"""
        settings = cls._get_db_settings()
        session_str = settings.get("userbot_session_string")
        has_session = bool(session_str and len(session_str) > 50)
        
        api_id = settings.get("userbot_api_id") or None
        api_hash = settings.get("userbot_api_hash") or ""
        phone = settings.get("userbot_phone") or ""

        # ماسک کردن هش برای نمایش امن در فرانت
        masked_hash = f"{api_hash[:4]}...{api_hash[-4:]}" if api_hash and len(api_hash) > 8 else ""

        failed_count = 0
        try:
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                failed_count = conn.execute("SELECT COUNT(*) FROM campaign_tracks WHERE status = 'failed' AND spotify_url IS NOT NULL").fetchone()[0]
        except Exception:
            pass

        return {
            "is_connected": has_session and bool(settings.get("userbot_enabled")),
            "phone": phone,
            "first_name": settings.get("userbot_first_name") or "",
            "username": settings.get("userbot_username") or "",
            "api_id": api_id or "",
            "api_hash_masked": masked_hash,
            "enabled": bool(settings.get("userbot_enabled")),
            "remaining_failed_count": failed_count
        }

    @classmethod
    async def send_code(cls, api_id, api_hash, phone):
        """Send verification code and save intermediate StringSession to decouple event loops."""
        try:
            api_id = int(api_id)
            api_hash = str(api_hash).strip()
            phone = str(phone).strip()

            if not phone.startswith("+"):
                phone = f"+{phone}"

            session = StringSession()
            client = TelegramClient(session, api_id, api_hash)
            await client.connect()

            res = await client.send_code_request(phone)
            temp_session_str = client.session.save()
            await client.disconnect()

            _PENDING_AUTHS[phone] = {
                "session_string": temp_session_str,
                "phone_code_hash": res.phone_code_hash,
                "api_id": api_id,
                "api_hash": api_hash,
                "created_at": time.time()
            }
            logger.info(f"Verification code sent to {phone}")
            return True, "Verification code sent to your Telegram."
        except FloodWaitError as e:
            logger.warning(f"FloodWait while sending code to {phone}: {e}")
            return False, f"Telegram rate limit: please wait {e.seconds} seconds before retrying."
        except Exception as e:
            logger.error(f"Error sending verification code: {e}")
            return False, f"Error sending verification code: {str(e)}"

    @classmethod
    async def verify_code(cls, phone, code, password=None):
        """Verify code on the current request's event loop with the saved StringSession."""
        phone = str(phone).strip()
        if not phone.startswith("+"):
            phone = f"+{phone}"

        pending = _PENDING_AUTHS.get(phone)
        if not pending:
            return False, "No active session found for this phone number. Please click Send Code first."

        session_string = pending["session_string"]
        phone_code_hash = pending["phone_code_hash"]
        api_id = pending["api_id"]
        api_hash = pending["api_hash"]

        # Create a fresh client directly bound to the current event loop
        client = TelegramClient(StringSession(session_string), api_id, api_hash)
        await client.connect()

        try:
            try:
                await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                if not password:
                    await client.disconnect()
                    return False, "2FA_REQUIRED: Please enter your 2FA Cloud Password."
                await client.sign_in(password=password)

            me = await client.get_me()
            final_session_str = client.session.save()

            # Save securely to SQLite database
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.execute("""
                    UPDATE settings 
                    SET userbot_api_id = ?, 
                        userbot_api_hash = ?, 
                        userbot_phone = ?, 
                        userbot_session_string = ?, 
                        userbot_first_name = ?, 
                        userbot_username = ?, 
                        userbot_enabled = 1
                    WHERE id = 1
                """, (api_id, api_hash, phone, final_session_str, me.first_name, me.username or ""))
                conn.commit()

            await client.disconnect()
            _PENDING_AUTHS.pop(phone, None)

            logger.info(f"Userbot successfully linked and saved to DB for {me.first_name} ({phone})")
            return True, {
                "first_name": me.first_name,
                "username": me.username,
                "phone": phone
            }

        except PhoneCodeInvalidError:
            await client.disconnect()
            return False, "Invalid or expired verification code."
        except PasswordHashInvalidError:
            await client.disconnect()
            return False, "Invalid 2FA password."
        except Exception as e:
            logger.error(f"Error during verify_code: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            return False, f"Authentication error: {str(e)}"

    @classmethod
    def disconnect(cls):
        """غیرفعال‌سازی و حذف سشن ذخیره شده از دیتابیس"""
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.execute("""
                UPDATE settings 
                SET userbot_session_string = NULL, 
                    userbot_first_name = NULL, 
                    userbot_username = NULL, 
                    userbot_enabled = 0
                WHERE id = 1
            """)
            conn.commit()
        if cls._active_client:
            try:
                curr_loop = asyncio.get_event_loop()
                if curr_loop and curr_loop.is_running():
                    curr_loop.create_task(cls._active_client.disconnect())
            except Exception:
                pass
        cls._active_client = None
        cls._bot_entity = None
        logger.info("Userbot session cleared from database.")
        return True

    @classmethod
    def find_existing_in_vault(cls, spotify_id=None, youtube_id=None, title=None, artist=None):
        """
        Check if track already exists in the database vault (storage channel).
        Priorities:
        1. spotify_id
        2. youtube_id
        3. normalized title + performer
        """
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            # 1. Check by spotify_id
            if spotify_id:
                row = conn.execute(
                    "SELECT * FROM tracks WHERE spotify_id = ? AND storage_message_id IS NOT NULL", 
                    (spotify_id,)
                ).fetchone()
                if row:
                    return dict(row), "spotify_id"

            # 2. Check by youtube_id
            if youtube_id:
                row = conn.execute(
                    "SELECT * FROM tracks WHERE youtube_id = ? AND storage_message_id IS NOT NULL", 
                    (youtube_id,)
                ).fetchone()
                if row:
                    return dict(row), "youtube_id"

            # 3. Check by normalized title & performer
            if title and artist:
                clean_t = re.sub(r'[\(\[\{].*?[\)\]\}]', '', title).strip().lower()
                clean_a = re.sub(r'[\(\[\{].*?[\)\]\}]', '', artist).strip().lower()
                primary_artist = clean_a.split(',')[0].split('&')[0].strip()
                if len(clean_t) >= 3 and len(primary_artist) >= 2:
                    row = conn.execute("""
                        SELECT * FROM tracks 
                        WHERE (
                            (LOWER(title) LIKE ? AND LOWER(performer) LIKE ?) OR
                            (LOWER(title) LIKE ? AND LOWER(title) LIKE ?)
                        )
                        AND storage_message_id IS NOT NULL 
                        LIMIT 1
                    """, (f"%{clean_t}%", f"%{primary_artist}%", f"%{clean_t}%", f"%{primary_artist}%")).fetchone()
                    if row:
                        return dict(row), "title_artist"

        return None, None

    @classmethod
    async def leech_track(cls, spotify_url, target_channel_id=None):
        """
        واکشی ابری قطعه موزیک با لینک اسپاتیفای و کپی مستقیم در کانال استوریج تلگرام
        بدون دانلود فیزیکی روی سرور یا مصرف پهنای باند.
        """
        m_sp = re.search(r'track/([a-zA-Z0-9]+)', spotify_url)
        spotify_id = m_sp.group(1) if m_sp else None

        # ⚡️ Deduplication Gate 1: Check if already stored in database channel
        if spotify_id:
            existing, match_type = cls.find_existing_in_vault(spotify_id=spotify_id)
            if existing:
                logger.info(f"[LeechEngine] Duplicate prevented: '{existing['title']}' already in vault via {match_type} (Storage Msg ID: {existing['storage_message_id']})")
                return True, existing

        dest_channel = target_channel_id or Config.STORAGE_CHANNEL_ID
        if not dest_channel:
            return False, "Storage channel STORAGE_CHANNEL_ID is not configured."

        # Re-use persistent warm MTProto client (Zero handshake latency)
        client, err = await cls.get_client()
        if not client:
            return False, err or "Userbot client could not be connected."

        start_time = time.time()
        audio_msg = None
        used_bot = None

        cls._is_user_busy = True
        try:
            async with cls._get_leech_lock():
                # 1. Query Primary Bot: @DeezerMusicBot with auto-retry
                for attempt in range(1, 3):
                    logger.info(f"[LeechEngine] Querying {cls.PRIMARY_BOT} for {spotify_url} (Attempt {attempt}/2)...")
                    sent_req = await client.send_message(cls.PRIMARY_BOT, spotify_url)
                    
                    # High-frequency adaptive polling: check every 0.35s (up to 14s wait per attempt)
                    for _ in range(40):
                        await asyncio.sleep(0.35)
                        recent = await client.get_messages(cls.PRIMARY_BOT, limit=5)
                        for m in recent:
                            if m.id > sent_req.id and (m.document or m.audio):
                                audio_msg = m
                                used_bot = cls.PRIMARY_BOT
                                break
                        if audio_msg:
                            break
                    if audio_msg:
                        break
                    if attempt == 1:
                        logger.info(f"[LeechEngine] {cls.PRIMARY_BOT} delayed, pausing 2s before retry...")
                        await asyncio.sleep(2)

                # 2. Failover to @deezload2bot
                if not audio_msg:
                    logger.warning(f"[LeechEngine] {cls.PRIMARY_BOT} did not respond in time. Failing over to {cls.BACKUP_BOT}...")
                    sent_req2 = await client.send_message(cls.BACKUP_BOT, spotify_url)
                    
                    # Check every 0.4s (up to 14s wait)
                    for _ in range(35):
                        await asyncio.sleep(0.4)
                        recent = await client.get_messages(cls.BACKUP_BOT, limit=5)
                        for m in recent:
                            if m.id > sent_req2.id:
                                if m.document or m.audio:
                                    audio_msg = m
                                    used_bot = cls.BACKUP_BOT
                                    break
                                elif m.buttons and not audio_msg:
                                    try:
                                        await m.click(0, 0)
                                    except Exception:
                                        pass
                        if audio_msg:
                            break

                if not audio_msg or not (audio_msg.document or audio_msg.audio):
                    return False, "No audio file received from Telegram bots."

                # 3. Extract metadata
                doc = audio_msg.document or audio_msg.audio
                title = "Unknown Track"
                performer = "Unknown Artist"
                duration = 0
                for attr in getattr(doc, 'attributes', []):
                    if hasattr(attr, 'title') and attr.title:
                        title = attr.title
                    if hasattr(attr, 'performer') and attr.performer:
                        performer = attr.performer
                    if hasattr(attr, 'duration') and attr.duration:
                        duration = attr.duration

                # 4. Pure Telegram Cloud relay to official Lyraz Bot (Zero Server Bandwidth)
                m_sp = re.search(r'track/([a-zA-Z0-9]+)', spotify_url)
                spotify_id = m_sp.group(1) if m_sp else None

                bot_handle = (Config.BOT_USERNAME or 'LyrazBot').lstrip('@')
                if not cls._bot_entity:
                    cls._bot_entity = await client.get_entity(bot_handle)
                bot_entity = cls._bot_entity
                relay_caption = f"#cloud_leech {spotify_id or 'none'} {dest_channel}"
                
                logger.info(f"[LeechEngine] Relaying '{title}' cloud-to-cloud to @{bot_handle} (Zero download)...")
                await client.send_file(bot_entity, audio_msg.media, caption=relay_caption)
                
                # Anti-flood breathing room for DeezerBot before next track
                await asyncio.sleep(0.8)

            # 5. Wait for official Bot to copy audio to storage channel & save in SQLite (fast 0.15s polling)
            found_track = None
            for _ in range(50):  # up to 7.5s wait
                await asyncio.sleep(0.15)
                with sqlite3.connect(Config.DATABASE_URI) as conn:
                    conn.row_factory = sqlite3.Row
                    if spotify_id:
                        row = conn.execute(
                            "SELECT * FROM tracks WHERE spotify_id = ? AND storage_message_id IS NOT NULL", 
                            (spotify_id,)
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT * FROM tracks WHERE title = ? AND performer = ? AND storage_message_id IS NOT NULL", 
                            (title, performer)
                        ).fetchone()
                    if row:
                        found_track = dict(row)
                        break

            if not found_track:
                return False, "Timed out waiting for official Lyraz Bot to relay audio to storage channel."

            elapsed = round(time.time() - start_time, 2)
            logger.info(
                f"[LeechEngine] Successfully cloud-leeched '{found_track['title']}' via {used_bot} -> "
                f"@{bot_handle} -> Channel in {elapsed}s (Storage Msg ID: {found_track['storage_message_id']})"
            )

            return True, {
                "id": found_track.get('id'),
                "title": found_track['title'],
                "performer": found_track['performer'],
                "duration": found_track['duration'],
                "file_size": found_track['file_size'],
                "file_id": found_track['file_id'],
                "file_unique_id": found_track['file_unique_id'],
                "storage_message_id": found_track['storage_message_id'],
                "spotify_id": spotify_id,
                "elapsed": elapsed,
                "provider": used_bot
            }

        except Exception as e:
            logger.error(f"[LeechEngine] Leech error: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            cls._active_client = None
            cls._bot_entity = None
            return False, f"Cloud leech error: {str(e)}"
        finally:
            cls._is_user_busy = False

    @classmethod
    async def ingest_and_register_track(cls, spotify_url, target_channel_id=None, title=None, artist=None, youtube_id=None):
        """
        Leech track via userbot, relay to official Lyraz Bot, and register in database tracks table.
        Checks vault first to avoid re-uploading duplicates to storage channel.
        """
        m = re.search(r'track/([a-zA-Z0-9]+)', spotify_url)
        spotify_id = m.group(1) if m else None

        # ⚡️ Deduplication Gate 2: Check vault by spotify_id, youtube_id, or title+artist
        existing, match_type = cls.find_existing_in_vault(
            spotify_id=spotify_id,
            youtube_id=youtube_id,
            title=title,
            artist=artist
        )
        if existing:
            # Link spotify_id to existing track if missing
            if spotify_id and not existing.get('spotify_id'):
                with sqlite3.connect(Config.DATABASE_URI) as conn:
                    conn.execute("UPDATE tracks SET spotify_id = ? WHERE id = ?", (spotify_id, existing['id']))
                    conn.commit()

            # Mark completed in campaign_tracks
            if spotify_id or spotify_url:
                with sqlite3.connect(Config.DATABASE_URI) as conn:
                    conn.execute("""
                        UPDATE campaign_tracks 
                        SET status = 'completed', 
                            error_msg = 'Resolved via existing vault match',
                            delivered_at = CURRENT_TIMESTAMP
                        WHERE (spotify_url = ? OR spotify_url LIKE ?) AND status != 'completed'
                    """, (spotify_url, f"%{spotify_id}%"))
                    conn.commit()

            logger.info(f"[LeechEngine] Duplicate resolved from vault via {match_type}: '{existing['title']}' (Storage Msg: {existing['storage_message_id']})")
            return True, existing, "cached"

        ok, res = await cls.leech_track(spotify_url, target_channel_id)
        if not ok:
            return False, res, "leech_failed"

        return True, res, "ingested"

    @classmethod
    async def rescue_failed_batch(cls, batch_size=5):
        """
        ترمیم دسته‌ای و ایمن قطعات شکست‌خورده با نرخ کنترل‌شده
        """
        settings = cls._get_db_settings()
        if not settings.get('userbot_enabled') or not settings.get('userbot_session_string'):
            return False, "Userbot engine is not enabled or connected."

        if cls._is_user_busy:
            logger.info("[RescueEngine] User download is actively running. Deferring background cloud rescue.")
            return True, {"rescued": 0, "failed": 0, "message": "User download active"}

        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            failed_tracks = conn.execute("""
                SELECT id, title, artist, spotify_url, youtube_id, campaign_id
                FROM campaign_tracks
                WHERE status = 'failed' AND spotify_url IS NOT NULL
                ORDER BY id ASC
                LIMIT ?
            """, (batch_size,)).fetchall()

        if not failed_tracks:
            return True, {"rescued": 0, "failed": 0, "message": "No failed tracks with Spotify links found for rescue."}

        rescued_count = 0
        failed_count = 0

        for trk in failed_tracks:
            sp_url = trk['spotify_url']
            ok, res, reason = await cls.ingest_and_register_track(
                sp_url, 
                title=trk['title'], 
                artist=trk['artist'], 
                youtube_id=trk['youtube_id']
            )
            if ok:
                rescued_count += 1
                logger.info(f"[RescueEngine] Rescued ({reason}): {trk['title']} - {trk['artist']}")
            else:
                failed_count += 1
                logger.warning(f"[RescueEngine] Failed to rescue {trk['title']}: {res}")

            # تاخیر امن فقط زمانی که واکشی واقعی از شبکه انجام شده باشد
            if reason != "cached":
                await asyncio.sleep(3)

        return True, {
            "processed": len(failed_tracks),
            "rescued": rescued_count,
            "failed": failed_count,
            "remaining": cls.get_status().get("remaining_failed_count", 0)
        }

# سینگلتون سراسری سرویس
telegram_leech_service = TelegramLeechService()
