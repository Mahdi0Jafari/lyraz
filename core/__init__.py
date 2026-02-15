# core/__init__.py

from flask import Flask, Response, render_template, send_from_directory
from .config import Config
from .models import init_db, close_db
from .sse import announcer

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # ۱. اطمینان از وجود دیتابیس هنگام بالا آمدن سایت
    with app.app_context():
        init_db()

    # ۲. بستن کانکشن دیتابیس در پایان هر درخواست (حیاتی برای WAL Mode)
    app.teardown_appcontext(close_db)

    @app.route('/static/<path:filename>')
    def custom_static(filename):
        return send_from_directory('static', filename)

    # --- ثبت Blueprint ها ---
    from .routes.auth import auth_bp
    from .routes.stream import stream_bp 
    from .routes.admin import admin_bp
    from .routes.control import control_bp
    
    # 🔥 تغییر مهم: اضافه کردن Bridge برای دریافت پیام از کانتینر ربات
    from .routes.bridge import bridge_bp  

    app.register_blueprint(auth_bp)
    app.register_blueprint(stream_bp) 
    app.register_blueprint(admin_bp)
    app.register_blueprint(bridge_bp) # <--- مسیر داخلی ارتباطی
    
    try:
        app.register_blueprint(control_bp)
    except Exception as e: 
        print(f"Warning: Control blueprint failed to load: {e}")
    
    # --- مسیرهای عمومی ---
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/events')
    def events():
        def stream():
            # ساخت صف اختصاصی برای کلاینت (تلویزیون/موبایل)
            messages = announcer.listen()
            try:
                while True:
                    # منتظر دریافت پیام از سمت Bridge یا Admin
                    msg = messages.get()
                    yield msg
            except GeneratorExit:
                pass

        # تنظیم هدرها برای جلوگیری از کش شدن استریم
        return Response(stream(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        })

    @app.route('/favicon.ico')
    def favicon():
        return "", 204

    return app