# core/routes/stream.py


from flask import Blueprint, Response, request, redirect, stream_with_context, jsonify
from core.models import get_db
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import Timeout, RequestException
from urllib3.util.retry import Retry
import urllib.parse
from core.config import Config
import logging
import time
import re
from difflib import SequenceMatcher

stream_bp = Blueprint('stream', __name__)
logger = logging.getLogger(__name__)

# --- 1. Network Hardening (High Performance Config) ---
LINK_CACHE = {}
METADATA_CACHE = {}
CACHE_DURATION = 3600  # لینک‌های تلگرام تا ۱ ساعت معتبر هستند

# تنظیمات اتصال تهاجمی (Aggressive Connection Strategy)
# هدف: اگر پکت لاس داشتیم، سریع دوباره تلاش کن، معطل نکن.
retry_strategy = Retry(
    total=3,
    backoff_factor=0.2, 
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=50, pool_maxsize=50)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)


# --- 2. The Metadata Oracle (iTunes Integration) ---

def fetch_itunes_metadata(artist, title):
    """
    نام آرتیست و آهنگ را استاندارد می‌کند (مثلاً 'dang show' -> 'Dang Show')
    """
    cache_key = f"{artist}|{title}"
    if cache_key in METADATA_CACHE:
        return METADATA_CACHE[cache_key]

    try:
        clean_q = f"{artist.split(',')[0]} {title.split('(')[0]}"
        query = urllib.parse.quote(clean_q)
        url = f"https://itunes.apple.com/search?term={query}&media=music&entity=song&limit=1"
        
        # تایم‌اوت کوتاه (۳ ثانیه) چون نمی‌خواهیم UI برای کاور قفل شود
        res = http_session.get(url, timeout=3.0).json()

        if res.get('resultCount', 0) > 0:
            track = res['results'][0]
            data = {
                'artist': track['artistName'],
                'title': track['trackName'],
                'cover': track['artworkUrl100'].replace('100x100bb', '600x600bb')
            }
            METADATA_CACHE[cache_key] = data
            return data
    except Exception as e:
        logger.warning(f"Oracle warning: {e}")

    return None

def similarity(a, b):
    if not a or not b: return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def get_tg_link(file_id):
    """
    دریافت لینک مستقیم دانلود از تلگرام با استفاده از کش حافظه
    """
    current_time = time.time()
    
    # 1. بررسی کش (سریع‌ترین حالت ممکن)
    if file_id in LINK_CACHE:
        cached = LINK_CACHE[file_id]
        if current_time < cached['expire']: 
            return cached['url']
        else: 
            del LINK_CACHE[file_id]

    # 2. دریافت از تلگرام (کندترین حالت - حدود ۰.۵ تا ۱ ثانیه)
    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/getFile?file_id={file_id}"
    try:
        res = http_session.get(url, timeout=4.0).json()
        if res.get('ok'):
            file_path = res['result']['file_path']
            download_url = f"https://api.telegram.org/file/bot{Config.BOT_TOKEN}/{file_path}"
            
            # ذخیره در کش برای استفاده‌های بعدی (Pre-fetching نتیجه‌اش اینجا ذخیره می‌شود)
            LINK_CACHE[file_id] = {'url': download_url, 'expire': current_time + CACHE_DURATION}
            return download_url
    except Exception as e:
        logger.error(f"Telegram API Error: {e}")
        pass
    return None

# --- Cache Database Helpers ---
def get_cached_lyrics(unique_id):
    try:
        db = get_db()
        return db.execute("SELECT lyrics FROM lyrics_cache WHERE file_unique_id=?", (unique_id,)).fetchone()
    except: return None

def save_lyrics_to_cache(unique_id, lyrics, source):
    try:
        db = get_db()
        db.execute("INSERT OR REPLACE INTO lyrics_cache (file_unique_id, lyrics, source, updated_at) VALUES (?, ?, ?, ?)",
                   (unique_id, lyrics, source, int(time.time())))
        db.commit()
    except: pass


# --- 3. Optimized Routes ---

@stream_bp.route('/stream/warmup/<unique_id>')
def warmup_link(unique_id):
    """
    🔥 Pre-fetch Endpoint:
    فرانت‌اند باید این آدرس را برای آهنگ بعدی صدا بزند.
    این تابع لینک را می‌گیرد و در RAM ذخیره می‌کند.
    """
    try:
        db = get_db()
        track = db.execute("SELECT file_id FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
        
        if track:
            # فراخوانی get_tg_link باعث پر شدن کش می‌شود
            link = get_tg_link(track['file_id'])
            if link:
                return jsonify({"status": "warmed", "unique_id": unique_id})
                
        return jsonify({"status": "failed"}), 404
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@stream_bp.route('/stream/<unique_id>')
def audio(unique_id):
    """
    🚀 Real-time Streaming Endpoint
    """
    db = get_db()
    track = db.execute("SELECT file_id, file_size FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()

    if not track: return "Track Not Found", 404
    
    # 1. دریافت لینک (اگر وارم‌آپ شده باشد، زیر ۱ میلی‌ثانیه طول می‌کشد)
    link = get_tg_link(track['file_id'])
    if not link: return "Link Error", 500

    # 2. انتقال هدر Range برای Seek کردن
    headers = {}
    if 'Range' in request.headers: 
        headers['Range'] = request.headers['Range']

    try:
        # 3. درخواست به سرور تلگرام
        # connect timeout=3.05 (باید سریع وصل شود)
        # read timeout=300 (استریم طولانی قطع نشود)
        req = http_session.get(link, stream=True, headers=headers, timeout=(3.05, 300))
        
        def generate():
            try:
                # --- بهینه‌سازی حیاتی: چانک ۸ کیلوبایتی ---
                # ارسال بسته‌های کوچک برای پر کردن آنی بافر پلیر
                for chunk in req.iter_content(chunk_size=8192):
                    if chunk: yield chunk
            except Exception:
                pass

        # 4. ساخت پاسخ
        response = Response(
            stream_with_context(generate()), 
            status=req.status_code, 
            content_type=req.headers.get('Content-Type', 'audio/mpeg')
        )
        
        # 5. انتقال هدرهای ضروری
        safe_headers = ['Content-Range', 'Content-Length', 'Accept-Ranges']
        for h in safe_headers:
            if h in req.headers: response.headers[h] = req.headers[h]
        
        # Fallback Content-Length
        if req.status_code == 200 and 'Content-Length' not in response.headers and track['file_size']:
             response.headers['Content-Length'] = track['file_size']

        # 6. غیرفعال کردن بافرینگ در Nginx و مرورگر (Real-time Mode)
        response.headers['X-Accel-Buffering'] = 'no' 
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        
        return response

    except Exception as e:
        logger.error(f"Stream Error: {e}")
        return "Stream Failed", 500


@stream_bp.route('/cover/<unique_id>')
def get_cover(unique_id):
    db = get_db()
    track = db.execute("SELECT thumb_id, title, performer FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
    default_cover = "https://placehold.co/600x600/121212/333?text=No+Cover&font=roboto"
    
    if not track: return redirect(default_cover)

    # اولویت ۱: آیتونز (کیفیت بالا)
    meta = fetch_itunes_metadata(track['performer'], track['title'])
    if meta and meta['cover']:
        return redirect(meta['cover'])

    # اولویت ۲: تلگرام
    if track['thumb_id']:
        link = get_tg_link(track['thumb_id'])
        if link: return redirect(link)
        
    return redirect(default_cover)


# --- 🔥 Lyrics Engine v5.2 (Non-Blocking & Robust) 🔥 ---
@stream_bp.route('/stream/lyrics/<unique_id>')
def get_lyrics(unique_id):
    # 1. بررسی دیتابیس لوکال (پاسخ آنی)
    cached = get_cached_lyrics(unique_id)
    if cached:
        return jsonify({"status": "found", "lyrics": cached['lyrics'], "source": "local_cache"})

    db = get_db()
    track = db.execute("SELECT title, performer, duration FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
    if not track: return jsonify({"status": "error"}), 404

    raw_artist = track['performer'] or ""
    raw_title = track['title'] or ""
    track_duration = track['duration']

    # 2. اصلاح متادیتا با Oracle
    oracle_data = fetch_itunes_metadata(raw_artist, raw_title)
    if oracle_data:
        search_artist = oracle_data['artist']
        search_title = oracle_data['title']
    else:
        search_artist = re.sub(r'[\(\[].*?[\)\]]', '', raw_artist).strip()
        search_title = re.sub(r'[\(\[].*?[\)\]]', '', raw_title).strip()

    # 3. جستجو در LRCLIB با استراتژی‌های مختلف
    candidates = []
    headers = {'User-Agent': 'FanusMusicPlayer/5.0'}
    
    queries = [f"{search_artist} {search_title}"]
    if len(search_title) > 3: queries.append(search_title)

    for q in queries:
        try:
            # تایم‌اوت ۱۵ ثانیه برای خواندن دیتا (برای مقابله با کندی شبکه)
            res = http_session.get("https://lrclib.net/api/search", params={'q': q}, headers=headers, timeout=(3.05, 15))
            if res.status_code == 200:
                results = res.json()
                if results:
                    candidates.extend(results)
                    if q == queries[0]: break 
        except (Timeout, RequestException):
            continue 

    # 4. الگوریتم تطبیق هوشمند
    best_match = None
    highest_score = 0.0

    for cand in candidates:
        if not cand.get('syncedLyrics'): continue
        
        # فیلتر زمان (اختلاف بیش از ۴ ثانیه رد می‌شود)
        time_diff = abs(cand.get('duration', 0) - track_duration) if track_duration else 0
        if track_duration and time_diff > 4: continue 

        # امتیازدهی شباهت متنی
        t_sim = similarity(cand['trackName'], search_title)
        a_sim = similarity(cand['artistName'], search_artist)
        
        score = (t_sim * 3.0) + (a_sim * 2.0)
        if time_diff <= 2: score += 2.0

        if score > highest_score:
            highest_score = score
            best_match = cand

    if best_match and highest_score > 3.0:
        save_lyrics_to_cache(unique_id, best_match['syncedLyrics'], "lrclib")
        return jsonify({"status": "found", "lyrics": best_match['syncedLyrics']})

    return jsonify({"status": "not_found"})