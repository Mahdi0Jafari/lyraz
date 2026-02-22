# core/routes/bridge.py

from flask import Blueprint, request, jsonify
from core.sse import announcer
import logging

bridge_bp = Blueprint('bridge', __name__)
logger = logging.getLogger(__name__)

@bridge_bp.route('/internal/announce', methods=['POST'])
def internal_announce():
    """
    دریافت پیام از کانتینر Bot (طریق شبکه داخلی داکر)
    و پخش (Broadcast) آن برای تمام کلاینت‌های متصل به هاب (Web Players).
    """
    data = request.json
    if not data:
        return jsonify({'status': 'error', 'msg': 'No data provided'}), 400
        
    message = data.get('message')
    if message:
        # پیام مستقیماً به سیستم SSE (Server-Sent Events) تزریق می‌شود
        announcer.announce(message)
        logger.debug(f"Bridge: Message broadcasted via SSE.")
        return jsonify({'status': 'ok', 'broadcasted': True})
        
    return jsonify({'status': 'ignored'})