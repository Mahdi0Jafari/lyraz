# core/sse.py

import queue
import threading
import logging

logger = logging.getLogger(__name__)

class MessageAnnouncer:
    """
    مدیریت ارسال رویدادهای زنده (SSE) به کلاینت‌ها.
    این کلاس بر اساس الگوی Publisher-Subscriber در حافظه کار می‌کند.
    """
    def __init__(self):
        self.listeners = []
        # قفل برای جلوگیری از Race Condition در محیط‌های چند نخی (Threading)
        self.lock = threading.Lock()

    def listen(self):
        """
        ثبت‌نام یک کلاینت جدید.
        یک صف (Queue) اختصاصی برمی‌گرداند که کلاینت باید روی آن منتظر بماند.
        """
        # ظرفیت ۵۰ پیام برای جلوگیری از پر شدن حافظه در صورت کندی کلاینت
        q = queue.Queue(maxsize=50)
        
        with self.lock:
            self.listeners.append(q)
            
        return q

    def announce(self, msg):
        """
        ارسال پیام (Broadcast) به تمام شنوندگان فعال.
        """
        # استفاده از لیست موقت برای حذف کلاینت‌های مرده (Dead Listeners)
        # تاثیری روی پرفورمنس ندارد چون تعداد شنوندگان همزمان معمولاً کم است.
        to_remove = []

        with self.lock:
            for i, q in enumerate(self.listeners):
                try:
                    # ارسال پیام بدون مسدود کردن (Non-blocking)
                    q.put_nowait(msg)
                except queue.Full:
                    # اگر صف پر شده، یعنی کلاینت پاسخ نمی‌دهد -> مارک برای حذف
                    to_remove.append(i)
            
            # پاکسازی کلاینت‌های مرده (از آخر به اول برای حفظ ایندکس‌ها)
            for i in reversed(to_remove):
                try:
                    del self.listeners[i]
                except IndexError:
                    pass # اگر قبلاً حذف شده باشد

    def get_listener_count(self):
        """برای مانیتورینگ: تعداد افراد آنلاین"""
        with self.lock:
            return len(self.listeners)

# نمونه گلوبال
# ⚠️ نکته معماری: برای عملکرد صحیح این ماژول، Gunicorn باید فقط ۱ ورکر داشته باشد
# یا از Worker Type 'gevent' استفاده کند.
announcer = MessageAnnouncer()