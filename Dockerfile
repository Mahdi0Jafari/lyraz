FROM python:3.11-slim

# تنظیمات پایه
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ۱. نصب FFmpeg (حیاتی برای دانلودر)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ۲. نصب وابستگی‌ها
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ۳. کپی کدها
COPY . /app

EXPOSE 5000

# ۴. 🔥 دستور اجرای نهایی (اینجا جادو اتفاق می‌افتد)
# --workers 1: همه چیز در یک پروسه باشد تا متغیر announcer مشترک بماند
# --threads 100: قدرت پاسخگویی همزمان به ۱۰۰ نفر (برای SSE عالی است)
CMD ["gunicorn", "--workers", "1", "--threads", "100", "--bind", "0.0.0.0:5000", "app:app"]