import requests
import cloudscraper
from bs4 import BeautifulSoup
import json
import time
import os
from dotenv import load_dotenv
import logging
from fake_useragent import UserAgent
import random
from collections import deque

logging.basicConfig(
    level=logging.DEBUG, format="|%(levelname)s| %(asctime)s - %(message)s"
)

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Idealista search URL — alquiler en Sevilla, ordenado por más reciente
IDEALISTA_URL = "https://www.idealista.com/alquiler-viviendas/sevilla-sevilla/?ordenado-por=fecha-publicacion-desc"

# Neighborhoods to exclude
EXCLUDED_AREAS = []

# Keywords to filter out in the description
EXCLUDED_TERMS = []

# Exclude unwanted floors
EXCLUDED_FLOORS = []

# Track seen listings to avoid duplicates
SEEN_LISTINGS_FILE = "/app/data/seen_listings.json"

# Max listings saved in deduplication queue
MAX_LISTINGS = 100

# Track if an error has already been notified
ERROR_LOG_FILE = "/app/data/error_log.json"


def load_seen_listings():
    try:
        with open(SEEN_LISTINGS_FILE, "r") as f:
            return deque(json.load(f), maxlen=MAX_LISTINGS)
    except (FileNotFoundError, json.JSONDecodeError):
        return deque(maxlen=MAX_LISTINGS)


def save_seen_listings(seen_listings):
    os.makedirs(os.path.dirname(SEEN_LISTINGS_FILE), exist_ok=True)
    with open(SEEN_LISTINGS_FILE, "w") as f:
        json.dump(list(seen_listings), f)


def load_error_status():
    try:
        with open(ERROR_LOG_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"last_error": None}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_error": None}


def save_error_status(error_code):
    os.makedirs(os.path.dirname(ERROR_LOG_FILE), exist_ok=True)
    with open(ERROR_LOG_FILE, "w") as f:
        json.dump({"error": error_code}, f)


def send_telegram_notification(text, image_url=None, session=None):
    """
    Send a message to the configured Telegram group.
    If image_url is provided, sends a photo with caption; otherwise sends a text message.
    Uses HTML parse mode.
    """
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    try:
        if image_url:
            # Normalize URL
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                image_url = "https://www.idealista.com" + image_url

            # Try sending photo with caption (caption max 1024 chars)
            caption = text[:1024]
            resp = requests.post(
                f"{base_url}/sendPhoto",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "photo": image_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                timeout=15,
            )

            if resp.status_code == 200:
                logging.debug("Telegram photo notification sent successfully")
                return True
            else:
                logging.warning(
                    f"sendPhoto failed ({resp.status_code}): {resp.text} — falling back to text"
                )

        # Fallback or no image: send plain text message
        resp = requests.post(
            f"{base_url}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )

        if resp.status_code == 200:
            logging.debug("Telegram text notification sent successfully")
            return True
        else:
            logging.error(f"sendMessage failed ({resp.status_code}): {resp.text}")
            return False

    except Exception as e:
        logging.error(f"Error sending Telegram notification: {e}")
        return False


def scrape_idealista():
    logging.debug("Scraping Idealista...")

    headers = {
        "User-Agent": UserAgent().random,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }

    session = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    session.headers.update(headers)
    response = session.get(IDEALISTA_URL)

    error_status = load_error_status()

    if response.status_code == 403:
        logging.warning("⚠️ Error 403 — Idealista has blocked access")
        if error_status.get("last_error") != 403:
            send_telegram_notification(
                "🚨 <b>Error 403 detectado</b>\nIdealista ha bloqueado el acceso."
            )
            save_error_status(403)
        return []

    if response.status_code == 200 and error_status.get("last_error") == 403:
        save_error_status(None)

    if response.status_code != 200:
        logging.error(f"Error fetching page: {response.status_code}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    listings = []
    seen_listings = load_seen_listings()
    seen_set = set(seen_listings)

    pending_notifications = []

    for listing in soup.find_all("article", class_="item"):
        try:
            title_el = listing.find("a", class_="item-link")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = "https://www.idealista.com" + title_el["href"]

            description_el = listing.find("div", class_="description")
            description = description_el.get_text(strip=True) if description_el else ""

            price_el = listing.find("span", class_="item-price")
            price = (
                price_el.get_text(strip=True).split("€")[0].strip() + " €/mes"
                if price_el
                else "Precio no disponible"
            )

            details = listing.find_all("span", class_="item-detail")
            rooms = details[0].get_text(strip=True) if len(details) > 0 else "—"
            size = details[1].get_text(strip=True) if len(details) > 1 else "—"
            floor = details[2].get_text(strip=True) if len(details) > 2 else "—"

            # Apply filters
            if any(f.lower() in floor.lower() for f in EXCLUDED_FLOORS):
                continue
            if any(f.lower() in size.lower() for f in EXCLUDED_FLOORS):
                continue
            if any(
                a.lower() in title.lower() or a.lower() in description.lower()
                for a in EXCLUDED_AREAS
            ):
                continue
            if any(t.lower() in description.lower() for t in EXCLUDED_TERMS):
                continue

            # Skip already seen
            if link in seen_set:
                continue

            listings.append(link)
            seen_listings.append(link)
            seen_set.add(link)

            # Extract image URL
            image_url = None
            img_el = listing.find("img", class_="item-multimedia") or listing.find(
                "img"
            )
            if img_el:
                image_url = (
                    img_el.get("data-ondemand-img")
                    or img_el.get("data-src")
                    or img_el.get("src")
                )

            # Build message
            ATICO_TERMS = ["Atico", "Ático", "Atic"]
            is_atico = any(
                t.lower() in title.lower() or t.lower() in description.lower()
                for t in ATICO_TERMS
            )

            header = (
                "🚨 <b>ÁTICO DISPONIBLE</b> 🚨\n"
                if is_atico
                else "🏠 <b>Nuevo piso en alquiler en Sevilla</b>\n"
            )

            message = (
                f"{header}\n"
                f"<b>{title}</b>\n\n"
                f"💰 {price}\n"
                f"🛏 {rooms}\n"
                f"📐 {size}\n"
                f"🏢 {floor}\n\n"
                f'🔗 <a href="{link}">Ver anuncio</a>'
            )

            pending_notifications.append({"message": message, "image_url": image_url})

        except Exception as e:
            logging.debug(f"Error parsing listing: {e}")

    if pending_notifications:
        logging.debug(f"Sending {len(pending_notifications)} notifications...")
        for notif in pending_notifications:
            send_telegram_notification(notif["message"], image_url=notif["image_url"])
            time.sleep(0.5)
        logging.debug(f"✅ {len(pending_notifications)} notifications sent")

    save_seen_listings(seen_listings)
    return listings


if __name__ == "__main__":
    while True:
        try:
            new_listings = scrape_idealista()
            if new_listings:
                logging.debug(f"Found {len(new_listings)} new listings!")
            else:
                logging.debug("No new listings.")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")

        time.sleep(10800)  # Wait 3 hours before next scrape
