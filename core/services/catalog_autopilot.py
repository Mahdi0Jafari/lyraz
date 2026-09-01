# core/services/catalog_autopilot.py

import time
import logging
import sqlite3
from core.config import Config
from core.models import get_db
from core.services.spotify_extractor import spotify_extractor

logger = logging.getLogger(__name__)

# حافظه کش برای فید رادار اسپاتیفای جهت کاهش درخواست‌های تکراری
_RADAR_CACHE = {
    "data": None,
    "cached_at": 0
}
RADAR_CACHE_TTL = 1800  # ۳۰ دقیقه کش

class CatalogAutopilotService:
    """
    موتور هوشمند و خودکار گنجینه طلایی و رادار کشف اسپاتیفای (Spotify Radar & Vault Autopilot)
    مدیریت تزریق پیوسته (Drip-Feed) دیسکوگرافی هنرمندان و پلی‌لیست‌ها با سرعت بهینه و ایمن.
    """

    TARGET_GOAL = 25000  # هدف طلایی تعداد قطعات آرشیو

    def get_vault_metrics(self):
        """محاسبه شاخص‌های زنده و آماری گنجینه طلایی لایراز"""
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            
            total_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            total_size_bytes = conn.execute("SELECT COALESCE(SUM(file_size), 0) FROM tracks").fetchone()[0]
            total_duration_sec = conn.execute("SELECT COALESCE(SUM(duration), 0) FROM tracks").fetchone()[0]
            
            # آمار امروز
            ingested_today = conn.execute("""
                SELECT COUNT(*) FROM ingestion_logs 
                WHERE status = 'completed' AND date(created_at, 'localtime') = date('now', 'localtime')
            """).fetchone()[0]

            campaign_artists_count = conn.execute("SELECT COUNT(*) FROM artist_campaigns").fetchone()[0]
            completed_campaigns = conn.execute("SELECT COUNT(*) FROM artist_campaigns WHERE status = 'completed'").fetchone()[0]
            
            # تنظیمات اتوپایلوت
            settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            autopilot_enabled = bool(settings['autopilot_enabled']) if settings and 'autopilot_enabled' in settings.keys() else False
            target_goal = int(settings['autopilot_target_goal']) if settings and 'autopilot_target_goal' in settings.keys() and settings['autopilot_target_goal'] else self.TARGET_GOAL

            percent = round((total_tracks / max(1, target_goal)) * 100, 1)
            if percent > 100: percent = 100.0

            return {
                "total_tracks": total_tracks,
                "target_goal": target_goal,
                "progress_percent": percent,
                "total_size_mb": round(total_size_bytes / (1024 * 1024), 1),
                "total_size_gb": round(total_size_bytes / (1024 * 1024 * 1024), 2),
                "total_hours": round(total_duration_sec / 3600, 1),
                "ingested_today": ingested_today,
                "total_artists": campaign_artists_count,
                "completed_artists": completed_campaigns,
                "autopilot_enabled": autopilot_enabled
            }

    def get_radar_feed(self, force_refresh=False):
        """دریافت فید زنده رادار اسپاتیفای به همراه وضعیت سینک در دیتابیس محلی"""
        global _RADAR_CACHE
        now = time.time()
        
        if force_refresh or not _RADAR_CACHE["data"] or (now - _RADAR_CACHE["cached_at"]) > RADAR_CACHE_TTL:
            try:
                raw_radar = spotify_extractor.fetch_live_curated_radar()
                _RADAR_CACHE["data"] = raw_radar
                _RADAR_CACHE["cached_at"] = now
            except Exception as e:
                logger.error(f"Error fetching live Spotify radar: {e}")
                if not _RADAR_CACHE["data"]:
                    return {"categories": [], "playlists": []}

        radar_data = _RADAR_CACHE["data"]
        
        # تطبیق با دیتابیس محلی
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            
            campaigns = conn.execute("SELECT id, artist_name, spotify_id, total_tracks, completed_tracks, status, hub_token FROM artist_campaigns").fetchall()
            campaign_map = {c['spotify_id']: dict(c) for c in campaigns if c['spotify_id']}
            artist_name_map = {c['artist_name'].lower().strip(): dict(c) for c in campaigns if c['artist_name']}

            enriched_categories = []
            for cat in radar_data.get("categories", []):
                enriched_artists = []
                for a in cat.get("artists", []):
                    sp_id = a.get("id")
                    name = a.get("name", "")
                    
                    matched_camp = campaign_map.get(sp_id) or artist_name_map.get(name.lower().strip())
                    
                    artist_status = "ready" # آماده برای لانچ
                    camp_id = None
                    hub_token = None
                    completed_count = 0
                    total_count = 0
                    
                    if matched_camp:
                        camp_id = matched_camp['id']
                        hub_token = matched_camp.get('hub_token')
                        completed_count = matched_camp['completed_tracks'] or 0
                        total_count = matched_camp['total_tracks'] or 0
                        if matched_camp['status'] == 'completed':
                            artist_status = "completed"
                        else:
                            artist_status = "in_progress"

                    enriched_artists.append({
                        **a,
                        "status": artist_status,
                        "campaign_id": camp_id,
                        "hub_token": hub_token,
                        "completed_tracks": completed_count,
                        "total_tracks": total_count
                    })

                enriched_categories.append({
                    **cat,
                    "artists": enriched_artists
                })

            return {
                "categories": enriched_categories,
                "playlists": radar_data.get("playlists", [])
            }

    def launch_artist_campaign(self, spotify_id, target_channel_id=None):
        """راه‌اندازی فوری کمپین استخراج و دانلود دیسکوگرافی یک خواننده از اسپاتیفای"""
        from core.services.admin_service import admin_service
        
        # ۱. واکشی دیسکوگرافی از اسپاتیفای
        disco = spotify_extractor.fetch_artist_discography(spotify_id)
        if not disco or not disco.get("tracks"):
            raise ValueError("Could not extract discography or artist has no tracks.")

        artist_name = disco["artist_name"]
        avatar_url = disco.get("artist_image")
        spotify_url = disco.get("artist_url")
        tracks = disco["tracks"]

        # ۲. استفاده از سرویس مدیریت کمپین
        target_ch = target_channel_id or Config.STORAGE_CHANNEL_ID
        campaign_id = admin_service.create_artist_campaign(
            artist_name=artist_name,
            spotify_id=spotify_id,
            spotify_url=spotify_url,
            avatar_url=avatar_url,
            target_channel_id=str(target_ch),
            tracks_data=tracks
        )

        return {
            "campaign_id": campaign_id,
            "artist_name": artist_name,
            "total_tracks": len(tracks)
        }

    def launch_playlist_ingestion(self, playlist_id, category_label="Playlist"):
        """استخراج تمام قطعات یک پلی‌لیست اسپاتیفای و ارسال تک‌به‌تک به صف ورکر با عدم تکرار"""
        from core.services.crawler import crawler_service
        
        pl_data = spotify_extractor.fetch_playlist_tracks(playlist_id)
        if not pl_data or not pl_data.get("tracks"):
            raise ValueError("Could not extract playlist tracks.")

        formatted_tracks = []
        for t in pl_data["tracks"]:
            formatted_tracks.append({
                "title": t.get("title"),
                "artist": t.get("artist_string"),
                "cover": (t.get("album") or {}).get("cover_url"),
                "duration": t.get("duration_seconds"),
                "search_query": f"{t.get('artist_string')} {t.get('title')}"
            })

        res = crawler_service.ingest_tracks_one_by_one(
            formatted_tracks,
            source_label=f"pl:{pl_data.get('title', 'playlist')[:15]}",
            max_limit=len(formatted_tracks)
        )

        return {
            "playlist_id": playlist_id,
            "title": pl_data.get("title"),
            "total_scanned": len(formatted_tracks),
            "queued": res.get("queued", 0),
            "skipped": res.get("skipped", 0)
        }

    def autopilot_tick(self):
        """
        تپش دوره‌ای اتوپایلوت (هر چند دقیقه یک‌بار):
        اگر اتوپایلوت فعال باشد، بررسی می‌کند که آیا صف ورکر خلوت است یا خیر؛ در این صورت آرتیست یا پلی‌لیست بعدی را لانچ می‌کند.
        """
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            conn.row_factory = sqlite3.Row
            settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            if not settings or 'autopilot_enabled' not in settings.keys() or not settings['autopilot_enabled']:
                return

            # ۱. بررسی شلوغی صف
            active_or_queued = conn.execute("""
                SELECT COUNT(*) FROM campaign_tracks WHERE status IN ('downloading', 'queued')
            """).fetchone()[0]

            if active_or_queued > 30:
                # صف در حال حاضر پر است، صبر تا خلوت شدن
                return

            # ۲. پیدا کردن خواننده بعدی از رادار که هنوز کمپین آن ساخته نشده است
            feed = self.get_radar_feed(force_refresh=False)
            candidate_artist = None
            for cat in feed.get("categories", []):
                for art in cat.get("artists", []):
                    if art.get("status") == "ready":
                        candidate_artist = art
                        break
                if candidate_artist:
                    break

            if candidate_artist:
                logger.info(f"⚡️ [Autopilot] Auto-Launching Next Artist: {candidate_artist['name']} ({candidate_artist['id']})...")
                try:
                    self.launch_artist_campaign(candidate_artist["id"])
                    logger.info(f"✅ [Autopilot] Artist {candidate_artist['name']} dispatched to queue.")
                except Exception as e:
                    logger.error(f"Autopilot launch error for {candidate_artist.get('name')}: {e}")

catalog_autopilot = CatalogAutopilotService()
