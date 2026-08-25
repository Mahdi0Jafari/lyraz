FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ۱. نصب پکیج‌های سیستمی و موتور Deno جهت حل چالش‌های رمزنگاری یوتیوب
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    curl -fsSL https://deno.land/install.sh | sh && \
    cp /root/.deno/bin/deno /usr/local/bin/ && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ۲. نصب وابستگی‌ها
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ۳. کپی کل پروژه
COPY . /app

EXPOSE 5000