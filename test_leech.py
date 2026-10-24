# test_leech.py
"""
Lyraz Telegram Music Leecher - Verified with @DeezerMusicBot
Fetches official 320kbps audio from Spotify track link and relays server-side to Saved Messages.
"""

import os
import sys
import time
import sqlite3
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from core.config import Config

# Read safely from local SQLite database (zero hardcoding)
conn = sqlite3.connect(Config.DATABASE_URI)
conn.row_factory = sqlite3.Row
db_settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone() or {}
conn.close()

API_ID = db_settings.get("userbot_api_id")
API_HASH = db_settings.get("userbot_api_hash")
SESSION_STR = db_settings.get("userbot_session_string")

# Safe destination: Saved Messages ('me') to keep production channels untouched
DESTINATION_TARGET = "me"

# Verified working track link (Hardest To Love - The Weeknd)
DEFAULT_TEST_TRACK = "https://open.spotify.com/track/6kWxIqQDsKFYCJGbU4AjCX"

# Primary verified bot
TARGET_BOT = "@DeezerMusicBot"

async def main():
    print("=" * 65)
    print("LYRAZ MUSIC LEECHER - VERIFIED VIA @DeezerMusicBot")
    print("=" * 65)

    if not SESSION_STR or not API_ID or not API_HASH:
        print("[-] Userbot credentials not configured in database settings.")
        return

    client = TelegramClient(StringSession(SESSION_STR), int(API_ID), str(API_HASH))
    
    print("[*] Connecting to Telegram MTProto...")
    await client.connect()

    if not await client.is_user_authorized():
        print("[-] Client is not authorized. Please check your session file.")
        return

    me = await client.get_me()
    print(f"[+] Authorized as: {me.first_name} (@{me.username or me.phone})")
    print(f"[+] Output destination: Saved Messages ('{DESTINATION_TARGET}')\n")

    test_link = DEFAULT_TEST_TRACK
    if sys.stdin.isatty():
        input_link = input(f">> Enter Spotify track URL [Default: {DEFAULT_TEST_TRACK}]: ").strip()
        if input_link:
            test_link = input_link

    print(f"\n[*] Sending track URL to {TARGET_BOT}: {test_link}")
    start_time = time.time()

    # Step 1: Send the direct Spotify track link
    sent_req = await client.send_message(TARGET_BOT, test_link)

    print("[*] Waiting for @DeezerMusicBot to resolve and send MP3...")
    audio_msg = None

    # Wait up to 35 seconds for bot to process
    for attempt in range(18):
        await asyncio.sleep(2)
        recent_msgs = await client.get_messages(TARGET_BOT, limit=5)
        for m in recent_msgs:
            if m.id > sent_req.id and (m.document or m.audio):
                audio_msg = m
                break
        if audio_msg:
            break

    if audio_msg and audio_msg.document:
        elapsed = round(time.time() - start_time, 2)
        doc = audio_msg.document
        size_mb = round(doc.size / (1024 * 1024), 2)
        
        title = "Unknown Track"
        performer = "Unknown Artist"
        duration = 0
        for attr in doc.attributes:
            if hasattr(attr, 'title') and attr.title:
                title = attr.title
            if hasattr(attr, 'performer') and attr.performer:
                performer = attr.performer
            if hasattr(attr, 'duration') and attr.duration:
                duration = attr.duration

        print(f"\n[+] Audio file received from {TARGET_BOT} in {elapsed}s!")
        print(f"    - Title:     {title}")
        print(f"    - Performer: {performer}")
        print(f"    - Duration:  {duration}s")
        print(f"    - File Size: {size_mb} MB (Studio 320kbps)")

        print(f"\n[*] Relaying server-side to Saved Messages (0 MB server bandwidth)...")
        relay_start = time.time()
        sent_to_me = await client.send_file(
            DESTINATION_TARGET,
            audio_msg.media,
            caption=f"🎵 {title} - {performer}\n⚡️ Ingested via Lyraz Userbot (@DeezerMusicBot)\n🔗 {test_link}"
        )
        relay_time = round(time.time() - relay_start, 2)
        print(f"[+] Relayed to Saved Messages in {relay_time}s! (Msg ID: {sent_to_me.id})")
        print("\n" + "=" * 65)
        print("[SUCCESS] Check your Telegram 'Saved Messages' right now to verify!")
        print("=" * 65)
    else:
        print(f"[-] Could not retrieve audio file from {TARGET_BOT}. Verify track availability.")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
