FROM python:3.11-slim

# تنظیمات محیطی پایتون برای داکر
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ۱. نصب پکیج‌های سیستمی (FFmpeg حیاتی است)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ۲. نصب وابستگی‌ها
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ۳. کپی کل پروژه
COPY . /app

# پورت ۵۰۰۰ برای وب‌سایت
EXPOSE 5000
# پورت ۸۴۴۳ برای دریافت مستقیم ترافیک Webhook ربات تلگرام
EXPOSE 8443

# ⚠️ نکته مهم: اینجا CMD نداریم. 
# دستور اجرا در docker-compose.yml تعیین می‌شود.