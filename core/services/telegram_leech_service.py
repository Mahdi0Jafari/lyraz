# core/services/telegram_leech_service.py
"""
Lyraz Telegram Cloud Leech Service.
Supports direct MTProto web authentication via Admin Panel, dynamic StringSession
storage in SQLite settings table, and multi-bot pool with @DeezerMusicBot priority and @deezload2bot fallback.
"""

import os
import time
import asyncio
import logging
import sqlite3
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
        logger.info("Userbot session cleared from database.")
        return True

    @classmethod
    async def leech_track(cls, spotify_url, target_channel_id=None):
        """
        واکشی ابری قطعه موزیک با لینک اسپاتیفای و کپی مستقیم در کانال استوریج تلگرام
        بدون دانلود فیزیکی روی سرور یا مصرف پهنای باند.
        """
        settings = cls._get_db_settings()
        session_str = settings.get("userbot_session_string")
        if not session_str or not settings.get("userbot_enabled"):
            return False, "Userbot engine is not enabled or connected."

        api_id = settings.get("userbot_api_id")
        api_hash = settings.get("userbot_api_hash")
        if not api_id or not api_hash:
            return False, "Userbot API ID or API Hash is not set in database settings."

        api_id = int(api_id)
        api_hash = str(api_hash).strip()
        dest_channel = target_channel_id or Config.STORAGE_CHANNEL_ID
        if not dest_channel:
            return False, "Storage channel STORAGE_CHANNEL_ID is not configured."

        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return False, "Userbot session is unauthorized or expired. Please reconnect."

        start_time = time.time()
        audio_msg = None
        used_bot = None

        try:
            # 1. Query Primary Bot: @DeezerMusicBot
            logger.info(f"[LeechEngine] Querying {cls.PRIMARY_BOT} for {spotify_url}...")
            sent_req = await client.send_message(cls.PRIMARY_BOT, spotify_url)
            
            for _ in range(12):  # up to 24s wait
                await asyncio.sleep(2)
                recent = await client.get_messages(cls.PRIMARY_BOT, limit=5)
                for m in recent:
                    if m.id > sent_req.id and (m.document or m.audio):
                        audio_msg = m
                        used_bot = cls.PRIMARY_BOT
                        break
                if audio_msg:
                    break

            # 2. Failover to @deezload2bot
            if not audio_msg:
                logger.warning(f"[LeechEngine] {cls.PRIMARY_BOT} did not respond in time. Failing over to {cls.BACKUP_BOT}...")
                sent_req2 = await client.send_message(cls.BACKUP_BOT, spotify_url)
                
                for _ in range(10):  # up to 20s wait
                    await asyncio.sleep(2)
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
                await client.disconnect()
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

            # 4. Cloud relay directly to Lyraz storage channel
            caption = f"🎵 {title} - {performer}\n⚡️ Ingested by Lyraz Userbot ({used_bot})\n🔗 {spotify_url}"
            sent_to_storage = await client.send_file(
                dest_channel,
                audio_msg.media,
                caption=caption
            )
            
            elapsed = round(time.time() - start_time, 2)
            await client.disconnect()

            logger.info(f"[LeechEngine] Successfully leeched '{title}' via {used_bot} in {elapsed}s to channel {dest_channel}")

            return True, {
                "title": title,
                "performer": performer,
                "duration": duration,
                "file_size": getattr(doc, 'size', 0),
                "storage_message_id": sent_to_storage.id,
                "elapsed": elapsed,
                "provider": used_bot
            }

        except Exception as e:
            logger.error(f"[LeechEngine] Leech error: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            return False, f"Cloud leech error: {str(e)}"

    @classmethod
    async def ingest_and_register_track(cls, spotify_url, target_channel_id=None):
        """
        واکشی ابری قطعه، استخراج شناسه رسمی Bot API با فوروارد داخلی و ثبت نهایی در جدول tracks دیتابیس
        """
        import re
        import requests

        m = re.search(r'track/([a-zA-Z0-9]+)', spotify_url)
        spotify_id = m.group(1) if m else None

        # بررسی وجود قبلی در دیتابیس
        if spotify_id:
            with sqlite3.connect(Config.DATABASE_URI) as conn:
                conn.row_factory = sqlite3.Row
                existing = conn.execute("SELECT * FROM tracks WHERE spotify_id = ?", (spotify_id,)).fetchone()
                if existing:
                    return True, dict(existing), "cached"

        ok, res = await cls.leech_track(spotify_url, target_channel_id)
        if not ok:
            return False, res, "leech_failed"

        storage_msg_id = res['storage_message_id']
        dest_channel = target_channel_id or Config.STORAGE_CHANNEL_ID

        # استخراج file_id و file_unique_id مخصوص Bot API از طریق فوروارد موقت در همان چنل
        file_id = None
        file_unique_id = None
        try:
            fwd_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/forwardMessage"
            fwd_res = requests.post(fwd_url, json={
                "chat_id": dest_channel,
                "from_chat_id": dest_channel,
                "message_id": storage_msg_id
            }, timeout=8.0).json()

            if fwd_res.get('ok'):
                audio_obj = fwd_res['result'].get('audio')
                if audio_obj:
                    file_id = audio_obj.get('file_id')
                    file_unique_id = audio_obj.get('file_unique_id')

                # حذف پیام فوروارد تکراری جهت نظم کانال استوریج
                try:
                    del_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/deleteMessage"
                    requests.post(del_url, json={
                        "chat_id": dest_channel,
                        "message_id": fwd_res['result']['message_id']
                    }, timeout=4.0)
                except Exception:
                    pass
        except Exception as fwd_err:
            logger.warning(f"[LeechEngine] Error resolving Bot API file_id: {fwd_err}")

        # درج دائمی در جدول tracks
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO tracks 
                (file_unique_id, file_id, title, performer, duration, file_size, spotify_id, storage_message_id, is_tagged)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                file_unique_id or f"sp_{spotify_id}",
                file_id or "",
                res['title'],
                res['performer'],
                res['duration'],
                res['file_size'],
                spotify_id,
                storage_msg_id
            ))
            track_id = cursor.lastrowid

            # تغییر وضعیت به completed در جدول کمپین‌ها
            if spotify_id or spotify_url:
                conn.execute("""
                    UPDATE campaign_tracks 
                    SET status = 'completed', error_msg = 'Rescued via Telegram Leech'
                    WHERE (spotify_url = ? OR spotify_url LIKE ?) AND status = 'failed'
                """, (spotify_url, f"%{spotify_id}%"))

            conn.commit()
            saved_row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
            return True, dict(saved_row) if saved_row else res, "ingested"

    @classmethod
    async def rescue_failed_batch(cls, batch_size=5):
        """
        ترمیم دسته‌ای و ایمن قطعات شکست‌خورده با نرخ کنترل‌شده
        """
        settings = cls._get_db_settings()
        if not settings.get('userbot_enabled') or not settings.get('userbot_session_string'):
            return False, "Userbot engine is not enabled or connected."

        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            failed_tracks = conn.execute("""
                SELECT id, title, artist, spotify_url, campaign_id
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
            ok, res, reason = await cls.ingest_and_register_track(sp_url)
            if ok:
                rescued_count += 1
                logger.info(f"[RescueEngine] Rescued: {trk['title']} - {trk['artist']}")
            else:
                failed_count += 1
                logger.warning(f"[RescueEngine] Failed to rescue {trk['title']}: {res}")

            # تاخیر امن برای جلوگیری از ریت‌لیمیت بات‌ها
            await asyncio.sleep(3)

        return True, {
            "processed": len(failed_tracks),
            "rescued": rescued_count,
            "failed": failed_count,
            "remaining": cls.get_status().get("remaining_failed_count", 0)
        }

# سینگلتون سراسری سرویس
telegram_leech_service = TelegramLeechService()
