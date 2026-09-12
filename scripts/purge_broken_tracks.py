#!/usr/bin/env python3
# scripts/purge_broken_tracks.py
"""
Purge broken cached tracks from Telegram storage channel and database.

Finds all tracks cached after the broken deployment (2026-09-09),
deletes their messages from the Telegram storage channel, and removes them
from the database so the next user request triggers a clean re-download
with correct cover art and ID3v2.3 tags.

Usage:
  python3 scripts/purge_broken_tracks.py --list             # dry-run, list only
  python3 scripts/purge_broken_tracks.py --purge            # purge since default date
  python3 scripts/purge_broken_tracks.py --purge --since '2026-09-09 07:00:00'
  python3 scripts/purge_broken_tracks.py --purge --all      # purge ALL tracks (careful!)
"""

import os
import sys
import asyncio
import sqlite3
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("purge_broken_tracks")

# Last broken deployment: commit 494ea33 (feat(matcher)) - 2026-09-09
DEFAULT_BROKEN_SINCE = "2026-09-09 07:00:00"


def get_db():
    conn = sqlite3.connect(Config.DATABASE_URI)
    conn.row_factory = sqlite3.Row
    return conn


def find_broken_tracks(since_datetime):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, performer, youtube_id, file_id, file_unique_id,
                   storage_message_id, created_at
            FROM tracks
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (since_datetime,)
        ).fetchall()
        return [dict(r) for r in rows]


def find_all_tracks():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, performer, youtube_id, file_id, file_unique_id,
                   storage_message_id, created_at
            FROM tracks ORDER BY created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


async def delete_telegram_message(bot, storage_message_id, track_title):
    if not storage_message_id:
        logger.warning(f"  No storage_message_id for '{track_title}', skipping Telegram deletion")
        return False
    try:
        await bot.delete_message(
            chat_id=Config.STORAGE_CHANNEL_ID,
            message_id=storage_message_id
        )
        logger.info(f"  Deleted Telegram msg {storage_message_id} for '{track_title}'")
        return True
    except Exception as e:
        err = str(e).lower()
        if "message to delete not found" in err or "message_id_invalid" in err:
            logger.warning(f"  Message {storage_message_id} already gone for '{track_title}'")
            return True
        logger.error(f"  Failed to delete msg {storage_message_id} for '{track_title}': {e}")
        return False


def delete_from_database(track_ids, file_unique_ids):
    if not track_ids:
        return
    placeholders = ",".join("?" * len(track_ids))
    with get_db() as conn:
        if file_unique_ids:
            uid_ph = ",".join("?" * len(file_unique_ids))
            deleted_lyrics = conn.execute(
                f"DELETE FROM lyrics_cache WHERE file_unique_id IN ({uid_ph})",
                file_unique_ids
            ).rowcount
            logger.info(f"  Removed {deleted_lyrics} lyrics_cache entries")

        try:
            deleted_pl = conn.execute(
                f"DELETE FROM playlist_items WHERE track_id IN ({placeholders})",
                track_ids
            ).rowcount
            if deleted_pl:
                logger.info(f"  Removed {deleted_pl} playlist_items")
        except sqlite3.OperationalError:
            pass

        deleted = conn.execute(
            f"DELETE FROM tracks WHERE id IN ({placeholders})",
            track_ids
        ).rowcount
        conn.commit()
        logger.info(f"  Deleted {deleted} tracks from DB")


async def run_purge(tracks, dry_run=False):
    from telegram import Bot
    from telegram.request import HTTPXRequest

    if not tracks:
        print("\nNo tracks found matching criteria. Nothing to purge.")
        return

    label = "DRY RUN - " if dry_run else ""
    print(f"\n{label}Found {len(tracks)} tracks:\n")
    print(f"  {'ID':>4} | {'Created At':^19} | {'StorageMsg':>10} | Track")
    print(f"  {'-'*4}-+-{'-'*19}-+-{'-'*10}-+-{'-'*45}")
    for t in tracks:
        msg_id = t.get('storage_message_id') or 'N/A'
        name = f"{t.get('performer','?')} - {t.get('title','?')}"
        print(f"  {t['id']:>4} | {str(t.get('created_at','N/A')):^19} | {str(msg_id):>10} | {name[:60]}")

    if dry_run:
        print(f"\nDRY RUN done. Run with --purge to delete {len(tracks)} tracks.")
        return

    print(f"\nStarting purge of {len(tracks)} tracks...")
    req = HTTPXRequest(connect_timeout=20.0, read_timeout=30.0, write_timeout=30.0)
    bot = Bot(token=Config.BOT_TOKEN, request=req)
    await bot.initialize()

    try:
        deleted_tg = 0
        failed_tg = 0
        track_ids = []
        uids = []

        for i, track in enumerate(tracks, 1):
            performer = track.get('performer', '?')
            title = track.get('title', '?')
            storage_msg_id = track.get('storage_message_id')
            uid = track.get('file_unique_id')
            logger.info(f"[{i}/{len(tracks)}] {performer} - {title}")

            if storage_msg_id and Config.STORAGE_CHANNEL_ID:
                ok = await delete_telegram_message(bot, storage_msg_id, title)
                if ok:
                    deleted_tg += 1
                else:
                    failed_tg += 1
                await asyncio.sleep(0.3)

            track_ids.append(track['id'])
            if uid:
                uids.append(uid)

        print()
        delete_from_database(track_ids, uids)

        print()
        print("=" * 55)
        print("Purge Complete!")
        print(f"  Telegram messages deleted : {deleted_tg}")
        if failed_tg:
            print(f"  Telegram deletions failed : {failed_tg}")
        print(f"  DB tracks purged          : {len(track_ids)}")
        print(f"  Lyrics cache purged       : {len(uids)}")
        print()
        print("Tracks will be freshly re-downloaded on next user request")
        print("with correct cover art, ID3v2.3 tags, and synced lyrics.")
        print("=" * 55)
    finally:
        try:
            await bot.shutdown()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Purge broken cached tracks")
    parser.add_argument("--list", action="store_true", help="Dry run - list only")
    parser.add_argument("--purge", action="store_true", help="Actually delete")
    parser.add_argument("--since", type=str, default=DEFAULT_BROKEN_SINCE,
                        help=f"Delete tracks created since this datetime (default: {DEFAULT_BROKEN_SINCE})")
    parser.add_argument("--all", action="store_true", help="Target ALL tracks (dangerous!)")

    args = parser.parse_args()

    if not args.list and not args.purge:
        parser.print_help()
        sys.exit(0)

    if not Config.BOT_TOKEN:
        logger.error("BOT_TOKEN not set. Check .env")
        sys.exit(1)

    if not Config.STORAGE_CHANNEL_ID:
        logger.warning("STORAGE_CHANNEL_ID not set - Telegram deletion will be skipped")

    if args.all:
        tracks = find_all_tracks()
        mode = "ALL tracks"
    else:
        tracks = find_broken_tracks(args.since)
        mode = f"tracks since {args.since}"

    print(f"\nMode: {mode}")

    if args.list:
        asyncio.run(run_purge(tracks, dry_run=True))
    elif args.purge:
        if args.all:
            c = input(f"\nWARNING: purge ALL {len(tracks)} tracks? Type 'YES I KNOW': ").strip()
            if c != "YES I KNOW":
                print("Aborted.")
                sys.exit(0)
        asyncio.run(run_purge(tracks, dry_run=False))


if __name__ == "__main__":
    main()
