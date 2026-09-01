# core/services/soundcloud.py

import os
import re
import logging
import requests
import yt_dlp
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class SoundCloudService:
    """
    سرویس استخراج مشخصات و تحلیل لینک‌های ساندکلاد (SoundCloud Direct Extractor)
    پشتیبانی از لینک‌های دسکتاپ، موبایل (on.soundcloud.com) و پلی‌لیست‌ها.
    """
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        })

    def resolve_url(self, raw_url):
        """
        رزولوشین و حل ریدایرکت لینک‌های کوتاه موبایل (on.soundcloud.com) به آدرس اصلی
        """
        raw_url = (raw_url or "").strip()
        if not raw_url:
            return ""

        if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = "https://" + raw_url

        if "on.soundcloud.com" in raw_url:
            try:
                res = self.session.head(raw_url, allow_redirects=True, timeout=8)
                return res.url
            except Exception as e:
                logger.warning(f"Failed to resolve on.soundcloud.com shortlink: {e}")
                return raw_url

        return raw_url

    def extract_info(self, url):
        """
        واکشی متادیتا، کاور HD و ساختار قطعه یا پلی‌لیست از ساندکلاد با yt-dlp
        """
        canonical_url = self.resolve_url(url)
        if not canonical_url:
            return {'status': 'error', 'message': 'Invalid SoundCloud URL'}

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'socket_timeout': 8,
            'nocheckcertificate': True
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(canonical_url, download=False)
                if not info:
                    return {'status': 'error', 'message': 'Could not fetch SoundCloud metadata'}

                info_type = info.get('_type')

                # حالت ۱: پلی‌لیست یا آلبوم (Set)
                if info_type == 'playlist' or '/sets/' in canonical_url:
                    entries = info.get('entries') or []
                    tracks = []
                    for idx, entry in enumerate(entries, 1):
                        if not entry:
                            continue
                        e_id = entry.get('id')
                        e_title = entry.get('title') or f"Track {idx}"
                        e_artist = entry.get('uploader') or entry.get('artist') or "SoundCloud Artist"
                        e_cover = entry.get('thumbnail') or info.get('thumbnail')
                        e_duration = int(entry.get('duration') or 0)
                        e_url = entry.get('url') or entry.get('webpage_url') or canonical_url

                        # تمیزکاری کاور به فرمت اورجینال
                        if e_cover and '-large' in e_cover:
                            e_cover = e_cover.replace('-large', '-t500x500')

                        tracks.append({
                            'id': f"sc_{e_id}",
                            'title': e_title,
                            'artist': e_artist,
                            'cover': e_cover,
                            'duration': e_duration,
                            'source_url': e_url,
                            'search_query': f"{e_artist} {e_title}"
                        })

                    return {
                        'status': 'success',
                        'type': 'playlist',
                        'title': info.get('title') or 'SoundCloud Set',
                        'artist': info.get('uploader') or 'SoundCloud',
                        'cover': info.get('thumbnail'),
                        'tracks': tracks,
                        'total': len(tracks),
                        'url': canonical_url
                    }

                # حالت ۲: تک‌آهنگ (Single Track)
                t_id = info.get('id')
                t_title = info.get('title') or 'SoundCloud Track'
                t_artist = info.get('uploader') or info.get('artist') or 'SoundCloud Artist'
                t_cover = info.get('thumbnail')
                if t_cover and '-large' in t_cover:
                    t_cover = t_cover.replace('-large', '-t500x500')

                t_duration = int(info.get('duration') or 0)

                return {
                    'status': 'success',
                    'type': 'track',
                    'id': f"sc_{t_id}",
                    'title': t_title,
                    'artist': t_artist,
                    'cover': t_cover,
                    'duration': t_duration,
                    'source_url': canonical_url,
                    'url': canonical_url
                }

        except Exception as e:
            logger.error(f"SoundCloud extraction exception: {e}")
            return {'status': 'error', 'message': f'SoundCloud Error: {str(e)}'}

soundcloud_service = SoundCloudService()
