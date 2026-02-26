# core/routes/stream.py

from flask import Blueprint, Response, request, redirect, stream_with_context, jsonify, render_template
from core.models import get_db
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import Timeout, RequestException
from urllib3.util.retry import Retry
from core.config import Config
import logging
import time

# 🔥 استفاده از سرویس مرکزی متادیتا به جای کدهای تکراری
from core.services.metadata import metadata_service

stream_bp = Blueprint('stream', __name__)
logger = logging.getLogger(__name__)

# --- 1. Network Hardening (High Performance Config) ---
LINK_CACHE = {}
CACHE_DURATION = 3600  # لینک‌های تلگرام تا ۱ ساعت معتبر هستند

retry_strategy = Retry(
    total=3,
    backoff_factor=0.2, 
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=50, pool_maxsize=50)
http_session = requests.Session()
http_session.mount("https://", adapter)
http_session.mount("http://", adapter)


# ==========================================
# 📺 THE LIVE HUB (Consumer Node)
# ==========================================

@stream_bp.route('/live/<token>')
def live_player(token):
    """نقطه ورود اصلی کلاینت‌های پخش‌کننده"""
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    
    if not session:
        return """
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; background:#121212; color:white; font-family:sans-serif; text-align:center;">
            <h1 style="color:#e74c3c; font-size:4rem; margin-bottom: 0;">🚫</h1>
            <h2>Hub Not Found</h2>
            <p style="color:#888;">This Hub is invalid or has been deleted.</p>
        </div>
        """, 404
        
    return render_template('index.html', token=token, session=session)


@stream_bp.route('/api/stream/state/<token>')
def get_hub_state(token):
    """استخراج وضعیت فعلی (State Recovery)"""
    db = get_db()
    query = """
        SELECT 
            s.play_status, s.seek_position, s.sync_timestamp,
            t.file_unique_id, t.duration
        FROM sessions s
        LEFT JOIN tracks t ON s.current_track_id = t.id
        WHERE s.token = ?
    """
    state = db.execute(query, (token,)).fetchone()
    
    if not state or not state['file_unique_id']:
        return jsonify({"status": "idle"})
        
    current_server_time = time.time()
    time_passed = current_server_time - state['sync_timestamp'] if state['sync_timestamp'] else 0
    
    real_position = state['seek_position']
    if state['play_status'] == 'playing':
        real_position += time_passed
        
    if state['duration'] and real_position > state['duration']:
        real_position = state['duration']

    return jsonify({
        "status": "active",
        "file_unique_id": state['file_unique_id'],
        "is_playing": state['play_status'] == 'playing',
        "seek_position": real_position,
        "server_time": current_server_time
    })


# ==========================================
# 🗃️ CACHE HELPERS
# ==========================================

def get_tg_link(file_id):
    """دریافت لینک مستقیم دانلود از سرورهای تلگرام"""
    current_time = time.time()
    
    if file_id in LINK_CACHE:
        cached = LINK_CACHE[file_id]
        if current_time < cached['expire']: 
            return cached['url']
        else: 
            del LINK_CACHE[file_id]

    url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/getFile?file_id={file_id}"
    try:
        res = http_session.get(url, timeout=4.0).json()
        if res.get('ok'):
            file_path = res['result']['file_path']
            download_url = f"https://api.telegram.org/file/bot{Config.BOT_TOKEN}/{file_path}"
            LINK_CACHE[file_id] = {'url': download_url, 'expire': current_time + CACHE_DURATION}
            return download_url
    except Exception as e:
        logger.error(f"Telegram API Error: {e}")
    return None


# ==========================================
# 🚀 CORE STREAMING ROUTES
# ==========================================

@stream_bp.route('/stream/warmup/<unique_id>')
def warmup_link(unique_id):
    """Pre-fetch Endpoint"""
    try:
        db = get_db()
        track = db.execute("SELECT file_id FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
        if track:
            link = get_tg_link(track['file_id'])
            if link:
                return jsonify({"status": "warmed", "unique_id": unique_id})
        return jsonify({"status": "failed"}), 404
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@stream_bp.route('/stream/<unique_id>')
def audio(unique_id):
    """Real-time Streaming Endpoint"""
    db = get_db()
    track = db.execute("SELECT file_id, file_size FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()

    if not track: return "Track Not Found", 404
    
    link = get_tg_link(track['file_id'])
    if not link: return "Link Error", 500

    headers = {}
    if 'Range' in request.headers: 
        headers['Range'] = request.headers['Range']

    try:
        req = http_session.get(link, stream=True, headers=headers, timeout=(3.05, 300))
        
        def generate():
            try:
                for chunk in req.iter_content(chunk_size=8192):
                    if chunk: yield chunk
            except Exception:
                pass

        response = Response(
            stream_with_context(generate()), 
            status=req.status_code, 
            content_type=req.headers.get('Content-Type', 'audio/mpeg')
        )
        
        safe_headers = ['Content-Range', 'Content-Length', 'Accept-Ranges']
        for h in safe_headers:
            if h in req.headers: response.headers[h] = req.headers[h]
        
        if req.status_code == 200 and 'Content-Length' not in response.headers and track['file_size']:
             response.headers['Content-Length'] = track['file_size']

        response.headers['X-Accel-Buffering'] = 'no' 
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    except Exception as e:
        logger.error(f"Stream Error: {e}")
        return "Stream Failed", 500


@stream_bp.route('/cover/<unique_id>')
def get_cover(unique_id):
    """
    سرویس ارائه کاور. ابتدا از سرویس متادیتا کاور باکیفیت می‌خواهد.
    اگر نبود، از عکس پیش‌فرض تلگرام (thumb_id) استفاده می‌کند.
    """
    db = get_db()
    track = db.execute("SELECT thumb_id, title, performer FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
    default_cover = "https://placehold.co/600x600/121212/333?text=No+Cover&font=roboto"
    
    if not track: return redirect(default_cover)

    # استفاده از سرویس مرکزی
    itunes_data = metadata_service.fetch_itunes_data(track['performer'], track['title'])
    if itunes_data and itunes_data.get('cover_url'):
        return redirect(itunes_data['cover_url'])

    # Fallback به تامنیل یوتیوب (ذخیره شده در تلگرام)
    if track['thumb_id']:
        link = get_tg_link(track['thumb_id'])
        if link: return redirect(link)
        
    return redirect(default_cover)


@stream_bp.route('/stream/lyrics/<unique_id>')
def get_lyrics(unique_id):
    """
    ارائه لیریک. ابتدا کش دیتابیس را چک می‌کند، اگر نبود از سرویس متادیتا درخواست می‌کند.
    """
    db = get_db()
    
    # 1. Check Database Cache
    try:
        cached = db.execute("SELECT lyrics FROM lyrics_cache WHERE file_unique_id=?", (unique_id,)).fetchone()
        if cached:
            return jsonify({"status": "found", "lyrics": cached['lyrics'], "source": "local_cache"})
    except: pass

    # 2. Fetch from Central Metadata Service
    track = db.execute("SELECT title, performer, duration FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
    if not track: return jsonify({"status": "error"}), 404

    # استفاده از متد قدرتمندِ fetch_lyrics که خودش اسامی را پاکسازی و بهینه‌سازی می‌کند
    lyrics = metadata_service.fetch_lyrics(track['performer'], track['title'], track['duration'])

    if lyrics:
        # ذخیره در کش برای دفعات بعد
        try:
            db.execute("INSERT OR REPLACE INTO lyrics_cache (file_unique_id, lyrics, source, updated_at) VALUES (?, ?, ?, ?)",
                       (unique_id, lyrics, "lrclib", int(time.time())))
            db.commit()
        except: pass
        return jsonify({"status": "found", "lyrics": lyrics})

    return jsonify({"status": "not_found"})