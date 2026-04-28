FROM python:3.9-slim

# System dependencies required by Firefox/Playwright headless
RUN apt-get update && apt-get install -y \
    libgtk-3-0 \
    libx11-xcb1 \
    libasound2 \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Firefox binary for Playwright (headless, no Chromium needed)
RUN playwright install firefox

COPY . .

CMD ["python", "src/scraper.py"]