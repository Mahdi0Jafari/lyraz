# core/sse.py

import queue
import threading
import logging

logger = logging.getLogger(__name__)

class MessageAnnouncer:
    def __init__(self):
        self.listeners = []
        # قفل برای مدیریت دسترسی همزمان تردهای مختلف (Bot Thread vs Web Threads)
        self.lock = threading.Lock()

    def listen(self):
        """
        ثبت‌نام یک کلاینت جدید برای دریافت پیام‌ها.
        یک صف اختصاصی برای کلاینت می‌سازد و برمی‌گرداند.
        """
        # ظرفیت صف را بالا بردیم (50) تا در صورت ارسال رگباری پیام‌ها،
        # کلاینت‌های کند بلافاصله قطع نشوند.
        q = queue.Queue(maxsize=50)
        
        with self.lock:
            self.listeners.append(q)
            
        return q

    def announce(self, msg):
        """
        ارسال پیام به تمام کلاینت‌های متصل (Broadcast).
        اگر کلاینتی مرده باشد (صف پر شده باشد)، از لیست حذف می‌شود.
        """
        with self.lock:
            # لیست را معکوس پیمایش می‌کنیم تا حذف کردن ایندکس‌ها را به هم نریزد
            for i in reversed(range(len(self.listeners))):
                try:
                    # تلاش برای گذاشتن پیام در صف کلاینت
                    self.listeners[i].put_nowait(msg)
                except queue.Full:
                    # اگر صف پر شده، یعنی کلاینت گوش نمی‌دهد یا قطع شده -> حذفش کن
                    del self.listeners[i]

# یک نمونه گلوبال که در کل برنامه استفاده شود
# نکته مهم: این روش فقط زمانی کار می‌کند که Gunicorn با 1 Worker اجرا شود.
announcer = MessageAnnouncer()