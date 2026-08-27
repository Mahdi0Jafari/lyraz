# core/services/spotify_official.py

import re
import json
import logging
import requests

logger = logging.getLogger(__name__)

class SpotifyScraperService:
    """
    استخراج متادیتای رسمی و باکیفیت اسپاتیفای از طریق ویجت‌های عمومی (Embeds)
    با مکانیزم پشتیبان OpenGraph Meta Tags.
    کاملاً رایگان، بدون تحریم و بدون نیاز به کلید یا اکانت پرمیوم.
    """
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        })
        logger.info("🥷 Spotify Embed Scraper Initialized.")

    def extract_id_and_type(self, url):
        clean_url = url.split('?')[0]
        match = re.search(r'spotify\.com/(track|playlist|album)/([a-zA-Z0-9]+)', clean_url)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _scrape_embed(self, item_type, item_id):
        """
        استخراج دیتا از صفحه Embed اسپاتیفای با خواندن اسکریپت __NEXT_DATA__.
        """
        embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
        try:
            res = self.session.get(embed_url, timeout=10)
            if res.status_code != 200:
                logger.warning(f"Spotify embed returned status {res.status_code}")
                return None

            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', res.text, re.DOTALL)
            if not match:
                logger.warning("Could not find __NEXT_DATA__ in embed HTML.")
                return None

            data = json.loads(match.group(1))
            entity = data.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity', {})
            return entity
        except Exception as e:
            logger.error(f"Embed Scrape Error: {e}")
            return None

    def _scrape_opengraph_fallback(self, item_type, item_id):
        """
        لایه رزرو: استخراج اطلاعات از متا تگ‌های OpenGraph در صورت بروز خطا در صفحه امبد.
        """
        url = f"https://open.spotify.com/{item_type}/{item_id}"
        try:
            res = self.session.get(url, timeout=10)
            if res.status_code == 200:
                title_m = re.search(r'<meta property="og:title" content="(.*?)"', res.text)
                desc_m = re.search(r'<meta property="og:description" content="(.*?)"', res.text)
                img_m = re.search(r'<meta property="og:image" content="(.*?)"', res.text)

                title = title_m.group(1) if title_m else "Unknown Track"
                artist = "Unknown Artist"
                if desc_m:
                    parts = [p.strip() for p in desc_m.group(1).split('·')]
                    if parts and parts[0]:
                        artist = parts[0]

                cover = img_m.group(1) if img_m else None
                return {'title': title, 'artist': artist, 'cover': cover}
        except Exception as e:
            logger.warning(f"OpenGraph fallback failed: {e}")
        return None

    def get_track_info(self, track_id):
        entity = self._scrape_embed('track', track_id)
        if entity:
            title = entity.get('title') or entity.get('name') or "Unknown Track"
            
            # استخراج اسامی تمام خوانندگان از فیلد artists
            artists_raw = entity.get('artists', [])
            if isinstance(artists_raw, list) and artists_raw:
                artist_names = [a['name'] for a in artists_raw if isinstance(a, dict) and 'name' in a]
                artist = ', '.join(artist_names) if artist_names else (entity.get('subtitle') or "Unknown Artist")
            else:
                artist = entity.get('subtitle') or "Unknown Artist"

            # استخراج باکیفیت‌ترین کاور آلبوم (آخرین تصویر معمولاً ۶۴۰×۶۴۰ است)
            images = entity.get('visualIdentity', {}).get('image', [])
            cover_url = images[-1].get('url') if images else None
            
            # طول آهنگ به ثانیه
            duration = round(entity.get('duration', 0) / 1000) if entity.get('duration') else None

            return {
                "type": "track",
                "title": title,
                "artist": artist,
                "search_query": f"{artist} {title}",
                "cover": cover_url,
                "duration": duration
            }

        # در صورت در دسترس نبودن امبد، استفاده از تگ‌های OpenGraph
        fb = self._scrape_opengraph_fallback('track', track_id)
        if fb:
            return {
                "type": "track",
                "title": fb['title'],
                "artist": fb['artist'],
                "search_query": f"{fb['artist']} {fb['title']}",
                "cover": fb['cover'],
                "duration": None
            }

        return None

    def _parse_tracklist(self, entity, item_type):
        """پردازش لیست آهنگ‌ها برای پلی‌لیست و آلبوم"""
        if not entity or 'trackList' not in entity:
            return None

        playlist_name = entity.get('title') or entity.get('name') or f"Spotify {item_type.capitalize()}"

        cover_url = None
        try:
            images = entity.get('visualIdentity', {}).get('image', [])
            if images:
                cover_url = images[-1].get('url')
            elif 'coverArt' in entity and entity['coverArt'].get('sources'):
                cover_url = entity['coverArt']['sources'][0].get('url')
            elif 'image' in entity:
                cover_url = entity['image']
            elif 'thumbnailUrl' in entity:
                cover_url = entity['thumbnailUrl']
        except Exception:
            pass

        tracks = []
        for track in entity['trackList'][:100]: # محدود به ۱۰۰ آهنگ اول
            title = track.get('title') or track.get('name')
            
            # استخراج دقیق خواننده از لیست artists یا subtitle
            artists_raw = track.get('artists', [])
            if isinstance(artists_raw, list) and artists_raw:
                artist_names = [a['name'] for a in artists_raw if isinstance(a, dict) and 'name' in a]
                artist = ', '.join(artist_names) if artist_names else (track.get('subtitle') or "Unknown Artist")
            else:
                artist = track.get('subtitle') or "Unknown Artist"

            duration = round(track.get('duration', 0) / 1000) if track.get('duration') else None

            if title and artist:
                tracks.append({
                    "title": title,
                    "artist": artist,
                    "search_query": f"{artist} {title}",
                    "duration": duration
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

spotify_keyless = SpotifyScraperService()