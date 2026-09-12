#!/usr/bin/env python3
"""
Lyraz Music Player - Retroactive Lyrics Backfill Engine
Scans the tracks database and populates lyrics_cache with synced/plain lyrics from LRCLIB.
Usage:
    python3 scripts/backfill_lyrics.py [--limit 100] [--delay 0.25]
"""

import sys
import os
import time
import sqlite3
import argparse
import logging

# تنظیم مسیر پروژه
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.services.metadata import metadata_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("backfill_lyrics")

def backfill(limit=None, delay=0.25):
    db_path = Config.DATABASE_URI
    logger.info(f"Connecting to database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT t.id, t.file_unique_id, t.title, t.performer, t.duration, t.youtube_id
        FROM tracks t
        LEFT JOIN lyrics_cache l ON t.file_unique_id = l.file_unique_id
        WHERE l.file_unique_id IS NULL AND t.title IS NOT NULL AND t.title != ''
        ORDER BY t.id DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    cursor.execute(query)
    tracks = cursor.fetchall()
    total = len(tracks)

    logger.info(f"Found {total} tracks missing lyrics in lyrics_cache.")
    if total == 0:
        logger.info("All tracks already have lyrics cached!")
        return

    found_count = 0
    synced_count = 0
    not_found_count = 0

    for idx, trk in enumerate(tracks, 1):
        fid = trk['file_unique_id']
        title = trk['title']
        performer = trk['performer']
        duration = trk['duration']
        yt_id = trk['youtube_id']

        try:
            lyrics = metadata_service.fetch_lyrics(
                artist=performer,
                title=title,
                duration=duration,
                video_id=yt_id
            )

            if lyrics:
                is_synced = lyrics.startswith('[')
                source_tag = "lrclib" if "[" in lyrics else "plain"
                cursor.execute(
                    "INSERT OR REPLACE INTO lyrics_cache (file_unique_id, lyrics, source, updated_at) VALUES (?, ?, ?, ?)",
                    (fid, lyrics, source_tag, int(time.time()))
                )
                conn.commit()
                found_count += 1
                if is_synced:
                    synced_count += 1
                logger.info(f"[{idx}/{total}] ✅ {title} - {performer} (Synced: {is_synced})")
            else:
                not_found_count += 1
                logger.debug(f"[{idx}/{total}] ❌ Not found: {title} - {performer}")

        except Exception as e:
            logger.warning(f"[{idx}/{total}] ⚠️ Error on {title}: {e}")

        time.sleep(delay)

    logger.info("=" * 50)
    logger.info(f"Backfill Completed! Processed: {total} | Found: {found_count} (Synced: {synced_count}) | Not Found: {not_found_count}")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lyraz Lyrics Backfill")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of tracks to backfill")
    parser.add_argument("--delay", type=float, default=0.25, help="Delay between API calls in seconds")
    args = parser.parse_args()

    backfill(limit=args.limit, delay=args.delay)
