# core/services/youtube.py

import os
import shutil
import asyncio
import logging
import yt_dlp
from ytmusicapi import YTMusic
from core.config import Config

# ایمپورت‌های مربوط به Mutagen برای تزریق متادیتا در سطح باینری
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, TIT2, TPE1, error

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

    def search(self, query):
        try:
            return self.yt.search(query, filter="songs", limit=10)
        except Exception as e:
            logger.error(f"YT Search Error: {e}")
            return []

    def get_video_info(self, video_id):
        """
        دریافت مستقیم و دقیق مشخصات ویدیو/موزیک بدون سرچ اشتباه هش
        """
        try:
            song = self.yt.get_song(video_id)
            if song and 'videoDetails' in song:
                details = song['videoDetails']
                title = details.get('title', 'Unknown Track')
                author = details.get('author', 'Unknown Artist')
                return {'title': title, 'artist': author, 'videoId': video_id}
        except Exception as e:
            logger.warning(f"YT get_song info failed: {e}")

        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
            }
            if os.path.exists(Config.YT_COOKIES_PATH):
                ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                if info:
                    title = info.get('title', 'Unknown Track')
                    artist = info.get('artist') or info.get('uploader') or info.get('channel') or 'Unknown Artist'
                    if ' - ' in title and artist in ['Unknown Artist', info.get('uploader'), info.get('channel')]:
                        parts = title.split(' - ', 1)
                        artist = parts[0].strip()
                        title = parts[1].strip()
                    return {'title': title, 'artist': artist, 'videoId': video_id}
        except Exception as e:
            logger.error(f"yt_dlp get_video_info error: {e}")

        return {'title': 'YouTube Track', 'artist': 'Unknown Artist', 'videoId': video_id}

    def apply_metadata_to_file(self, file_path, metadata):
        """
        تزریق کاور، لیریک و مشخصات دقیق به هدر فایل MP3 با استفاده از ID3v2.
        این کار باعث می‌شود فایل در تمامی پلیرهای آفلاین هویت کامل داشته باشد.
        """
        if not metadata:
            return

        try:
            audio = MP3(file_path, ID3=ID3)
            
            # اگر فایل تگ ID3 نداشت، آن را بساز
            try:
                audio.add_tags()
            except error:
                pass  # تگ از قبل وجود دارد (توسط ffmpeg ساخته شده)

            # ۱. اصلاح نام آهنگ و خواننده (جایگزینی نام یوتیوب با نام رسمی آیتونز)
            if metadata.get('title'):
                audio.tags.add(TIT2(encoding=3, text=metadata['title']))
            if metadata.get('artist'):
                audio.tags.add(TPE1(encoding=3, text=metadata['artist']))

            # ۲. تزریق کاور با کیفیت (APIC)
            if metadata.get('cover_bytes'):
                audio.tags.add(
                    APIC(
                        encoding=3,  # UTF-8
                        mime='image/jpeg',
                        type=3,  # نوع 3 یعنی کاور جلوی آلبوم (Front Cover)
                        desc=u'Cover',
                        data=metadata['cover_bytes']
                    )
                )

            # ۳. تزریق متن لیریک (USLT)
            if metadata.get('lyrics'):
                audio.tags.add(
                    USLT(
                        encoding=3,  # UTF-8 برای پشتیبانی کامل از فارسی
                        lang=u'eng', # زبان (تثبیت‌شده روی eng یا und برای سازگاری بهتر)
                        desc=u'Lyrics',
                        text=metadata['lyrics']
                    )
                )

            audio.save()
            logger.info(f"[+] Metadata stitched successfully: {os.path.basename(file_path)}")
            
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

        # ۱. آماده‌سازی منابع دانلود (لینک مستقیم یوتیوب -> جستجوی ساندکلاد -> جستجوی یوتیوب)
        search_query = f"{metadata.get('artist', '')} {metadata.get('title', '')}".strip() if metadata else ""
        sources = [f"https://www.youtube.com/watch?v={video_id}"]
        if search_query and search_query != "Unknown Artist Unknown Track":
            sources.append(f"scsearch:{search_query}")
            sources.append(f"ytsearch:{search_query}")

        logger.info(f"[*] Starting Multi-Source Download for [{video_id}] | Quality: {target_quality}kbps")

        for source in sources:
            output_template = os.path.join(self.download_dir, f"{video_id}.%(ext)s")
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': True,
                'ignoreerrors': True,
                'nocheckcertificate': True,
                'geo_bypass': True,
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': target_quality,
                    },
                    {'key': 'FFmpegMetadata', 'add_metadata': True},
                ],
            }

            if self.ffmpeg_path and os.path.exists(self.ffmpeg_path):
                ydl_opts['ffmpeg_location'] = self.ffmpeg_path

            # لود کردن کوکی‌ها برای یوتیوب
            if os.path.exists(Config.YT_COOKIES_PATH) and not source.startswith('scsearch'):
                ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH

            try:
                def run_dl(src=source, opts=ydl_opts):
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        return ydl.extract_info(src, download=True)

                info = await asyncio.to_thread(run_dl)
                if info and os.path.exists(final_path):
                    logger.info(f"[+] Successfully downloaded via [{source}]: {final_path}")
                    if metadata:
                        self.apply_metadata_to_file(final_path, metadata)
                    return final_path
            except Exception as e:
                logger.warning(f"[-] Source failed [{source}]: {e}")
                continue

        logger.error(f"[-] All download sources exhausted for: {video_id}")
        return None

    def cleanup(self, file_path):
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except: pass