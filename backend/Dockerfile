FROM python:3.11-slim

# Chromium runtime deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdbus-1-3 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install chromium

COPY . .

ENV PYTHONUNBUFFERED=1

# --workers 1: free-tier 512MB RAM can't fit two Chromium instances
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 120 --workers 1 --threads 4 app:app
