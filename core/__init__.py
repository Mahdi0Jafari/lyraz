from flask import Flask, Response, render_template, send_from_directory
from .config import Config
# 🔥 تغییر ۱: ایمپورت تابع بستن دیتابیس
from .models import init_db, close_db
from .sse import announcer

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # مدیریت دیتابیس
    with app.app_context():
        init_db()

    # 🔥 تغییر ۲: بستن خودکار اتصال دیتابیس در پایان هر درخواست
    # این خط برای سلامت SQLite و جلوگیری از نشت حافظه حیاتی است
    app.teardown_appcontext(close_db)

    @app.route('/static/<path:filename>')
    def custom_static(filename):
        return send_from_directory('static', filename)

    # ثبت بلوپرینت‌ها
    from .routes.auth import auth_bp
    from .routes.stream import stream_bp 
    from .routes.admin import admin_bp
    from .routes.control import control_bp
    
    app.register_blueprint(auth_bp)
    
    # حذف url_prefix طبق خواسته شما
    app.register_blueprint(stream_bp) 

    app.register_blueprint(admin_bp)
    
    # هندل کردن خطای احتمالی در ایمپورت کنترل (اختیاری اما امن)
    try:
        app.register_blueprint(control_bp)
    except Exception as e: 
        print(f"Warning: Control blueprint failed to load: {e}")
    
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/events')
    def events():
        def stream():
            # ساخت صف اختصاصی برای کلاینت
            messages = announcer.listen()
            try:
                while True:
                    # گرفتن پیام از صف (اینجا منتظر می‌ماند تا پیامی بیاید)
                    msg = messages.get()
                    yield msg
            except GeneratorExit:
                # اگر کلاینت قطع شد، اینجا می‌توان لاگ زد (اختیاری)
                pass

        # هدرهای لازم برای جلوگیری از بافر شدن توسط Nginx یا مرورگر
        return Response(stream(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        })

    @app.route('/favicon.ico')
    def favicon():
        return "", 204

    return app