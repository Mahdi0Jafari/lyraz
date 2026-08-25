# core/services/metadata.py

import re
import io
import urllib.parse
import logging
import requests
from difflib import SequenceMatcher
from PIL import Image

logger = logging.getLogger(__name__)

class MetadataOrchestrator:
    """
    مغز متمرکز استخراج و استانداردسازی اطلاعات آهنگ.
    وظایف: کشف کاور ۶۰۰×۶۰۰ (iTunes)، استخراج لیریک زمانی (LRCLIB) و بهینه‌سازی عکس.
    """
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'LyrazMusicPlayer/5.0 (Enterprise Metadata Engine)'
        })

    def _clean_string(self, text):
        """حذف عبارات اضافی مثل (Official Video) یا [Lyrics] برای جستجوی دقیق‌تر"""
        if not text: return ""
        return re.sub(r'[\(\[].*?[\)\]]', '', text).strip()

    def _similarity(self, a, b):
        """محاسبه شباهت دو رشته برای پیدا کردن دقیق‌ترین لیریک"""
        if not a or not b: return 0.0
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _optimize_cover(self, image_bytes):
        """
        پردازش تصویر: تغییر اندازه به 600x600 و تبدیل به Baseline JPEG.
        این کار باعث می‌شود حجم هدر MP3 پایین بماند و روی تمام پلیرها سریع لود شود.
        """
        try:
            img = Image.open(io.BytesIO(image_bytes))
            # حذف لایه آلفا (شفافیت) در صورت وجود PNG
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # تغییر سایز استاندارد کاور آلبوم
            img = img.resize((600, 600), Image.Resampling.LANCZOS)
            
            output = io.BytesIO()
            # ذخیره بدون حالت progressive تا در پلیر ماشین و تلویزیون‌های قدیمی خوانده شود
            img.save(output, format='JPEG', quality=85, optimize=True, progressive=False)
            return output.getvalue()
        except Exception as e:
            logger.error(f"Image Optimization Error: {e}")
            return None

    def fetch_itunes_data(self, artist, title):
        """استخراج شناسنامه رسمی آهنگ و کاور باکیفیت از اپل با بررسی دقیق شباهت"""
        clean_artist = self._clean_string(artist)
        clean_title = self._clean_string(title)
        
        if not clean_title or clean_title.lower() in ['unknown track', 'youtube track', 'video']:
            return None

        has_artist = bool(clean_artist and clean_artist.lower() not in ['unknown', 'unknown artist'])
        query_str = f"{clean_artist} {clean_title}" if has_artist else clean_title

        try:
            query = urllib.parse.quote(query_str)
            url = f"https://itunes.apple.com/search?term={query}&media=music&entity=song&limit=5"
            res = self.session.get(url, timeout=5).json()
            
            for track in res.get('results', []):
                t_name = track.get('trackName', '')
                a_name = track.get('artistName', '')
                
                t_sim = self._similarity(t_name, clean_title)
                a_sim = self._similarity(a_name, clean_artist) if has_artist else 1.0
                
                # اگر خواننده مشخص بود، باید هم نام آهنگ و هم خواننده همخوانی داشته باشند
                if has_artist:
                    is_match = (t_sim >= 0.55 and a_sim >= 0.40) or (t_sim >= 0.85 and a_sim >= 0.30)
                else:
                    is_match = (t_sim >= 0.75)
                
                if is_match:
                    cover_url = track.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    img_res = self.session.get(cover_url, timeout=5) if cover_url else None
                    cover_bytes = None
                    if img_res and img_res.status_code == 200:
                        cover_bytes = self._optimize_cover(img_res.content)

                    return {
                        'artist': a_name,
                        'title': t_name,
                        'cover_url': cover_url,
                        'cover_bytes': cover_bytes
                    }
        except Exception as e:
            logger.warning(f"iTunes Fetch Error: {e}")

        return None

    def fetch_lyrics(self, artist, title, duration=None):
        """جستجوی دقیق متن هماهنگ‌شده (Synced LRC) از LRCLIB"""
        search_artist = self._clean_string(artist)
        search_title = self._clean_string(title)
        
        queries = [f"{search_artist} {search_title}"]
        if len(search_title) > 3: 
            queries.append(search_title)

        candidates = []
        for q in queries:
            try:
                res = self.session.get("https://lrclib.net/api/search", params={'q': q}, timeout=10)
                if res.status_code == 200:
                    results = res.json()
                    if results:
                        candidates.extend(results)
                        if q == queries[0]: break 
            except Exception as e:
                logger.warning(f"LRCLIB Fetch Error: {e}")
                continue 

        best_match = None
        highest_score = 0.0

        for cand in candidates:
            if not cand.get('syncedLyrics') and not cand.get('plainLyrics'): 
                continue
            
            # تلورانس ۵ ثانیه‌ای برای اختلاف طول آهنگ در یوتیوب و اسپاتیفای
            time_diff = abs(cand.get('duration', 0) - duration) if duration else 0
            if duration and time_diff > 5: 
                continue 

            t_sim = self._similarity(cand['trackName'], search_title)
            a_sim = self._similarity(cand['artistName'], search_artist)
            
            score = (t_sim * 3.0) + (a_sim * 2.0)
            if time_diff <= 2: score += 2.0

            if score > highest_score:
                highest_score = score
                best_match = cand

        if best_match and highest_score > 3.0:
            # اولویت با لیریک سینک شده (LRC) است، در غیر اینصورت متن ساده
            return best_match.get('syncedLyrics') or best_match.get('plainLyrics')
        return None

    def get_full_metadata(self, raw_artist, raw_title, duration=None, thumbnail_url=None):
        """
        نقطه ورود اصلی برای دریافت پکیج کامل اطلاعات آهنگ.
        یک دیکشنری تمیز، آماده برای تزریق (Injection) توسط Mutagen برمی‌گرداند.
        """
        metadata = {
            'title': self._clean_string(raw_title) or raw_title,
            'artist': self._clean_string(raw_artist) or raw_artist,
            'cover_bytes': None,
            'lyrics': None
        }

        # ۱. استخراج دیتای مرجع از آیتونز (تنها در صورت تطابق قطعی نام و خواننده)
        itunes_data = self.fetch_itunes_data(metadata['artist'], metadata['title'])
        if itunes_data:
            metadata['title'] = itunes_data['title']
            metadata['artist'] = itunes_data['artist']
            metadata['cover_bytes'] = itunes_data['cover_bytes']

        # ۲. در صورتی که آهنگ در آیتونز نبود، کاور باکیفیت ویدیوی اصلی را لود کن
        if not metadata['cover_bytes'] and thumbnail_url:
            try:
                img_res = self.session.get(thumbnail_url, timeout=5)
                if img_res.status_code == 200:
                    metadata['cover_bytes'] = self._optimize_cover(img_res.content)
            except Exception as e:
                logger.warning(f"Thumbnail Cover Error: {e}")

        # ۳. استخراج لیریک با استفاده از نام‌های واقعی
        lyrics = self.fetch_lyrics(metadata['artist'], metadata['title'], duration)
        if lyrics:
            metadata['lyrics'] = lyrics

        return metadata

# ایجاد یک سینگلتون (Singleton) برای استفاده در کل برنامه
metadata_service = MetadataOrchestrator()