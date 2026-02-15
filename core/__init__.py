from flask import Flask, Response, render_template, send_from_directory
from .config import Config
from .models import init_db
from .sse import announcer

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    with app.app_context():
        init_db()

    @app.route('/static/<path:filename>')
    def custom_static(filename):
        return send_from_directory('static', filename)

    # ثبت بلوپرینت‌ها
    from .routes.auth import auth_bp
    from .routes.stream import stream_bp # ایمپورت استریم
    from .routes.admin import admin_bp
    from .routes.control import control_bp
    
    app.register_blueprint(auth_bp)
    
    # --- اصلاح مهم: حذف url_prefix ---
    # چون در خود فایل stream.py آدرس‌ها را کامل می‌نویسیم
    app.register_blueprint(stream_bp) 

    app.register_blueprint(admin_bp)
    try:
        app.register_blueprint(control_bp)
    except: pass
    
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/events')
    def events():
        def stream():
            messages = announcer.listen()
            while True:
                msg = messages.get()
                yield msg
        return Response(stream(), mimetype='text/event-stream')

    @app.route('/favicon.ico')
    def favicon():
        return "", 204

    return app