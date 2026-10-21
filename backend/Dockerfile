FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# Chromium + complete runtime deps (Playwright chromium on Debian bookworm),
# pip deps and browser download in ONE layer so apt lists stay valid.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libatspi2.0-0 libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    libx11-6 libxcb1 libxext6 libx11-xcb1 \
    fonts-liberation \
 && pip install --no-cache-dir -r requirements.txt \
 && playwright install chromium \
 && rm -rf /var/lib/apt/lists/* /root/.cache/pip

COPY . .

ENV PYTHONUNBUFFERED=1

# --workers 1: free-tier 512MB RAM can't fit two Chromium instances
# --timeout 180: multi-page scroll-capture can take ~90s+
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 180 --workers 1 --threads 4 app:app
