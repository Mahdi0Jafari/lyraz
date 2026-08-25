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
            # ایجاد صف اختصاصی برای این کلاینت
            messages = announcer.listen()
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
                announcer.unlisten(messages)

        # تنظیم هدرهای استاندارد برای استریم زنده و جلوگیری از بافرینگ توسط Nginx/Cloudflare
        return Response(stream(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Content-Type': 'text/event-stream',
            'Connection': 'keep-alive'
        })

    @app.route('/favicon.ico')
    def favicon():
        return "", 204

    return app