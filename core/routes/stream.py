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
from urllib.parse import quote

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

def get_tg_link(file_id, track_row=None):
    """دریافت لینک مستقیم دانلود از سرورهای تلگرام با قابلیت خودترمیمی در صورت تغییر توکن"""
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
        elif not res.get('ok') and track_row:
            storage_msg_id = track_row['storage_message_id'] if ('storage_message_id' in track_row.keys() and track_row['storage_message_id']) else None
            if storage_msg_id and Config.STORAGE_CHANNEL_ID:
                logger.warning(f"Telegram getFile failed for {file_id}. Attempting self-heal via storage msg {storage_msg_id}...")
                fwd_url = f"https://api.telegram.org/bot{Config.BOT_TOKEN}/forwardMessage"
                fwd_res = http_session.post(
                    fwd_url,
                    json={
                        "chat_id": Config.STORAGE_CHANNEL_ID,
                        "from_chat_id": Config.STORAGE_CHANNEL_ID,
                        "message_id": storage_msg_id
                    },
                    timeout=5.0
                ).json()
                if fwd_res.get('ok'):
                    new_audio = fwd_res['result'].get('audio')
                    if new_audio:
                        new_file_id = new_audio['file_id']
                        db = get_db()
                        db.execute("UPDATE tracks SET file_id=? WHERE id=?", (new_file_id, track_row['id']))
                        db.commit()
                        try:
                            http_session.post(
                                f"https://api.telegram.org/bot{Config.BOT_TOKEN}/deleteMessage",
                                json={"chat_id": Config.STORAGE_CHANNEL_ID, "message_id": fwd_res['result']['message_id']},
                                timeout=3.0
                            )
                        except Exception: pass
                        return get_tg_link(new_file_id)
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
        track = db.execute("SELECT id, file_id, storage_message_id FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
        if track:
            link = get_tg_link(track['file_id'], track_row=track)
            if link:
                return jsonify({"status": "warmed", "unique_id": unique_id})
        return jsonify({"status": "failed"}), 404
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


@stream_bp.route('/stream/<unique_id>')
def audio(unique_id):
    """Real-time Streaming Endpoint"""
    db = get_db()
    track = db.execute("SELECT id, file_id, file_size, storage_message_id FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()

    if not track: return "Track Not Found", 404
    
    link = get_tg_link(track['file_id'], track_row=track)
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


@stream_bp.route('/download/<unique_id>')
def download_audio(unique_id):
    """
    نقطه پایانی دانلود مستقیم و امن فایل صوتی (Content-Disposition: attachment)
    با نام‌گذاری استاندارد، سرعت ماکسیمم چانکینگ، پشتیبانی از IDM و Range Header و ضد ایندکس کپی‌رایت.
    """
    db = get_db()
    track = db.execute("SELECT id, file_id, file_size, title, performer, storage_message_id FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()

    if not track:
        return "Track Not Found", 404
    
    link = get_tg_link(track['file_id'], track_row=track)
    if not link:
        return "Audio Link Expired or Unavailable", 500

    headers = {}
    if 'Range' in request.headers:
        headers['Range'] = request.headers['Range']

    try:
        req = http_session.get(link, stream=True, headers=headers, timeout=(3.05, 300))
        
        def generate():
            try:
                for chunk in req.iter_content(chunk_size=65536):  # 64KB chunks for fast IDM/browser downloading
                    if chunk: yield chunk
            except Exception:
                pass

        response = Response(
            stream_with_context(generate()), 
            status=req.status_code, 
            content_type='audio/mpeg'
        )
        
        # نام‌گذاری فایل با فرمت "Artist - Title.mp3" با پشتیبانی کامل از کاراکترهای فارسی و استاندارد RFC 5987
        raw_artist = (track['performer'] or 'Lyraz').strip()
        raw_title = (track['title'] or 'Track').strip()
        clean_filename = f"{raw_artist} - {raw_title}.mp3"
        safe_ascii_name = "".join([c for c in clean_filename if c.isalnum() or c in " .-_"]).strip() or "track.mp3"
        encoded_filename = quote(clean_filename)

        response.headers['Content-Disposition'] = f'attachment; filename="{safe_ascii_name}"; filename*=UTF-8\'\'{encoded_filename}'
        
        safe_headers = ['Content-Range', 'Content-Length', 'Accept-Ranges']
        for h in safe_headers:
            if h in req.headers: response.headers[h] = req.headers[h]
        
        if req.status_code == 200 and 'Content-Length' not in response.headers and track['file_size']:
            response.headers['Content-Length'] = track['file_size']

        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        response.headers['Cache-Control'] = 'public, max-age=86400'
        return response
    except Exception as e:
        logger.error(f"Download Stream Error: {e}")
        return "Download Failed", 500


@stream_bp.route('/api/hub/<token>/export_links')
def export_hub_links(token):
    """
    خروجی لیست تجمیعی لینک‌های دانلود تمام آهنگ‌های یک هاب جهت کپی در IDM یا دانلود فایل TXT.
    """
    db = get_db()
    session = db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    if not session:
        return jsonify({"status": "error", "message": "Hub not found"}), 404

    query = """
        SELECT t.id, t.title, t.performer, t.file_unique_id, t.file_size, t.duration
        FROM playlist_items pi
        JOIN tracks t ON pi.track_id = t.id
        WHERE pi.session_token = ?
        ORDER BY pi.id ASC
    """
    tracks = db.execute(query, (token,)).fetchall()

    base_url = request.host_url.rstrip('/')
    links = []
    lines = []
    for trk in tracks:
        dl_url = f"{base_url}/download/{trk['file_unique_id']}"
        links.append({
            "title": trk["title"],
            "performer": trk["performer"],
            "unique_id": trk["file_unique_id"],
            "url": dl_url
        })
        lines.append(dl_url)

    fmt = request.args.get('format', 'json').lower()
    if fmt == 'txt':
        content = "\n".join(lines)
        response = Response(content, mimetype="text/plain; charset=utf-8")
        hub_name = session['device_name'] or token
        safe_name = quote(f"{hub_name}_links.txt")
        response.headers["Content-Disposition"] = f'attachment; filename="download_links.txt"; filename*=UTF-8\'\'{safe_name}'
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    return jsonify({
        "status": "success",
        "hub_token": token,
        "device_name": session["device_name"],
        "total_tracks": len(links),
        "links": links,
        "raw_text": "\n".join(lines)
    })


DEFAULT_COVER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#18181b"/>
      <stop offset="100%" stop-color="#09090b"/>
    </linearGradient>
  </defs>
  <rect width="600" height="600" rx="32" fill="url(#g)"/>
  <circle cx="300" cy="300" r="140" fill="#27272a" opacity="0.6"/>
  <path d="M260 210v180c0 16.5-13.5 30-30 30s-30-13.5-30-30 13.5-30 30-30c5.3 0 10.3 1.4 14.6 3.8V250l110-25v145c0 16.5-13.5 30-30 30s-30-13.5-30-30 13.5-30 30-30c5.3 0 10.3 1.4 14.6 3.8V195L260 210z" fill="#0df233"/>
</svg>"""

COVER_BYTES_CACHE = {}

def process_cover_image(img_bytes):
    """تبدیل هر عکس به کاور مربعی استاندارد 500x500 بدون کادرهای عریض و با فرمت بهینه JPEG"""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        w, h = img.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        img_cropped = img.crop((left, top, left + min_dim, top + min_dim))
        img_resized = img_cropped.resize((500, 500), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img_resized.save(out, format='JPEG', quality=88, optimize=True)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"Cover Processing Error: {e}")
        return img_bytes

@stream_bp.route('/cover/<unique_id>')
def get_cover(unique_id):
    """
    سرویس مرکزی و پرسرعت ارائه کاور با کش مستقیم، تبدیل به ابعاد مربع ۱:۱ و هدر کامل CORS.
    جلوگیری از خطای مسدود شدن CORS و کاورهای خراب یا غیرمربع.
    """
    if unique_id == 'default':
        return Response(DEFAULT_COVER_SVG, mimetype='image/svg+xml', headers={
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=604800'
        })

    # 1. بازیابی از کش مستقیم بایت‌ها در حافظه
    if unique_id in COVER_BYTES_CACHE:
        cached_data, mime = COVER_BYTES_CACHE[unique_id]
        return Response(cached_data, mimetype=mime, headers={
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=604800, immutable'
        })

    db = get_db()
    track = db.execute("SELECT thumb_id, title, performer, youtube_id FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
    
    if not track:
        return Response(DEFAULT_COVER_SVG, mimetype='image/svg+xml', headers={
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=86400'
        })

    # لیست منابع احتمالی برای یافتن کاور
    candidate_urls = []

    # الف) جستجوی کاور رسمی و مربعی از iTunes
    try:
        itunes_data = metadata_service.fetch_itunes_data(track['performer'], track['title'])
        if itunes_data and itunes_data.get('cover_url'):
            candidate_urls.append(itunes_data['cover_url'])
    except Exception as e:
        logger.warning(f"iTunes fetch cover error: {e}")

    # ب) تصویر تامنیل تلگرام
    if track['thumb_id']:
        try:
            tg_link = get_tg_link(track['thumb_id'])
            if tg_link:
                candidate_urls.append(tg_link)
        except Exception:
            pass

    # ج) تصویر ویدیوی یوتیوب
    if track['youtube_id']:
        candidate_urls.append(f"https://i.ytimg.com/vi/{track['youtube_id']}/hqdefault.jpg")

    # تلاش برای دانلود مستقیم و تبدیل به تصویر مربعی استاندارد
    for url in candidate_urls:
        try:
            r = http_session.get(url, timeout=4)
            if r.status_code == 200 and len(r.content) > 500:
                processed_bytes = process_cover_image(r.content)
                COVER_BYTES_CACHE[unique_id] = (processed_bytes, 'image/jpeg')
                return Response(processed_bytes, mimetype='image/jpeg', headers={
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'public, max-age=604800, immutable'
                })
        except Exception as err:
            logger.warning(f"Failed to proxy cover from {url}: {err}")

    # در صورت عدم وجود هرگونه عکس، بازگرداندن کاور پیش‌فرض SVG
    return Response(DEFAULT_COVER_SVG, mimetype='image/svg+xml', headers={
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'public, max-age=86400'
    })


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