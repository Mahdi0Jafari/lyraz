# core/routes/bridge.py
from flask import Blueprint, request, jsonify
from core.sse import announcer
import logging

bridge_bp = Blueprint('bridge', __name__)
logger = logging.getLogger(__name__)

@bridge_bp.route('/internal/announce', methods=['POST'])
def internal_announce():
    """
    این متد فقط توسط کانتینر Bot (از طریق شبکه داکر) صدا زده می‌شود.
    هدف: دریافت پیام از ربات و پخش آن برای کلاینت‌های SSE (تلویزیون/موبایل).
    """
    # امنیت ساده: فقط اجازه درخواست از لوکال نتورک داکر (اختیاری)
    # در اینجا فرض بر اعتماد به شبکه داخلی داکر است.
    
    data = request.json
    if not data:
        return jsonify({'status': 'error', 'msg': 'No data'}), 400
        
    message = data.get('message')
    if message:
        # تزریق پیام به سیستم SSE در کانتینر وب
        announcer.announce(message)
        return jsonify({'status': 'ok', 'broadcasted': True})
        
    return jsonify({'status': 'ignored'})