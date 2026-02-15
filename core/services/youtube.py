# core/services/youtube.py

import os
import shutil
import asyncio
import logging
import yt_dlp
from ytmusicapi import YTMusic
from core.config import Config

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

    async def download(self, video_id):
        link = f"https://music.youtube.com/watch?v={video_id}"
        output_template = os.path.join(self.download_dir, '%(id)s.%(ext)s')
        final_path = os.path.join(self.download_dir, f"{video_id}.mp3")

        logger.info(f"[*] Downloading: {link}")

        # اگر فایل از قبل بود، همان را برگردان
        if os.path.exists(final_path):
            logger.info(f"[+] Cached: {final_path}")
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
                {'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'},
                {'key': 'FFmpegMetadata','add_metadata': True},
            ],
        }

        # اعمال مسیر ffmpeg
        if self.ffmpeg_path and os.path.exists(self.ffmpeg_path):
            ydl_opts['ffmpeg_location'] = self.ffmpeg_path

        # 🔥 لود کردن کوکی از فایل (حیاتی برای رفع ارور Sign in)
        if os.path.exists(Config.YT_COOKIES_PATH):
            ydl_opts['cookiefile'] = Config.YT_COOKIES_PATH
            logger.info(f"🍪 Cookies loaded: {Config.YT_COOKIES_PATH}")
        else:
            logger.warning("⚠️ No cookies.txt found! YouTube might block this.")

        try:
            def run_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(link, download=True)
                    # 🔥 رفع باگ NoneType: اگر دانلود شکست خورد، None برگردان
                    if not info:
                        return None
                    return info.get('id')

            downloaded_id = await asyncio.to_thread(run_download)
            
            if downloaded_id and os.path.exists(final_path):
                logger.info(f"[+] Success: {final_path}")
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