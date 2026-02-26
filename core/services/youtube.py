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


    # 🔥 پارامتر metadata اضافه شد تا بعد از دانلود، تزریق انجام شود
    async def download(self, video_id, quality=None, metadata=None):
        link = f"https://music.youtube.com/watch?v={video_id}"
        output_template = os.path.join(self.download_dir, '%(id)s.%(ext)s')
        final_path = os.path.join(self.download_dir, f"{video_id}.mp3")

        # تعیین کیفیت نهایی: اگر به تابع پاس داده شده بود همان، وگرنه مقدار پیش‌فرض کانفیگ
        target_quality = str(quality) if quality else str(Config.AUDIO_QUALITY)
        
        logger.info(f"[*] Downloading: {link} | Quality: {target_quality}kbps")

        # اگر فایل از قبل بود، فقط متادیتا را دوباره چک/تزریق کن و برگردان
        if os.path.exists(final_path):
            logger.info(f"[+] Cached: {final_path}")
            if metadata:
                self.apply_metadata_to_file(final_path, metadata)
            return final_path

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True, # جلوگیری از کرش اگر فرمت نبود
            'nocheckcertificate': True,
            'geo_bypass': True,
            'postprocessors': [
                {'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                # تزریق متغیر کیفیت به موتور FFmpeg
                'preferredquality': target_quality, 
                },
                {'key': 'FFmpegMetadata','add_metadata': True},
            ],
        }

        # اعمال مسیر ffmpeg
        if self.ffmpeg_path and os.path.exists(self.ffmpeg_path):
            ydl_opts['ffmpeg_location'] = self.ffmpeg_path

        # لود کردن کوکی از فایل (حیاتی برای رفع ارور Sign in)
        if os.path.exists(Config.YT_COOKIES_PATH):
            ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
            logger.info(f"🍪 Cookies loaded: {Config.YT_COOKIES_PATH}")
        else:
            logger.warning("⚠️ No cookies.txt found! YouTube might block this.")

        try:
            def run_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    # رفع باگ NoneType: اگر دانلود شکست خورد، None برگردان
                    if not info:
                        return None
                    return info.get('id')

            downloaded_id = await asyncio.to_thread(run_download)
            
            # اگر دانلود موفق بود، متادیتای غنی (کاور + لیریک) را به آن می‌دوزیم
            if downloaded_id and os.path.exists(final_path):
                logger.info(f"[+] Success DL: {final_path}")
                if metadata:
                    self.apply_metadata_to_file(final_path, metadata)
                return final_path
            
            logger.error("[-] Download failed internally (Check cookies)")
            return None

        except Exception as e:
            logger.error(f"YT Critical Error: {e}")
            return None

    def cleanup(self, file_path):
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except: pass