# core/sse.py

import logging

try:
    from gevent.queue import Queue as GeventQueue
    from gevent.lock import BoundedSemaphore
    def create_queue(maxsize=50):
        return GeventQueue(maxsize=maxsize)
    def create_lock():
        return BoundedSemaphore(1)
except ImportError:
    import queue
    import threading
    def create_queue(maxsize=50):
        return queue.Queue(maxsize=maxsize)
    def create_lock():
        return threading.Lock()

logger = logging.getLogger(__name__)

class MessageAnnouncer:
    """
    مدیریت ارسال رویدادهای زنده (SSE) به کلاینت‌ها.
    سازگار با Gevent و Multi-threading استاندارد بدون قفل شدن لوپ.
    """
    def __init__(self):
        self.listeners = [] # list of (queue, token)
        self.lock = create_lock()

    def listen(self, token=None):
        """
        ثبت‌نام یک کلاینت جدید با توکن هاب مربوطه.
        یک صف (Queue) اختصاصی برمی‌گرداند.
        """
        q = create_queue(maxsize=50)
        with self.lock:
            self.listeners.append((q, token))
            count = sum(1 for _, t in self.listeners if t == token) if token else len(self.listeners)

        if token:
            import json
            self.announce(f"data: {json.dumps({'type': 'device_count', 'session_token': token, 'count': count})}\n\n")
        return q

    def unlisten(self, q, token=None):
        """
        حذف کلاینت از لیست شنوندگان پس از قطع ارتباط و اطلاع‌رسانی تعداد باقیمانده.
        """
        found_token = token
        with self.lock:
            for item in list(self.listeners):
                if item[0] == q:
                    found_token = item[1] or token
                    try:
                        self.listeners.remove(item)
                    except ValueError:
                        pass
                    break
            count = sum(1 for _, t in self.listeners if t == found_token) if found_token else len(self.listeners)

        if found_token:
            import json
            self.announce(f"data: {json.dumps({'type': 'device_count', 'session_token': found_token, 'count': count})}\n\n")

    def announce(self, msg):
        """
        ارسال پیام (Broadcast) به تمام شنوندگان فعال.
        """
        to_remove = []
        with self.lock:
            for i, (q, _) in enumerate(self.listeners):
                try:
                    q.put_nowait(msg)
                except Exception:
                    to_remove.append(i)
            
            for i in reversed(to_remove):
                try:
                    del self.listeners[i]
                except IndexError:
                    pass

    def get_listener_count(self, token=None):
        with self.lock:
            if token:
                return sum(1 for _, t in self.listeners if t == token)
            return len(self.listeners)

announcer = MessageAnnouncer()