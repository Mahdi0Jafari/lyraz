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
            
            # ۱. تعداد کل فایل‌های صوتی یکتا در دیتابیس و کلود (دقیقاً منطبق با هدر پنل)
            total_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

            campaign_artists_count = conn.execute("SELECT COUNT(*) FROM artist_campaigns").fetchone()[0]
            completed_campaigns = conn.execute("SELECT COUNT(*) FROM artist_campaigns WHERE status = 'completed'").fetchone()[0]
            
            settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            autopilot_enabled = bool(settings['autopilot_enabled']) if settings and 'autopilot_enabled' in settings.keys() else False

            # ۲. قطعات باقیمانده در صف انتظار یا در حال پردازش
            pending_tracks = conn.execute("""
                SELECT COUNT(*) FROM campaign_tracks WHERE status IN ('queued', 'downloading')
            """).fetchone()[0]

            # هدف پویا و یکدست: مجموع فایل‌های تکمیل‌شده + فایل‌های در حال انتظار صف
            target_goal = total_tracks + pending_tracks
            if target_goal == 0:
                target_goal = 1

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
                "radar_artists": campaign_artists_count,
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
            
            campaigns = conn.execute("SELECT id, artist_name, spotify_id, spotify_url, avatar_url, total_tracks, completed_tracks, status, hub_token FROM artist_campaigns").fetchall()
            campaign_map = {c['spotify_id']: dict(c) for c in campaigns if c['spotify_id']}
            artist_name_map = {c['artist_name'].lower().strip(): dict(c) for c in campaigns if c['artist_name']}

            enriched_categories = []
            matched_camp_ids = set()

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
                        matched_camp_ids.add(camp_id)
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

            # آرتیست‌هایی که خارج از دسته‌بندی‌های ثابت اولیه و به صورت خودکار کشف شده‌اند
            discovered_camps = [c for c in campaigns if c['id'] not in matched_camp_ids]
            if discovered_camps:
                discovered_artists = []
                for dc in discovered_camps:
                    dcd = dict(dc)
                    discovered_artists.append({
                        "id": dcd.get('spotify_id'),
                        "name": dcd.get('artist_name'),
                        "image": dcd.get('avatar_url'),
                        "followers": 0,
                        "genres": ["Autonomous Discovery"],
                        "spotify_url": dcd.get('spotify_url'),
                        "status": "completed" if dcd.get('status') == 'completed' else "in_progress",
                        "campaign_id": dcd.get('id'),
                        "hub_token": dcd.get('hub_token'),
                        "completed_tracks": dcd.get('completed_tracks') or 0,
                        "total_tracks": dcd.get('total_tracks') or 0
                    })

                enriched_categories.append({
                    "id": "auto_discoveries",
                    "key": "auto_discoveries",
                    "title": "🔮 کشف‌های هوشمند رادار",
                    "subtitle": "هنرمندان جدید کشف‌شده به صورت خودکار از گراف اسپاتیفای",
                    "is_default": False,
                    "artists": discovered_artists
                })

            return {
                "categories": enriched_categories,
                "playlists": radar_data.get("playlists", [])
            }

    def launch_artist_campaign(self, spotify_id, target_channel_id=None):
        """راه‌اندازی فوری کمپین استخراج و دانلود دیسکوگرافی یک خواننده از اسپاتیفای"""
        # ۱. واکشی دیسکوگرافی از اسپاتیفای
        disco = spotify_extractor.fetch_artist_discography(spotify_id)
        if not disco or not disco.get("tracks"):
            raise ValueError("Could not extract discography or artist has no tracks.")

        artist_name = disco["artist_name"]
        avatar_url = disco.get("artist_image")
        spotify_url = disco.get("artist_url")
        tracks = disco["tracks"]

        target_ch = target_channel_id if target_channel_id and str(target_channel_id) != str(Config.STORAGE_CHANNEL_ID) else None
        
        with sqlite3.connect(Config.DATABASE_URI) as conn:
            existing = conn.execute("SELECT id FROM artist_campaigns WHERE spotify_id = ? OR LOWER(artist_name) = ?", (spotify_id, artist_name.lower().strip())).fetchone()
            if existing:
                campaign_id = existing[0]
                logger.info(f"Campaign #{campaign_id} already exists for {artist_name}, skipping new campaign creation.")
                return {
                    "campaign_id": campaign_id,
                    "artist_name": artist_name,
                    "total_tracks": len(tracks)
                }

            cur = conn.execute("""
                INSERT INTO artist_campaigns (artist_name, spotify_id, spotify_url, avatar_url, target_channel_id, total_tracks, completed_tracks, status)
                VALUES (?, ?, ?, ?, ?, ?, 0, 'processing')
            """, (
                artist_name,
                spotify_id,
                spotify_url,
                avatar_url,
                str(target_ch) if target_ch else None,
                len(tracks)
            ))
            campaign_id = cur.lastrowid
            conn.commit()

        # پاکسازی کش فید رادار
        global _RADAR_CACHE
        _RADAR_CACHE["data"] = None
        _RADAR_CACHE["cached_at"] = 0

        # ۲. ارسال تسک به ورکر پس‌زمینه Huey
        from core.tasks import ingest_artist_campaign_task
        ingest_artist_campaign_task(
            campaign_id=campaign_id,
            tracks=tracks,
            artist_name=artist_name,
            target_channel_id=str(target_ch) if target_ch else None
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

            # ۰. همگام‌سازی خودکار وضعیت کمپین‌های تکمیل‌شده
            conn.execute("""
                UPDATE artist_campaigns 
                SET completed_tracks = (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = artist_campaigns.id AND status = 'completed'),
                    total_tracks = (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = artist_campaigns.id),
                    status = 'completed',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status != 'completed'
                  AND (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = artist_campaigns.id AND status IN ('queued', 'downloading')) = 0
                  AND (SELECT COUNT(*) FROM campaign_tracks WHERE campaign_id = artist_campaigns.id AND status = 'completed') > 0
            """)
            conn.commit()

            # ۱. بررسی شلوغی صف
            active_or_queued = conn.execute("""
                SELECT COUNT(*) FROM campaign_tracks WHERE status IN ('downloading', 'queued')
            """).fetchone()[0]

            if active_or_queued > 30:
                # صف در حال حاضر پر است، صبر تا خلوت شدن
                return

            # ۲. 🔥 فرآیند خودترمیمی خودکار (Auto-Healing): دانلود آهنگ‌های فیل‌شده یا در صف مانده
            stuck_tracks = conn.execute("""
                SELECT ct.id, ct.title, ct.artist, ct.youtube_id, ct.cover_url, ct.duration_seconds, ac.target_channel_id, ac.artist_name 
                FROM campaign_tracks ct
                JOIN artist_campaigns ac ON ct.campaign_id = ac.id
                WHERE ct.status = 'failed'
                   OR (ct.status = 'queued' AND ct.created_at < datetime('now', '-30 minutes'))
                LIMIT 10
            """).fetchall()

            if stuck_tracks:
                logger.info(f"🔄 [Autopilot Auto-Healer] Auto-retrying {len(stuck_tracks)} stuck/failed tracks...")
                from core.tasks import download_and_process_track
                for trk in stuck_tracks:
                    conn.execute("UPDATE campaign_tracks SET status = 'queued', error_msg = NULL, created_at = CURRENT_TIMESTAMP WHERE id = ?", (trk['id'],))
                    
                    cur = conn.execute("""
                        INSERT INTO ingestion_logs (title, performer, youtube_id, source, status)
                        VALUES (?, ?, ?, ?, 'queued')
                    """, (trk['title'], trk['artist'], trk['youtube_id'], f"auto_retry:{trk['artist_name'][:12]}"))
                    log_id = cur.lastrowid

                    target_ch = trk['target_channel_id']
                    distinct_target_ch = target_ch if target_ch and str(target_ch) != str(Config.STORAGE_CHANNEL_ID) else None

                    download_and_process_track(
                        video_id=trk['youtube_id'],
                        title=trk['title'],
                        artist=trk['artist'],
                        user_id=0,
                        user_first_name="AutoHealer",
                        session_token=None,
                        chat_id=None,
                        message_id=None,
                        quality=None,
                        cover_url=trk['cover_url'],
                        duration=trk['duration_seconds'],
                        log_id=log_id,
                        target_channel_id=distinct_target_ch,
                        priority=12
                    )
                conn.commit()
                # در این تیک روی ترمیم آهنگ‌های قبلی تمرکز می‌کنیم
                return

            # ۳. بررسی مستقیم دیتابیس برای جلوگیری قطعی از تکرار کمپین‌ها
            existing_camps = conn.execute("SELECT spotify_id, LOWER(artist_name) FROM artist_campaigns").fetchall()
            existing_sp_ids = set(r[0] for r in existing_camps if r[0])
            existing_names = set(r[1] for r in existing_camps if r[1])

            # ۳. پیدا کردن خواننده بعدی از رادار
            feed = self.get_radar_feed(force_refresh=False)
            candidate_artist = None
            for cat in feed.get("categories", []):
                for art in cat.get("artists", []):
                    art_id = art.get("id")
                    art_name = (art.get("name") or "").lower().strip()
                    if art_id not in existing_sp_ids and art_name not in existing_names:
                        candidate_artist = art
                        break
                if candidate_artist:
                    break

            # ۴. 🔮 کشف خودکار ۲۴ ساعته (Continuous Autonomous Discovery): اگر لیست اصلی تمام شد، کشف آرتیست‌های هم‌سبک
            if not candidate_artist:
                try:
                    completed_parents = conn.execute("""
                        SELECT spotify_id, artist_name FROM artist_campaigns 
                        WHERE spotify_id IS NOT NULL AND status = 'completed'
                        ORDER BY RANDOM() LIMIT 5
                    """).fetchall()
                    for parent in completed_parents:
                        related = spotify_extractor.fetch_related_artists(parent['spotify_id'])
                        for r_art in related:
                            r_id = r_art.get('id')
                            r_name = (r_art.get('name') or '').lower().strip()
                            if r_id and r_id not in existing_sp_ids and r_name not in existing_names:
                                candidate_artist = r_art
                                logger.info(f"🔮 [Autopilot Auto-Discovery] Found new related artist via {parent['artist_name']}: {r_art['name']} ({r_id})")
                                break
                        if candidate_artist:
                            break
                except Exception as disc_err:
                    logger.warning(f"Auto-Discovery related artists error: {disc_err}")

            if candidate_artist:
                logger.info(f"⚡️ [Autopilot] Auto-Launching Next Artist: {candidate_artist['name']} ({candidate_artist['id']})...")
                try:
                    self.launch_artist_campaign(candidate_artist["id"])
                    logger.info(f"✅ [Autopilot] Artist {candidate_artist['name']} dispatched to queue.")
                except Exception as e:
                    logger.error(f"Autopilot launch error for {candidate_artist.get('name')}: {e}")

catalog_autopilot = CatalogAutopilotService()
