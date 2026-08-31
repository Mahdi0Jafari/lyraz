# core/__init__.py

from flask import Flask, Response, render_template, send_from_directory, abort
from .config import Config
from .models import init_db, close_db, get_db
from .sse import announcer

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # ۱. تضمین وجود دیتابیس هنگام لود شدن اپلیکیشن
    with app.app_context():
        init_db()

    # ۲. مدیریت چرخه حیات اتصال دیتابیس
    app.teardown_appcontext(close_db)

    @app.route('/static/<path:filename>')
    def custom_static(filename):
        return send_from_directory('static', filename)

    # --- ثبت Blueprint ها (V4.4 Architecture) ---
    from .routes.auth import auth_bp
    from .routes.stream import stream_bp 
    from .routes.admin import admin_bp
    from .routes.control import control_bp
    from .routes.bridge import bridge_bp  

    app.register_blueprint(auth_bp)
    app.register_blueprint(stream_bp) 
    app.register_blueprint(admin_bp)
    app.register_blueprint(bridge_bp)
    
    try:
        app.register_blueprint(control_bp)
    except Exception as e: 
        app.logger.warning(f"Control blueprint failed to load: {e}")
    
    # --- مسیرهای عمومی و سیستمی ---
    @app.route('/')
    def index():
        return render_template('index.html')

    # 🔥 اصلاح امنیتی: تبدیل به مسیر اختصاصی با اعتبارسنجی توکن
    @app.route('/api/events/<token>')
    def events(token):
        """
        Secure SSE Tunnel.
        فقط به کلاینت‌هایی که توکن معتبر دارند اجازه گوش دادن به رویدادها را می‌دهد.
        """
        # اعتبارسنجی سریع توکن در لایه اپلیکیشن
        db = get_db()
        hub = db.execute("SELECT token FROM sessions WHERE token = ?", (token,)).fetchone()
        
        if not hub:
            # اگر توکن نامعتبر بود، اتصال را ریجکت کن
            return Response("Unauthorized Hub Token", status=403)

        def stream():
            # ایجاد صف اختصاصی برای این کلاینت با ثبت توکن هاب
            messages = announcer.listen(token)
            try:
                # ارسال سیگنال اولیه به محض برقراری اتصال برای فلاش شدن بافر کلادفلر
                yield ": connected\n\n"
                while True:
                    try:
                        # دریافت پیام با تایم‌اوت ۱۵ ثانیه
                        msg = messages.get(timeout=15)
                        yield msg
                    except Exception:
                        # ارسال سیگنال Keep-Alive پینگ برای جلوگیری از قطع شدن اتصال توسط کلادفلر و Nginx
                        yield ": ping\n\n"
            except GeneratorExit:
                # مدیریت خروج کلاینت و آزاد کردن صف در حافظه
                announcer.unlisten(messages, token)

        # تنظیم هدرهای استاندارد برای استریم زنده و جلوگیری از بافرینگ توسط Nginx/Cloudflare
        return Response(stream(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Content-Type': 'text/event-stream',
            'Connection': 'keep-alive'
        })

    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory('static/icons', 'icon-48x48.png', mimetype='image/png')

    # ==========================================
    # 🔍 TECHNICAL SEO & AI BOT CRAWLABILITY (2026 STANDARDS)
    # ==========================================

    @app.route('/robots.txt')
    def robots_txt():
        content = """# Lyraz Robots.txt - 2026 Standards
User-agent: *
Allow: /
Allow: /static/
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin
Disallow: /api/
Disallow: /remote/

# AI Crawlers Policy (Search & Knowledge Ingestion)
User-agent: GPTBot
Allow: /
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin
Disallow: /api/

User-agent: ClaudeBot
Allow: /
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin
Disallow: /api/

User-agent: PerplexityBot
Allow: /
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin
Disallow: /api/

User-agent: Google-Extended
Allow: /
Allow: /llms.txt
Allow: /llms-full.txt
Disallow: /admin
Disallow: /api/

User-agent: Applebot
Allow: /
Disallow: /admin
Disallow: /api/

Sitemap: https://lyraz.ir/sitemap.xml
"""
        return Response(content, mimetype='text/plain; charset=utf-8')

    @app.route('/sitemap.xml')
    def sitemap_xml():
        content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url>
    <loc>https://lyraz.ir/</loc>
    <xhtml:link rel="alternate" hreflang="en" href="https://lyraz.ir/"/>
    <xhtml:link rel="alternate" hreflang="fa" href="https://lyraz.ir/"/>
    <xhtml:link rel="alternate" hreflang="x-default" href="https://lyraz.ir/"/>
    <lastmod>2026-08-29</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
        return Response(content, mimetype='application/xml; charset=utf-8')

    @app.route('/llms.txt')
    def llms_txt():
        content = """# Lyraz
> High-Resolution Synchronized Audio Streaming & Collaborative Music Hub

Lyraz (https://lyraz.ir) is a decentralized web audio player and live collaborative music hub. It enables zero-latency synchronized audio playback across desktop, mobile, and Smart TV screens, integrated with a high-performance Telegram audio vault.

## Core Capabilities
- **Synchronized Live Hubs:** Stream audio synchronously across multiple screens using QR-based session pairing.
- **Telegram Bot Audio Vault (@LyrazBot):** Instant downloads and queue dispatching for Spotify playlists and YouTube tracks at up to 320kbps.
- **Synchronized Real-Time Lyrics:** Millisecond-accurate synchronized lyrics rendering with dynamic background ambient glow.
- **Curated Music Magazine (@LyrazMusic):** Editorial music curation, timeless sounds, hidden gems, and storytelling.

## Entities & Links
- Website: https://lyraz.ir
- Telegram Bot: https://t.me/LyrazBot
- Telegram Channel: https://t.me/LyrazMusic
- Full Documentation: https://lyraz.ir/llms-full.txt
"""
        return Response(content, mimetype='text/plain; charset=utf-8')

    @app.route('/llms-full.txt')
    def llms_full_txt():
        content = """# Lyraz - Comprehensive Platform & Technical Specification

## Overview
Lyraz is a next-generation distributed audio streaming system and collaborative live player developed for cross-screen entertainment, parties, and personal high-fidelity music playback.

## Architecture
- **Web Player (https://lyraz.ir):** Dark OLED interface built with Vanilla CSS, Server-Sent Events (SSE) for zero-latency device synchronization, and hardware-accelerated dynamic blur shaders.
- **Bot Engine (@LyrazBot):** Python-based asynchronous worker engine interfacing with Telegram Bot API, yt-dlp, and Spotify metadata APIs.
- **Audio Quality:** Native support for 128kbps, 192kbps, and lossless 320kbps audio bitrate tiers.
- **Device Pairing:** QR-code based instant pairing without traditional username/password barriers.

## Official Channels & Community
- **Website:** https://lyraz.ir
- **Music Channel:** https://t.me/LyrazMusic ("Lyraz | Music & Stories")
- **Bot Interface:** https://t.me/LyrazBot ("@LyrazBot")
"""
        return Response(content, mimetype='text/plain; charset=utf-8')

    return app