# core/services/spotify_official.py

import re
import json
import logging
import requests

logger = logging.getLogger(__name__)

class SpotifyScraperService:
    """
    هک کردن متادیتای اسپاتیفای از طریق ویجت‌های عمومی (Spotify Embeds).
    کاملاً رایگان، بدون تحریم و بدون نیاز به کلید یا اکانت پرمیوم.
    """
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        })
        logger.info("🥷 Spotify Embed Scraper Initialized.")

    def extract_id_and_type(self, url):
        clean_url = url.split('?')[0]
        match = re.search(r'spotify\.com/(track|playlist|album)/([a-zA-Z0-9]+)', clean_url)
        if match: return match.group(1), match.group(2)
        return None, None

    def _scrape_embed(self, item_type, item_id):
        """
        استخراج دیتا از صفحه Embed (ویجت) اسپاتیفای.
        """
        # نکته کلیدی: کلمه embed در لینک اضافه شده است
        embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
        try:
            res = self.session.get(embed_url, timeout=10)
            if res.status_code != 200:
                logger.error(f"Embed returned status {res.status_code}")
                return None

            # استخراج آبجکت مخفی Next.js
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', res.text, re.DOTALL)
            if not match:
                logger.error("Could not find __NEXT_DATA__ in embed HTML.")
                return None

            data = json.loads(match.group(1))
            
            # مسیریابی امن در دل ساختار JSON برای رسیدن به لیست آهنگ‌ها
            entity = data.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity', {})
            return entity
        except Exception as e:
            logger.error(f"Embed Scrape Error: {e}")
            return None

    def get_track_info(self, track_id):
        entity = self._scrape_embed('track', track_id)
        if not entity: return None
        
        title = entity.get('title') or entity.get('name', 'Unknown')
        artist = entity.get('subtitle') or "Unknown"
        
        return {
            "type": "track",
            "title": title,
            "artist": artist,
            "search_query": f"{artist} {title}"
        }

    def _parse_tracklist(self, entity, item_type):
        """پردازش لیست آهنگ‌ها برای پلی‌لیست و آلبوم"""
        if not entity or 'trackList' not in entity:
            return None
            
        # 🔥 استخراج نام و کاور آلبوم/پلی‌لیست برای نمایش در ربات تلگرام
        playlist_name = entity.get('title') or entity.get('name') or f"Spotify {item_type.capitalize()}"
        
        cover_url = None
        try:
            # معماری JSON اسپاتیفای ممکن است متغیر باشد، بررسی تمام سناریوها
            if 'coverArt' in entity and entity['coverArt'].get('sources'):
                cover_url = entity['coverArt']['sources'][0].get('url')
            elif 'image' in entity:
                cover_url = entity['image']
            elif 'thumbnailUrl' in entity:
                cover_url = entity['thumbnailUrl']
        except Exception:
            pass

        tracks = []
        for track in entity['trackList'][:100]: # محدود به ۱۰۰ آهنگ اول
            title = track.get('title')
            artist = track.get('subtitle')
            if title and artist:
                tracks.append({
                    "title": title,
                    "artist": artist,
                    "search_query": f"{artist} {title}"
                })
                
        if tracks:
            return {
                "type": item_type,
                "name": playlist_name,
                "cover": cover_url,
                "track_count": len(tracks),
                "tracks": tracks
            }
        return None

    def get_playlist_tracks(self, playlist_id):
        entity = self._scrape_embed('playlist', playlist_id)
        return self._parse_tracklist(entity, 'playlist')

    def get_album_tracks(self, album_id):
        entity = self._scrape_embed('album', album_id)
        return self._parse_tracklist(entity, 'album')

    def parse_link(self, url):
        link_type, item_id = self.extract_id_and_type(url)
        if not link_type or not item_id:
            return {"status": "error", "message": "Invalid Spotify link."}
            
        if link_type == 'track':
            info = self.get_track_info(item_id)
        elif link_type == 'playlist':
            info = self.get_playlist_tracks(item_id)
        elif link_type == 'album':
            info = self.get_album_tracks(item_id)
        else:
            return {"status": "error", "message": "Unsupported link type."}
            
        if info:
            info['status'] = 'success'
            return info
        return {"status": "error", "message": f"{link_type.capitalize()} is private or empty."}

# نام نمونه قدیمی حفظ شده است
spotify_keyless = SpotifyScraperService()