FROM python:3.11-slim

# Chromium + all runtime deps (Playwright's official dependency set)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 libatspi2.0-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libasound2 libpango-1.0-0 libcairo2 libx11-6 libxcb1 \
    libxext6 libx11-xcb1 libxcursor1 libxi6 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium

COPY . .

ENV PYTHONUNBUFFERED=1

# --workers 1: free-tier 512MB RAM can't fit two Chromium instances
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 120 --workers 1 --threads 4 app:app
