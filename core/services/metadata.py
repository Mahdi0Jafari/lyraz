# core/services/metadata.py

import os
import re
import io
import urllib.parse
import logging
import requests
from difflib import SequenceMatcher
from PIL import Image
from core.config import Config

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

    def clean_artist(self, text):
        """حذف عباراتی نظیر - Topic و پرانتزهای اضافی و کاراکترهای فاصله نامتعارف از نام خواننده"""
        if not text:
            return ""
        artist = text.replace('\xa0', ' ').replace('\u200c', ' ')
        artist = re.sub(r'\s*-\s*Topic$', '', artist, flags=re.IGNORECASE)
        artist = re.sub(r'[\(\[].*?[\)\]]', '', artist).strip()
        return ' '.join(artist.split())

    def get_primary_artist(self, artist_text):
        """استخراج نام خواننده اصلی در ترک‌های چندخواننده‌ای (Collaborations / Feat)"""
        clean = self.clean_artist(artist_text)
        if not clean:
            return ""
        parts = re.split(r'[,/&]|\bfeat\.?\b|\bft\.?\b', clean, flags=re.IGNORECASE)
        if parts and parts[0].strip():
            return parts[0].strip()
        return clean

    def clean_title(self, text):
        """حذف عبارات اضافی مثل (Official Video) یا [Lyrics] برای جستجوی دقیق‌تر"""
        if not text:
            return ""
        patterns = [
            r'\s*[\(\[]\s*(official\s*(music\s*)?video|official\s*audio|lyric\s*video|audio|video|lyrics|visualizer|remastered|hq|hd|4k)\s*[\)\]]',
            r'\s*[\(\[]\s*(feat|ft|prod|prod\s*by)\.?\s+.*?[\)\]]',
            r'\s*[\(\[].*?[\)\]]'
        ]
        cleaned = text
        for p in patterns:
            cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def _similarity(self, a, b):
        """محاسبه شباهت دو رشته برای پیدا کردن دقیق‌ترین تطابق"""
        if not a or not b:
            return 0.0
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
        """
        استخراج شناسنامه رسمی آهنگ و کاور باکیفیت از اپل با بررسی دقیق شباهت.
        تنها در صورتی که خواننده و عنوان هر دو تطابق بالای معتبر داشته باشند استفاده می‌شود.
        """
        clean_art = self.clean_artist(artist)
        clean_tit = self.clean_title(title)
        
        if not clean_tit or clean_tit.lower() in ['unknown track', 'youtube track', 'video']:
            return None

        has_artist = bool(clean_art and clean_art.lower() not in ['unknown', 'unknown artist'])
        
        # اگر خواننده نامشخص است، هرگز از آیتونز بازنویسی نکن تا آهنگ‌های تصادفی با نام مشابه جایگزین نشوند
        if not has_artist:
            return None

        query_str = f"{clean_art} {clean_tit}"

        try:
            query = urllib.parse.quote(query_str)
            url = f"https://itunes.apple.com/search?term={query}&media=music&entity=song&limit=5"
            res = self.session.get(url, timeout=5).json()
            
            for track in res.get('results', []):
                t_name = track.get('trackName', '')
                a_name = track.get('artistName', '')
                
                t_sim = self._similarity(self.clean_title(t_name), clean_tit)
                a_sim = self._similarity(self.clean_artist(a_name), clean_art)
                
                # تطابق سخت‌گیرانه: نام آهنگ و خواننده هر دو باید با اطمینان بالا همخوانی داشته باشند
                is_match = (t_sim >= 0.75 and a_sim >= 0.70) or (t_sim >= 0.90 and a_sim >= 0.50)
                
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

    def fetch_lyrics_from_youtube(self, video_id):
        """استخراج هوشمند متن ترانه از توضیحات رسمی ویدیوی یوتیوب در صورت عدم وجود در دیتابیس‌های آنلاین (ویژه رپ و ایندی فارسی)"""
        if not video_id:
            return None
        try:
            import yt_dlp
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'socket_timeout': 5,
                'extractor_args': {
                    'youtube': {'player_client': ['android', 'ios', 'mweb', 'web']},
                    'youtubepot-bgutilhttp': {'base_url': ['http://Lyraz_pot:4416', 'http://pot:4416', 'http://172.17.0.1:4416', 'http://127.0.0.1:4416']}
                }
            }
            if os.path.exists(Config.YT_COOKIES_PATH):
                ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                desc = info.get('description', '')
                if not desc:
                    return None
                
                desc_lower = desc.lower()
                # رد متادیتاهای ماشینی یوتیوب (Auto-generated Topic releases)
                if any(k in desc_lower for k in [
                    'provided to youtube by', 'auto-generated by youtube',
                    'released on:', '℗', 'arranger:', 'composer:'
                ]):
                    return None

                lines = desc.split('\n')
                cleaned = []
                for line in lines:
                    l = line.strip()
                    if not l or l == '.':
                        continue
                    lower_l = l.lower()
                    if any(h in lower_l for h in [
                        'producer', 'beat', 'mix &', 'mastering', 'directed', 'director',
                        'video by', 'artwork', 'cover art', 'label:', 'subscribe', 'follow',
                        'stream', 'listen', 'track of', 'album', 'lyricist', 'arranged'
                    ]):
                        continue
                    if re.search(r'https?://|t\.me|instagram\.com|youtube\.com', l):
                        continue
                    if l.startswith('#'):
                        continue
                    cleaned.append(l)

                if len(cleaned) >= 6:
                    persian_count = len(re.findall(r'[\u0600-\u06FF]', '\n'.join(cleaned)))
                    if persian_count > 50 or len(cleaned) >= 10:
                        logger.info(f"✨ Successfully extracted lyrics from YouTube description [{video_id}]")
                        return '\n'.join(cleaned)
        except Exception as e:
            logger.debug(f"YouTube description lyrics extract error [{video_id}]: {e}")
        return None

    def fetch_lyrics(self, artist, title, duration=None, video_id=None):
        """
        جستجوی هوشمند و چندمرحله‌ای متن هماهنگ‌شده (Synced LRC) از LRCLIB.
        پشتیبانی کامل از ترک‌های چندخواننده‌ای، اولویت قطعی با متن زمان‌بندی‌شده، و فال‌بک رسمی.
        """
        search_artist = self.clean_artist(artist)
        primary_artist = self.get_primary_artist(artist)
        search_title = self.clean_title(title)
        
        if not search_title:
            return None

        # مرحله ۱: درخواست مستقیم به اندپوینت اختصاصی /api/get (دقیق‌ترین و سریع‌ترین روش)
        if primary_artist:
            try:
                get_params = {'track_name': search_title, 'artist_name': primary_artist}
                if duration:
                    get_params['duration'] = int(duration)
                res_get = self.session.get("https://lrclib.net/api/get", params=get_params, timeout=5)
                if res_get.status_code == 200:
                    d = res_get.json()
                    if d.get('syncedLyrics') or d.get('plainLyrics'):
                        logger.info(f"✨ Exact LRCLIB match via /api/get for '{search_title}' by '{primary_artist}'")
                        return d.get('syncedLyrics') or d.get('plainLyrics')
            except Exception as e:
                logger.debug(f"LRCLIB /api/get error: {e}")

        # مرحله ۲: جستجوی نامزدها (Candidates) از طریق اندپوینت /api/search با استراتژی چندمرحله‌ای
        candidates = []
        seen_cand_ids = set()

        search_attempts = []
        if primary_artist:
            search_attempts.append({'track_name': search_title, 'artist_name': primary_artist})
            search_attempts.append({'q': f"{primary_artist} {search_title}"})
        if search_artist and search_artist != primary_artist:
            search_attempts.append({'q': f"{search_artist} {search_title}"})
        if len(search_title) > 2:
            search_attempts.append({'q': search_title})

        for params in search_attempts:
            try:
                res = self.session.get("https://lrclib.net/api/search", params=params, timeout=5)
                if res.status_code == 200:
                    results = res.json()
                    if results:
                        for r in results:
                            cid = r.get('id')
                            if cid and cid not in seen_cand_ids:
                                seen_cand_ids.add(cid)
                                candidates.append(r)
                        if len(candidates) >= 5:
                            break
            except Exception as e:
                logger.debug(f"LRCLIB search attempt error: {e}")
                continue

        # مرحله ۳: امتیازدهی دقیق به نامزدها با اولویت بسیار بالا به لیریک زمان‌بندی‌شده (Synced LRC)
        best_match = None
        highest_score = 0.0
        clean_target_art = primary_artist.lower() if primary_artist else search_artist.lower()
        art_words = [w for w in clean_target_art.split() if len(w) > 2]

        for cand in candidates:
            synced = cand.get('syncedLyrics')
            plain = cand.get('plainLyrics')
            if not synced and not plain:
                continue
            
            cand_dur = cand.get('duration')
            time_diff = abs(int(cand_dur) - int(duration)) if (cand_dur is not None and duration is not None) else 0
            if duration and cand_dur is not None and time_diff > 14:
                continue

            cand_tit_clean = self.clean_title(cand.get('trackName', '')).lower()
            cand_art_clean = self.clean_artist(cand.get('artistName', '')).lower()

            t_sim = self._similarity(cand_tit_clean, search_title.lower())
            if t_sim < 0.55:
                continue

            a_sim = 0.5
            if clean_target_art:
                a_sim = self._similarity(cand_art_clean, clean_target_art)
                word_match = any(w in cand_art_clean for w in art_words)
                if not word_match and a_sim < 0.40 and clean_target_art not in cand_art_clean:
                    continue  # رد خواننده نامربوط
                if word_match or clean_target_art in cand_art_clean:
                    a_sim = max(a_sim, 0.85)

            score = (t_sim * 3.0) + (a_sim * 2.0)
            if time_diff <= 3:
                score += 2.0
            elif time_diff <= 6:
                score += 1.0

            # 🔥 امتیاز ویژه برای لیریک زمان‌بندی‌شده (Synced LRC)
            if synced:
                score += 3.0

            if score > highest_score:
                highest_score = score
                best_match = cand

        if best_match and highest_score >= 3.0:
            logger.info(f"✨ Matched LRCLIB Lyrics for '{title}' (Score: {highest_score:.2f}, Synced: {bool(best_match.get('syncedLyrics'))})")
            return best_match.get('syncedLyrics') or best_match.get('plainLyrics')

        # مرحله ۴: فال‌بک رسمی به استخراج لیریک از دیسکریپشن یوتیوب
        if video_id:
            yt_lyrics = self.fetch_lyrics_from_youtube(video_id)
            if yt_lyrics:
                return yt_lyrics

        return None

    def get_full_metadata(self, raw_artist, raw_title, duration=None, thumbnail_url=None, video_id=None):
        """
        نقطه ورود اصلی برای دریافت پکیج کامل اطلاعات آهنگ.
        یک دیکشنری تمیز، آماده برای تزریق (Injection) توسط Mutagen برمی‌گرداند.
        """
        cleaned_artist = self.clean_artist(raw_artist) or raw_artist or 'Unknown Artist'
        cleaned_title = raw_title or 'Unknown Track'

        metadata = {
            'title': cleaned_title,
            'artist': cleaned_artist,
            'cover_bytes': None,
            'lyrics': None
        }

        # ۱. استخراج دیتای مرجع از آیتونز (تنها در صورت تطابق قطعی نام و خواننده)
        itunes_data = self.fetch_itunes_data(cleaned_artist, cleaned_title)
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

        # ۳. استخراج لیریک با اعتبارسنجی دقیق و فال‌بک دیسکریپشن یوتیوب
        lyrics = self.fetch_lyrics(metadata['artist'], metadata['title'], duration, video_id=video_id)
        if lyrics:
            metadata['lyrics'] = lyrics

        return metadata

# ایجاد یک سینگلتون (Singleton) برای استفاده در کل برنامه
metadata_service = MetadataOrchestrator()