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
        self.listeners = []
        self.lock = create_lock()

    def listen(self):
        """
        ثبت‌نام یک کلاینت جدید.
        یک صف (Queue) اختصاصی برمی‌گرداند.
        """
        q = create_queue(maxsize=50)
        with self.lock:
            self.listeners.append(q)
        return q

    def unlisten(self, q):
        """
        حذف کلاینت از لیست شنوندگان پس از قطع ارتباط.
        """
        with self.lock:
            if q in self.listeners:
                try:
                    self.listeners.remove(q)
                except ValueError:
                    pass

    def announce(self, msg):
        """
        ارسال پیام (Broadcast) به تمام شنوندگان فعال.
        """
        to_remove = []
        with self.lock:
            for i, q in enumerate(self.listeners):
                try:
                    q.put_nowait(msg)
                except Exception:
                    to_remove.append(i)
            
            for i in reversed(to_remove):
                try:
                    del self.listeners[i]
                except IndexError:
                    pass

    def get_listener_count(self):
        with self.lock:
            return len(self.listeners)

announcer = MessageAnnouncer()