# core/services/crawler.py

import re
import time
import sqlite3
import logging
from core.config import Config
from core.models import get_db
from core.services.spotify_official import spotify_keyless
from core.services.youtube import YouTubeService

logger = logging.getLogger(__name__)

class CatalogCrawlerService:
    """
    Autonomous Catalog Pre-Warmer & Ingestion Engine.
    100% Keyless: Combines Spotify Embed Scraping and YouTube Music.
    Dispatches tracks one-by-one into the standard Huey worker queue with full deduplication.
    """

    CHART_PLAYLISTS = {
        'global_top_50': '37i9dQZEVXbMDoHDwVN2tF', # Top 50 - Global
        'todays_top_hits': '37i9dQZF1DXcBWIGoYBM5M', # Today's Top Hits
        'viral_50': '37i9dQZEVXbLiRSasKsNU9',       # Viral 50 - Global
    }

    def __init__(self):
        self.yt = YouTubeService()

    def get_spotify_chart(self, chart_type='global_top_50'):
        """دریافت ۵۰ آهنگ برتر چارت‌های اسپاتیفای به صورت بدون کلید (Keyless)"""
        playlist_id = self.CHART_PLAYLISTS.get(chart_type, chart_type)
        url = f"https://open.spotify.com/playlist/{playlist_id}"
        res = spotify_keyless.parse_link(url)
        if res.get('status') == 'success':
            return {
                'title': res.get('name', 'Spotify Chart'),
                'cover': res.get('cover'),
                'tracks': res.get('tracks', [])
            }
        logger.warning(f"Failed to fetch Spotify chart {chart_type}: {res.get('message')}")
        return {'title': 'Spotify Chart', 'tracks': []}

    def get_artist_top_tracks(self, artist_name, limit=10):
        """دریافت بهترین آهنگ‌های یک خواننده از یوتیوب موزیک"""
        try:
            results = self.yt.yt.search(artist_name, filter="songs", limit=limit)
            tracks = []
            for r in results:
                artists = ', '.join([a['name'] for a in r.get('artists', [])]) if r.get('artists') else artist_name
                tracks.append({
                    'title': r.get('title', 'Unknown Track'),
                    'artist': artists,
                    'videoId': r.get('videoId'),
                    'duration': r.get('duration_seconds'),
                    'cover': r.get('thumbnails', [{}])[-1].get('url') if r.get('thumbnails') else None
                })
            return tracks
        except Exception as e:
            logger.error(f"Error fetching artist top tracks for {artist_name}: {e}")
            return []

    def get_persian_trending(self, limit=25):
        """دریافت برترین و ترندترین موزیک‌های فارسی از یوتیوب موزیک"""
        try:
            results = self.yt.yt.search("Top Persian Songs 2026", filter="songs", limit=limit)
            tracks = []
            for r in results:
                artists = ', '.join([a['name'] for a in r.get('artists', [])]) if r.get('artists') else 'Persian Artist'
                tracks.append({
                    'title': r.get('title', 'Persian Track'),
                    'artist': artists,
                    'videoId': r.get('videoId'),
                    'duration': r.get('duration_seconds'),
                    'cover': r.get('thumbnails', [{}])[-1].get('url') if r.get('thumbnails') else None
                })
            return tracks
        except Exception as e:
            logger.error(f"Error fetching Persian trending: {e}")
            return []

    def parse_custom_link(self, url):
        """پردازش لینک دلخواه کاربر (اسپاتیفای یا یوتیوب)"""
        if 'spotify.com' in url:
            res = spotify_keyless.parse_link(url)
            if res.get('status') == 'success':
                if res.get('type') == 'track':
                    return [res]
                return res.get('tracks', [])
            return []
        elif 'youtube.com' in url or 'youtu.be' in url:
            match = re.search(r'(?:v=|/)([0-9A-Za-z_-]{11}).*', url)
            if match:
                vid = match.group(1)
                info = self.yt.get_video_info(vid)
                return [{
                    'title': info.get('title', 'YouTube Track'),
                    'artist': info.get('artist', 'Unknown Artist'),
                    'videoId': vid
                }]
        return []

    def ingest_tracks_one_by_one(self, tracks, source_label='crawler', max_limit=None):
        """
        تزریق تک‌به‌تک آهنگ‌ها به صف استاندارد Huey با رعایت اصل عدم تکرار (Deduplication).
        """
        from core.tasks import download_and_process_track
        
        queued_count = 0
        skipped_count = 0
        
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            
            for item in tracks:
                if max_limit and queued_count >= max_limit:
                    break
                    
                title = item.get('title')
                artist = item.get('artist')
                vid = item.get('videoId')
                
                # اگر ویدیو آیدی مستقیم نداشتیم (مثلاً از اسپاتیفای آمده بود)، در یوتیوب سرچ می‌کنیم
                if not vid:
                    query = item.get('search_query') or f"{artist} {title}"
                    yt_res = self.yt.search(query)
                    if yt_res:
                        vid = yt_res[0].get('videoId')
                        if not item.get('duration') and yt_res[0].get('duration_seconds'):
                            item['duration'] = yt_res[0].get('duration_seconds')
                
                if not vid:
                    logger.warning(f"Could not resolve video ID for {title} - {artist}")
                    continue

                # فیلتر کردن میکس‌ها و پادکست‌های طولانی (بالای ۱۲ دقیقه / ۷۲۰ ثانیه) که از لیمیت ۵۰ مگابایت تلگرام بیشتر نشوند
                dur = item.get('duration')
                if dur and int(dur) > 720:
                    logger.info(f"Skipping long mix/podcast: {title} ({dur}s)")
                    continue

                # ۱. بررسی تکراری نبودن در جدول tracks (Deduplication)
                existing = conn.execute("SELECT id FROM tracks WHERE youtube_id = ?", (vid,)).fetchone()
                if existing:
                    conn.execute("""
                        INSERT INTO ingestion_logs (title, performer, youtube_id, source, status, error_msg, completed_at)
                        VALUES (?, ?, ?, ?, 'skipped', 'Already in catalog', CURRENT_TIMESTAMP)
                    """, (title, artist, vid, source_label))
                    conn.commit()
                    skipped_count += 1
                    continue

                # ۲. ثبت در لاگ به عنوان queued
                c = conn.cursor()
                c.execute("""
                    INSERT INTO ingestion_logs (title, performer, youtube_id, source, status)
                    VALUES (?, ?, ?, ?, 'queued')
                """, (title, artist, vid, source_label))
                conn.commit()
                log_id = c.lastrowid

                # ۳. ارسال به صف استاندارد ورکر (Huey)
                download_and_process_track(
                    video_id=vid,
                    title=title,
                    artist=artist,
                    user_id=0, # System / Pre-warmer
                    user_first_name="Lyraz",
                    session_token=None,
                    chat_id=None,
                    message_id=None,
                    quality=Config.AUDIO_QUALITY,
                    cover_url=item.get('cover'),
                    duration=item.get('duration'),
                    log_id=log_id
                )
                queued_count += 1
                
        return {
            'queued': queued_count,
            'skipped': skipped_count,
            'total_scanned': len(tracks)
        }

    def get_queue_and_stats(self):
        """استخراج وضعیت لحظه‌ای صف و لاگ‌های اخیر جهت نمایش در پنل ادمین"""
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            
            active_count = conn.execute("SELECT COUNT(*) FROM ingestion_logs WHERE status = 'downloading'").fetchone()[0]
            queued_count = conn.execute("SELECT COUNT(*) FROM ingestion_logs WHERE status = 'queued'").fetchone()[0]
            completed_today = conn.execute("""
                SELECT COUNT(*) FROM ingestion_logs 
                WHERE status = 'completed' AND date(created_at, 'localtime') = date('now', 'localtime')
            """).fetchone()[0]
            skipped_today = conn.execute("""
                SELECT COUNT(*) FROM ingestion_logs 
                WHERE status = 'skipped' AND date(created_at, 'localtime') = date('now', 'localtime')
            """).fetchone()[0]
            
            recent_rows = conn.execute("""
                SELECT id, title, performer, youtube_id, source, status, error_msg, created_at, completed_at
                FROM ingestion_logs
                ORDER BY id DESC
                LIMIT 30
            """).fetchall()
            
            settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            
            return {
                'active_count': active_count,
                'queued_count': queued_count,
                'completed_today': completed_today,
                'skipped_today': skipped_today,
                'logs': [dict(r) for r in recent_rows],
                'settings': {
                    'enabled': bool(settings['crawler_enabled']) if settings and 'crawler_enabled' in settings.keys() else False,
                    'hour': settings['crawler_schedule_hour'] if settings and 'crawler_schedule_hour' in settings.keys() else '04:00',
                    'max_tracks': settings['crawler_max_tracks'] if settings and 'crawler_max_tracks' in settings.keys() else 15,
                    'source': settings['crawler_source'] if settings and 'crawler_source' in settings.keys() else 'global_top_50'
                }
            }

crawler_service = CatalogCrawlerService()
