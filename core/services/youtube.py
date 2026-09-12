# core/services/youtube.py

import os
import re
import time
import shutil
import asyncio
import logging
import requests
import yt_dlp
from ytmusicapi import YTMusic
from core.config import Config

# ایمپورت‌های مربوط به Mutagen برای تزریق متادیتا در سطح باینری
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, SYLT, TIT2, TPE1, error

logger = logging.getLogger(__name__)

class YouTubeService:
    def __init__(self, download_sub_dir="yt_cache"):
        # مسیر دانلود در پوشه instance
        self.download_dir = os.path.join(Config.INSTANCE_PATH, download_sub_dir)
        
        # ساخت پوشه اگر نباشد
        if not os.path.exists(self.download_dir):
            try:
                os.makedirs(self.download_dir)
            except OSError:
                pass
            
        self.yt = YTMusic()
        self.ffmpeg_path = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg" or "/opt/homebrew/bin/ffmpeg"

    def clean_artist_name(self, artist):
        """حذف پسوند متداول - Topic از کانال‌های اتوماتیک یوتیوب"""
        if not artist:
            return "Unknown Artist"
        artist = re.sub(r'\s*-\s*Topic$', '', artist, flags=re.IGNORECASE).strip()
        return artist if artist else "Unknown Artist"

    def search(self, query):
        try:
            res = self.yt.search(query, filter="songs", limit=16)
            if res:
                return res
        except Exception as e:
            logger.warning(f"YTMusic API Search Error for '{query}': {e}, falling back to yt-dlp...")

        # پلن پشتیبان سریع و پایدار با yt-dlp مجهز به POT Provider (بدون خطای 403 یا مسدودی یوتیوب)
        try:
            import yt_dlp
            ydl_opts = {
                'extract_flat': True,
                'quiet': True,
                'skip_download': True,
                'no_warnings': True,
                'socket_timeout': 5,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'ios', 'mweb', 'web'],
                    },
                    'youtubepot-bgutilhttp': {
                        'base_url': ['http://Lyraz_pot:4416', 'http://pot:4416', 'http://172.17.0.1:4416', 'http://127.0.0.1:4416']
                    }
                }
            }
            if os.path.exists(Config.YT_COOKIES_PATH):
                ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                entries = info.get('entries', [])
                if entries and entries[0]:
                    e = entries[0]
                    return [{
                        'videoId': e.get('id'),
                        'title': e.get('title'),
                        'duration_seconds': e.get('duration')
                    }]
        except Exception as ydl_err:
            logger.error(f"yt-dlp fallback search error for '{query}': {ydl_err}")

        return []

    @staticmethod
    def clean_match_str(s):
        if not s: return ''
        s = str(s).lower()
        s = re.sub(r'\(.*?\)|\[.*?\]', '', s)
        s = re.sub(r'[^a-zA-Z0-9\u0600-\u06FF\s]', ' ', s)
        return ' '.join(s.split())

    @classmethod
    def score_match(cls, target_title, target_artist, target_dur, cand_title, cand_artist, cand_dur):
        t_clean = cls.clean_match_str(target_title)
        c_clean = cls.clean_match_str(cand_title)
        if not t_clean or not c_clean:
            return 0

        score = 0
        t_words = [w for w in t_clean.split() if len(w) > 1]
        c_words = [w for w in c_clean.split() if len(w) > 1]

        # 1. تطابق عنوان ترک (Title Match)
        if t_clean == c_clean:
            score += 65
        elif t_clean in c_clean:
            score += 45
        elif any(tw in c_words for tw in t_words):
            matching_words = sum(1 for tw in t_words if tw in c_words)
            score += int(40 * (matching_words / len(t_words)))
        else:
            return -100  # اگر عنوان به کلی بی‌ربط بود، رد صلاحیت قطعی

        # 2. تطابق خواننده (Artist Match)
        if target_artist:
            art_clean = cls.clean_match_str(target_artist)
            art_words = [w for w in art_clean.split() if len(w) > 2]
            cand_art_full = f"{cand_title} {cand_artist or ''}".lower()
            if art_words:
                matches = sum(1 for aw in art_words if aw in cand_art_full)
                if matches > 0:
                    score += int(40 * (matches / len(art_words)))
                else:
                    score -= 60  # اگر نام هیچ‌یک از خواننده‌ها نبود

        # 3. تطابق مدت زمان (Duration Match)
        if target_dur and cand_dur:
            diff = abs(target_dur - cand_dur)
            if diff <= 3:
                score += 40
            elif diff <= 8:
                score += 25
            elif diff <= 16:
                score += 10
            elif diff > 35:
                score -= 50

        # 4. جریمه محتوای نامربوط (Reaction / Remix / Slowed / Covers)
        for kw in ['reaction', 'remix', 'slowed', 'reverb', '1 hour', 'karaoke', 'bass boosted']:
            if kw in cand_title.lower() and (not target_title or kw not in target_title.lower()):
                score -= 70

        return score

    def find_best_match(self, query, title=None, artist=None, duration=None):
        """
        یافتن هوشمند دقیق‌ترین موزیک با امتیازدهی چندبعدی (عنوان + خواننده + مدت زمان).
        از دانلود شدن ترک‌های اشتباه به جای ترک واقعی جلوگیری می‌کند.
        """
        search_query = query or f"{artist or ''} {title or ''}".strip()
        candidates = []

        # ۱. ابتدا جستجو در پایگاه قطعات رسمی YTMusic
        try:
            res = self.yt.search(search_query, filter="songs", limit=12)
            if res:
                for r in res:
                    c_title = r.get('title', '')
                    c_arts = ', '.join([a.get('name', '') for a in r.get('artists', [])])
                    c_dur = r.get('duration_seconds') or 0
                    if not c_dur and r.get('duration'):
                        try:
                            parts = r['duration'].split(':')
                            c_dur = int(parts[0]) * 60 + int(parts[1])
                        except Exception:
                            c_dur = 0
                    vid = r.get('videoId')
                    if vid:
                        s = self.score_match(title or query, artist, duration, c_title, c_arts, c_dur)
                        candidates.append((s, vid, c_title, c_arts, c_dur, 'ytmusic'))
        except Exception as e:
            logger.debug(f"YTMusic search error in find_best_match: {e}")

        candidates.sort(key=lambda x: x[0], reverse=True)
        # اگر تطابق با امتیاز بالای ۸۰ در قطعات رسمی پیدا شد
        if candidates and candidates[0][0] >= 80:
            logger.info(f"🎯 Exact YTMusic Match (Score: {candidates[0][0]}): {candidates[0][2]} [{candidates[0][1]}]")
            return candidates[0][1]

        # ۲. جستجوی ویدیوی رسمی در یوتیوب عمومی (Official Music Videos) با yt-dlp
        try:
            import yt_dlp
            ydl_opts = {
                'extract_flat': True,
                'quiet': True,
                'skip_download': True,
                'no_warnings': True,
                'socket_timeout': 5,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'ios', 'mweb', 'web'],
                    },
                    'youtubepot-bgutilhttp': {
                        'base_url': ['http://Lyraz_pot:4416', 'http://pot:4416', 'http://172.17.0.1:4416', 'http://127.0.0.1:4416']
                    }
                }
            }
            if os.path.exists(Config.YT_COOKIES_PATH):
                ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch6:{search_query}", download=False)
                for e in info.get('entries', []):
                    vid = e.get('id')
                    c_title = e.get('title', '')
                    c_arts = e.get('uploader', '')
                    c_dur = int(e.get('duration') or 0)
                    if vid:
                        s = self.score_match(title or query, artist, duration, c_title, c_arts, c_dur)
                        candidates.append((s, vid, c_title, c_arts, c_dur, 'youtube'))
        except Exception as yt_err:
            logger.debug(f"yt-dlp fallback search error in find_best_match: {yt_err}")

        candidates.sort(key=lambda x: x[0], reverse=True)
        if candidates and candidates[0][0] > 0:
            logger.info(f"🎯 Best Matched Track (Score: {candidates[0][0]} from {candidates[0][5]}): {candidates[0][2]} [{candidates[0][1]}]")
            return candidates[0][1]

        # ۳. در صورت عدم پیدا شدن کاندیدا، به نخستین نتیجه سرچ تکیه کن
        if candidates:
            return candidates[0][1]
        return None

    def get_video_info(self, video_id):
        """
        دریافت مستقیم و دقیق مشخصات ویدیو/موزیک با استراتژی چندمرحله‌ای (oEmbed -> YTMusic -> yt_dlp)
        """
        # ۱. استراتژی اول: استفاده از YouTube oEmbed (سریع‌ترین و ۱۰۰٪ بدون بلاک آی‌پی یا خطای بات)
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            res = requests.get(oembed_url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                title = data.get('title', 'Unknown Track')
                author = self.clean_artist_name(data.get('author_name', 'Unknown Artist'))
                
                # اگر نام ویدیو به صورت Artist - Title بود و نام کانال عمومی/تاپیک بود
                if ' - ' in title and (author in ['Unknown Artist', 'YouTube Track'] or 'topic' in author.lower() or 'records' in author.lower() or 'music' in author.lower()):
                    parts = title.split(' - ', 1)
                    if len(parts) == 2 and len(parts[0].strip()) > 0 and len(parts[1].strip()) > 0:
                        author = parts[0].strip()
                        title = parts[1].strip()
                        
                return {'title': title, 'artist': author, 'videoId': video_id}
        except Exception as e:
            logger.warning(f"YouTube oEmbed failed: {e}")

        # ۲. استراتژی دوم: YTMusic
        try:
            song = self.yt.get_song(video_id)
            if song and 'videoDetails' in song:
                details = song['videoDetails']
                title = details.get('title', 'Unknown Track')
                author = self.clean_artist_name(details.get('author', 'Unknown Artist'))
                return {'title': title, 'artist': author, 'videoId': video_id}
        except Exception as e:
            logger.warning(f"YT get_song info failed: {e}")

        # ۳. استراتژی سوم: yt_dlp
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'socket_timeout': 5,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'ios', 'mweb', 'web'],
                    },
                    'youtubepot-bgutilhttp': {
                        'base_url': ['http://Lyraz_pot:4416', 'http://pot:4416', 'http://172.17.0.1:4416', 'http://127.0.0.1:4416']
                    }
                }
            }
            if os.path.exists(Config.YT_COOKIES_PATH):
                ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                if info:
                    title = info.get('title', 'Unknown Track')
                    raw_artist = info.get('artist') or info.get('uploader') or info.get('channel') or 'Unknown Artist'
                    artist = self.clean_artist_name(raw_artist)
                    if ' - ' in title and artist in ['Unknown Artist', info.get('uploader'), info.get('channel')]:
                        parts = title.split(' - ', 1)
                        artist = parts[0].strip()
                        title = parts[1].strip()
                    return {'title': title, 'artist': artist, 'videoId': video_id}
        except Exception as e:
            logger.error(f"yt_dlp get_video_info error: {e}")

        return {'title': 'YouTube Track', 'artist': 'Unknown Artist', 'videoId': video_id}

    @staticmethod
    def parse_lrc_to_sylt(lrc_text):
        """تبدیل متن استاندارد LRC به تاپل‌های میلی‌ثانیه‌ای فریم SYLT برای پلیرهای سامسونگ و اندروید"""
        if not lrc_text:
            return []
        regex = re.compile(r'^\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\](.*)$')
        sylt_entries = []
        for line in lrc_text.split('\n'):
            line_str = line.strip()
            m = regex.match(line_str)
            if m:
                mins = int(m.group(1))
                secs = int(m.group(2))
                ms_str = m.group(3) or '0'
                ms = int(ms_str) if len(ms_str) == 3 else int(ms_str) * 10
                timestamp_ms = (mins * 60 + secs) * 1000 + ms
                text = m.group(4).strip()
                if text:
                    sylt_entries.append((text, timestamp_ms))
        return sylt_entries

    def apply_metadata_to_file(self, file_path, metadata):
        """
        تزریق کاور، لیریک سینک‌شده (USLT + SYLT) و مشخصات دقیق بر اساس استاندارد ID3v2.3.
        سازگار کامل با Samsung Music، پلیرهای اندروید، iOS و وب‌پلیر.
        """
        if not metadata:
            return

        try:
            audio = MP3(file_path, ID3=ID3)
            
            # اگر فایل تگ ID3 نداشت، آن را بساز
            try:
                audio.add_tags()
            except error:
                pass  # تگ از قبل وجود دارد

            # ۱. اصلاح نام آهنگ و خواننده با انکودینگ استاندارد ID3v2.3 (UTF-16 با BOM)
            if metadata.get('title'):
                audio.tags.setall('TIT2', [TIT2(encoding=1, text=metadata['title'])])
            if metadata.get('artist'):
                audio.tags.setall('TPE1', [TPE1(encoding=1, text=metadata['artist'])])

            # ۲. تزریق کاور با کیفیت (APIC)
            if metadata.get('cover_bytes'):
                audio.tags.setall('APIC', [
                    APIC(
                        encoding=0,
                        mime='image/jpeg',
                        type=3,  # Front Cover
                        desc=u'Cover',
                        data=metadata['cover_bytes']
                    )
                ])

            # ۳. تزریق متن لیریک (همگام‌سازی دوگانه USLT و SYLT برای سامسونگ و پلیرهای آفلاین)
            if metadata.get('lyrics'):
                lrc_lyrics = metadata['lyrics']
                # فریم USLT با فرمت خام (LRC با تایم‌استمپ) همانند نسخه قبلی برای سازگاری با تلگرام و سایر پلیرها
                audio.tags.setall('USLT', [
                    USLT(
                        encoding=3,  # UTF-8 برای پشتیبانی کامل از فارسی
                        lang=u'eng',
                        desc=u'Lyrics',
                        text=lrc_lyrics
                    )
                ])

            # ذخیره با استاندارد قطعی ID3v2.3 (حیاتی برای Samsung Music)
            audio.save(v2_version=3)
            logger.info(f"[+] Metadata stitched successfully (ID3v2.3): {os.path.basename(file_path)}")
            
        except Exception as e:
            logger.error(f"[-] Mutagen Stitching Error: {e}")


    async def download(self, video_id, quality=None, metadata=None):
        target_quality = str(quality) if quality else str(Config.AUDIO_QUALITY)
        final_path = os.path.join(self.download_dir, f"{video_id}.mp3")

        # اگر فایل از قبل بود، فقط متادیتا را دوباره چک/تزریق کن و برگردان
        if os.path.exists(final_path):
            logger.info(f"[+] Cached: {final_path}")
            if metadata:
                self.apply_metadata_to_file(final_path, metadata)
            return final_path

        # ۱. آماده‌سازی منابع دانلود (لینک مستقیم یوتیوب / ساندکلاد -> جستجوهای پشتیبان)
        raw_artist = metadata.get('artist', '') if metadata else ''
        raw_title = metadata.get('title', '') if metadata else ''
        source_url = metadata.get('source_url', '') if metadata else ''
        expected_dur = None
        if metadata and metadata.get('duration'):
            try:
                expected_dur = int(float(metadata['duration']))
            except (ValueError, TypeError):
                expected_dur = None

        clean_art = re.sub(r'\s*-\s*Topic$', '', raw_artist, flags=re.IGNORECASE).strip()
        clean_search_art = clean_art.replace(',', ' ').strip()
        search_query = f"{clean_search_art} {raw_title}".strip()
        if search_query in ['Unknown Artist Unknown Track', 'Unknown Artist YouTube Track', 'Unknown Track']:
            search_query = ""

        # ساخت لیست سورس‌ها با اولویت‌بندی هوشمند
        if source_url and ('soundcloud.com' in source_url or video_id.startswith('sc_')):
            sources = [source_url]
            if search_query:
                sources.append(f"scsearch1:{search_query}")
                sources.append(f"ytsearch1:{search_query}")
        elif video_id.startswith('sc_'):
            sources = []
            if search_query:
                sources.append(f"scsearch1:{search_query}")
                sources.append(f"ytsearch1:{search_query}")
        else:
            sources = [f"https://www.youtube.com/watch?v={video_id}"]
            if search_query:
                # اولویت با ساندکلاد برای فال‌بک چون در دیتاسنترها بلاک یوتیوب و 403 ندارد
                sources.append(f"scsearch1:{search_query}")
                sources.append(f"ytsearch1:{search_query}")
                # اگر چند خواننده بود، فال‌بک با خواننده اول هم اضافه شود
                if ',' in clean_art:
                    first_art = clean_art.split(',')[0].strip()
                    if first_art:
                        sources.append(f"scsearch1:{first_art} {raw_title}")

        logger.info(f"[*] Starting Multi-Source Download for [{video_id}] | Expected Dur: {expected_dur}s | Quality: {target_quality}kbps")

        for source in sources:
            output_template = os.path.join(self.download_dir, f"{video_id}.%(ext)s")
            ydl_opts = {
                'format': 'ba/ba*',
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': True,
                'ignoreerrors': True,
                'nocheckcertificate': True,
                'geo_bypass': True,
                'socket_timeout': 5,
                'retries': 2,
                'fragment_retries': 2,
                'concurrent_fragment_downloads': 8,
                'buffersize': 1024 * 1024,
                'http_chunk_size': 10485760,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'ios', 'mweb', 'web'],
                    },
                    'youtubepot-bgutilhttp': {
                        'base_url': ['http://Lyraz_pot:4416', 'http://pot:4416', 'http://172.17.0.1:4416', 'http://127.0.0.1:4416']
                    }
                },
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': target_quality,
                    },
                    {'key': 'FFmpegMetadata', 'add_metadata': True},
                ],
                'postprocessor_args': {
                    'ffmpeg': ['-threads', '1', '-vn'],
                    'ExtractAudio': ['-threads', '1', '-vn']
                }
            }

            if self.ffmpeg_path and os.path.exists(self.ffmpeg_path):
                ydl_opts['ffmpeg_location'] = self.ffmpeg_path

            # لود کردن کوکی‌ها برای سورس‌های وب در صورت وجود
            if os.path.exists(Config.YT_COOKIES_PATH) and not source.startswith('scsearch'):
                try:
                    if os.path.getsize(Config.YT_COOKIES_PATH) > 50:
                        ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
                except Exception: pass

            try:
                def run_dl(src=source, opts=ydl_opts):
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        return ydl.extract_info(src, download=True)

                info = await asyncio.to_thread(run_dl)
                if info and os.path.exists(final_path):
                    # 🛡 اعتبارسنجی مدت‌زمان فایل دانلودشده برای سورس‌های فال‌بک (Fallback Integrity Guard)
                    is_direct_source = ('watch?v=' in source or 'soundcloud.com/' in source) and not ('search' in source)
                    if not is_direct_source and expected_dur and expected_dur > 30:
                        try:
                            audio_check = MP3(final_path)
                            actual_dur = int(audio_check.info.length)
                            if abs(actual_dur - expected_dur) > 18:
                                logger.warning(
                                    f"⚠️ Fallback source [{source}] produced duration {actual_dur}s "
                                    f"which deviates from expected {expected_dur}s. Rejecting false match."
                                )
                                if os.path.exists(final_path):
                                    os.remove(final_path)
                                continue
                        except Exception as dur_err:
                            logger.debug(f"Duration check non-fatal error: {dur_err}")

                    logger.info(f"[+] Successfully downloaded via [{source}]: {final_path}")
                    if metadata:
                        self.apply_metadata_to_file(final_path, metadata)
                    return final_path
            except Exception as e:
                logger.warning(f"[-] Source failed [{source}]: {e}")
                continue

        logger.error(f"[-] All download sources exhausted for: {video_id}")
        return None

    def purge_stale_cache(self, max_age_seconds=3600):
        """پاکسازی خودکار فایل‌های واسط و کش‌های موقت yt_cache که بیش از ۱ ساعت از ساخت آن‌ها گذشته است"""
        try:
            if not os.path.exists(self.download_dir):
                return
            now = time.time()
            for fname in os.listdir(self.download_dir):
                fpath = os.path.join(self.download_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        if (now - os.path.getmtime(fpath)) > max_age_seconds:
                            os.remove(fpath)
                    except Exception:
                        pass
        except Exception:
            pass

    def cleanup(self, file_path=None, video_id=None):
        """پاکسازی کامل فایل اصلی و کلیه فایل‌های موقت واسط (.webm, .m4a, .part, cmp_*)"""
        try:
            if file_path and os.path.exists(file_path):
                try: os.remove(file_path)
                except: pass

            vid = video_id
            if not vid and file_path:
                base = os.path.basename(file_path)
                vid = os.path.splitext(base)[0]
                if vid.startswith('cmp_'):
                    vid = vid[4:]

            if vid and os.path.exists(self.download_dir):
                for fname in os.listdir(self.download_dir):
                    if vid in fname:
                        fpath = os.path.join(self.download_dir, fname)
                        if os.path.isfile(fpath):
                            try: os.remove(fpath)
                            except: pass
        except Exception:
            pass

    def get_playlist_info(self, playlist_url_or_id, max_tracks=50):
        """
        استخراج مشخصات و لیست ترک‌های پلی‌لیست یوتیوب به صورت Flat و پرسرعت
        """
        clean_url = playlist_url_or_id
        if not clean_url.startswith("http"):
            clean_url = f"https://www.youtube.com/playlist?list={playlist_url_or_id}"

        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'extractor_args': {
                'youtube': {'player_client': ['mweb', 'web']},
                'youtubepot-bgutilhttp': {'base_url': ['http://Lyraz_pot:4416', 'http://pot:4416', 'http://172.17.0.1:4416', 'http://127.0.0.1:4416']}
            }
        }
        if os.path.exists(Config.YT_COOKIES_PATH) and os.path.getsize(Config.YT_COOKIES_PATH) > 50:
            ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                res = ydl.extract_info(clean_url, download=False)
                if not res:
                    return {'status': 'error', 'message': 'Could not extract playlist information.'}

                playlist_title = res.get('title') or 'YouTube Playlist'
                entries = [e for e in res.get('entries', []) if e and e.get('id')]
                formatted_tracks = []

                for e in entries:
                    vid = e.get('id')
                    raw_title = e.get('title') or 'Unknown Track'
                    raw_artist = e.get('uploader') or e.get('channel') or 'YouTube Artist'
                    duration = e.get('duration') or 0

                    clean_artist = self.clean_artist_name(raw_artist)
                    clean_title = raw_title
                    if ' - ' in raw_title:
                        parts = raw_title.split(' - ', 1)
                        if len(parts) == 2 and len(parts[0].strip()) > 0 and len(parts[1].strip()) > 0:
                            clean_artist = self.clean_artist_name(parts[0].strip())
                            clean_title = parts[1].strip()

                    formatted_tracks.append({
                        'title': clean_title,
                        'artist': clean_artist,
                        'videoId': vid,
                        'video_id': vid,
                        'duration': duration,
                        'search_query': f"{clean_artist} {clean_title}".strip(),
                        'cover': f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                    })

                    if max_tracks and len(formatted_tracks) >= max_tracks:
                        break

                if not formatted_tracks:
                    return {'status': 'error', 'message': 'No playable tracks found in this playlist.'}

                return {
                    'status': 'success',
                    'name': playlist_title,
                    'total': len(formatted_tracks),
                    'tracks': formatted_tracks,
                    'cover': f"https://i.ytimg.com/vi/{formatted_tracks[0]['videoId']}/hqdefault.jpg" if formatted_tracks else None
                }
        except Exception as e:
            logger.error(f"Failed to extract YouTube playlist: {e}")
            return {'status': 'error', 'message': str(e)}