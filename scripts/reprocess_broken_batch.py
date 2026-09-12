#!/usr/bin/env python3
# scripts/reprocess_broken_batch.py
"""
ابزار تخصصی پاکسازی و دانلود مجدد ۱۰۰ آهنگ پلی‌لیست معیوب
- حذف پیام‌ها از کانال آرشیو تلگرام
- پاکسازی رکوردهای خراب از دیتابیس (tracks & lyrics_cache)
- ارسال مجدد به صف Huey برای دانلود تمیز با متادیتای اسپاتیفای و ID3v2.3
"""

import os
import sys
import asyncio
import sqlite3
import logging
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reprocess_batch")


def get_db():
    conn = sqlite3.connect(Config.DATABASE_URI)
    conn.row_factory = sqlite3.Row
    return conn


def get_target_tracks(min_msg=38806, max_msg=38905):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, title, performer, youtube_id, file_unique_id, storage_message_id
            FROM tracks
            WHERE storage_message_id >= ? AND storage_message_id <= ?
            ORDER BY storage_message_id ASC
        """, (min_msg, max_msg)).fetchall()
        return [dict(r) for r in rows]


async def run_cleanup_and_redownload(tracks, dry_run=False):
    from telegram import Bot
    from telegram.request import HTTPXRequest

    if not tracks:
        print("No tracks found in the specified range.")
        return

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Found {len(tracks)} tracks to purge and redownload:")
    for t in tracks[:10]:
        print(f"  msg={t['storage_message_id']} | yt={t['youtube_id']} | {t['performer']} - {t['title']}")
    if len(tracks) > 10:
        print(f"  ... and {len(tracks) - 10} more tracks")

    if dry_run:
        print("\nDry run finished. Run with --execute to perform actual cleanup and re-download.")
        return

    req = HTTPXRequest(connect_timeout=20.0, read_timeout=30.0, write_timeout=30.0)
    bot = Bot(token=Config.BOT_TOKEN, request=req)
    await bot.initialize()

    # ۱. حذف پیام‌ها از کانال تلگرام
    print(f"\n🗑  1. Deleting {len(tracks)} messages from Telegram channel {Config.STORAGE_CHANNEL_ID}...")
    deleted_msgs = 0
    for i, t in enumerate(tracks, 1):
        mid = t.get('storage_message_id')
        if mid and Config.STORAGE_CHANNEL_ID:
            try:
                await bot.delete_message(chat_id=Config.STORAGE_CHANNEL_ID, message_id=mid)
                deleted_msgs += 1
            except Exception as e:
                logger.warning(f"  Could not delete msg {mid}: {e}")
            await asyncio.sleep(0.25)
        if i % 15 == 0:
            print(f"  Progress: {i}/{len(tracks)} deleted...")

    await bot.shutdown()
    print(f"✅ Deleted {deleted_msgs} messages from channel.")

    # ۲. پاکسازی از دیتابیس
    print("\n🗑  2. Purging records from database...")
    track_ids = [t['id'] for t in tracks]
    uids = [t['file_unique_id'] for t in tracks if t.get('file_unique_id')]

    with get_db() as conn:
        if uids:
            uid_ph = ",".join("?" * len(uids))
            conn.execute(f"DELETE FROM lyrics_cache WHERE file_unique_id IN ({uid_ph})", uids)
        
        id_ph = ",".join("?" * len(track_ids))
        conn.execute(f"DELETE FROM tracks WHERE id IN ({id_ph})", track_ids)
        conn.commit()

    print(f"✅ Removed {len(track_ids)} tracks from database cache.")

    # ۳. صف‌بندی برای دانلود مجدد تمیز
    print("\n🚀 3. Queueing tracks for fresh download in Huey...")
    from core.tasks import download_and_process_track

    for i, t in enumerate(tracks, 1):
        vid = t['youtube_id']
        title = t['title']
        artist = t['performer']
        
        # ثبت لاگ دانلود
        with get_db() as conn:
            cur = conn.execute("""
                INSERT INTO ingestion_logs (title, performer, youtube_id, source, status)
                VALUES (?, ?, ?, 'playlist_repair', 'queued')
            """, (title, artist, vid))
            log_id = cur.lastrowid
            conn.commit()

        download_and_process_track(
            video_id=vid,
            title=title,
            artist=artist,
            user_id=1,  # Mahdi
            user_first_name="Mahdi",
            session_token=None,
            chat_id=None,
            message_id=None,
            quality=Config.AUDIO_QUALITY,
            duration=None,
            log_id=log_id,
            priority=10
        )
        logger.info(f"  [{i}/{len(tracks)}] Enqueued: {artist} - {title} ({vid})")

    print(f"\n🎉 All {len(tracks)} tracks have been enqueued for fresh download!")
    print("Workers will download them with authentic covers, ID3v2.3 tags, and upload new audio files to the storage channel.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Perform actual deletion and re-download")
    parser.add_argument("--min-msg", type=int, default=38806)
    parser.add_argument("--max-msg", type=int, default=38905)
    args = parser.parse_args()

    tracks = get_target_tracks(args.min_msg, args.max_msg)
    asyncio.run(run_cleanup_and_redownload(tracks, dry_run=not args.execute))
