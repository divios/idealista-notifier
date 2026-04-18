import re
import requests
from bs4 import BeautifulSoup
import json
import time
import os
from dotenv import load_dotenv
import logging
from collections import deque
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.DEBUG, format="|%(levelname)s| %(asctime)s - %(message)s"
)

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY")

INFLUXDB_URL = os.getenv("INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG", "")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "idealista")

# Search URLs — alquiler en Sevilla
IDEALISTA_URL = "https://www.idealista.com/alquiler-viviendas/sevilla-sevilla/?ordenado-por=fecha-publicacion-desc"
PISOS_URL = "https://www.pisos.com/alquiler/pisos-sevilla/"
FOTOCASA_URL = (
    "https://www.fotocasa.es/es/alquiler/casas/sevilla-capital/todas-las-zonas/l"
)
CASAS_URL = "https://www.casas.com/alquiler-pisos/sevilla/"

# Neighborhoods to exclude
EXCLUDED_AREAS = []

# Keywords to filter out in the description
EXCLUDED_TERMS = []

# Exclude unwanted floors
EXCLUDED_FLOORS = []

# Track seen listings to avoid duplicates
SEEN_LISTINGS_FILE = "/app/data/seen_listings.json"

# Max listings saved in deduplication queue
MAX_LISTINGS = 300

# Track if an error has already been notified
ERROR_LOG_FILE = "/app/data/error_log.json"

SCRAPERAPI_BASE = "http://api.scraperapi.com"


# ---------------------------------------------------------------------------
# InfluxDB metrics
# ---------------------------------------------------------------------------


def publish_metrics(
    scraper: str, listings_new: int, duration_seconds: float, success: bool
):
    """
    Write a single data point to InfluxDB for a scraper run.
    Silently skipped if INFLUXDB_URL or INFLUXDB_TOKEN are not configured.

    Measurement: scraper_run
    Tags:        scraper=<name>
    Fields:
      listings_new        — number of new listings found
      duration_seconds    — wall-clock time the scraper took
      success             — 1 if completed without error, 0 otherwise
    """
    if not INFLUXDB_URL or not INFLUXDB_TOKEN:
        return

    try:
        from influxdb_client import InfluxDBClient, Point, WritePrecision
        from influxdb_client.client.write_api import SYNCHRONOUS

        point = (
            Point("scraper_run")
            .tag("scraper", scraper)
            .field("listings_new", listings_new)
            .field("duration_seconds", round(duration_seconds, 3))
            .field("success", 1 if success else 0)
            .time(datetime.now(timezone.utc), WritePrecision.SECONDS)
        )

        with InfluxDBClient(
            url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=INFLUXDB_BUCKET, record=point
            )

        logging.debug(f"InfluxDB: wrote metrics for {scraper}")

    except Exception as e:
        logging.warning(f"InfluxDB write failed (non-fatal): {e}")


def publish_run_summary(total_new: int, total_duration_seconds: float):
    """
    Write a summary point for the full scraping run.

    Measurement: run_summary
    Fields:
      total_new           — total new listings across all scrapers
      duration_seconds    — total wall-clock time for the full run
    """
    if not INFLUXDB_URL or not INFLUXDB_TOKEN:
        return

    try:
        from influxdb_client import InfluxDBClient, Point, WritePrecision
        from influxdb_client.client.write_api import SYNCHRONOUS

        point = (
            Point("run_summary")
            .field("total_new", total_new)
            .field("duration_seconds", round(total_duration_seconds, 3))
            .time(datetime.now(timezone.utc), WritePrecision.SECONDS)
        )

        with InfluxDBClient(
            url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=INFLUXDB_BUCKET, record=point
            )

        logging.debug("InfluxDB: wrote run_summary")

    except Exception as e:
        logging.warning(f"InfluxDB run_summary write failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Numeric parsers
# ---------------------------------------------------------------------------


def parse_price_eur(raw: str) -> float | None:
    """Extract a numeric monthly rent from strings like '1.200 €/mes', '950€', '1,200'."""
    if not raw:
        return None
    # Remove thousands separators (dot or space used in Spanish formatting)
    cleaned = raw.replace(".", "").replace("\xa0", "").replace(" ", "")
    match = re.search(r"(\d+(?:,\d+)?)", cleaned)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def parse_rooms(raw: str) -> int | None:
    """Extract room count from strings like '3 hab.', '2 habitaciones', '4 rooms'."""
    if not raw:
        return None
    match = re.search(r"(\d+)", raw)
    return int(match.group(1)) if match else None


def parse_size_m2(raw: str) -> float | None:
    """Extract square metres from strings like '75 m²', '90m2', '120 metros'."""
    if not raw:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*m", raw, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def publish_listing_metrics(scraper: str, listing: dict):
    """
    Write one data point per new listing to InfluxDB.
    Silently skipped if INFLUXDB_URL or INFLUXDB_TOKEN are not configured.

    Measurement: listing
    Tags:        scraper=<name>, location=<barrio>
    Fields:
      price_eur   — monthly rent in euros (float, optional)
      rooms       — number of rooms (int, optional)
      size_m2     — surface in square metres (float, optional)
      link        — listing URL (string)
    """
    if not INFLUXDB_URL or not INFLUXDB_TOKEN:
        return

    try:
        from influxdb_client import InfluxDBClient, Point, WritePrecision
        from influxdb_client.client.write_api import SYNCHRONOUS

        point = (
            Point("listing")
            .tag("scraper", scraper)
            .tag("location", listing.get("location") or "unknown")
            .field("link", listing.get("link", ""))
            .time(datetime.now(timezone.utc), WritePrecision.SECONDS)
        )

        price_eur = parse_price_eur(listing.get("price_raw", ""))
        if price_eur is not None:
            point = point.field("price_eur", price_eur)

        rooms = parse_rooms(listing.get("rooms_raw", ""))
        if rooms is not None:
            point = point.field("rooms", rooms)

        size_m2 = parse_size_m2(listing.get("size_raw", ""))
        if size_m2 is not None:
            point = point.field("size_m2", size_m2)

        with InfluxDBClient(
            url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=INFLUXDB_BUCKET, record=point
            )

        logging.debug(
            f"InfluxDB: wrote listing metric for {scraper} — {listing.get('link', '')}"
        )

    except Exception as e:
        logging.warning(f"InfluxDB listing write failed (non-fatal): {e}")


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


def send_telegram_notification(text, image_url=None):
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


def fetch_via_scraperapi(url, render=False, retries=2):
    """
    Fetch a URL via ScraperAPI with optional JS rendering.
    Returns the response or None on failure.
    """
    params = {
        "api_key": SCRAPERAPI_KEY,
        "url": url,
        "country_code": "es",
        "render": "true" if render else "false",
    }

    for attempt in range(1, retries + 1):
        try:
            logging.debug(f"ScraperAPI fetch attempt {attempt}: {url}")
            response = requests.get(SCRAPERAPI_BASE, params=params, timeout=60)
            logging.debug(f"ScraperAPI response: {response.status_code}")
            if response.status_code == 200:
                return response
            else:
                logging.warning(
                    f"ScraperAPI returned {response.status_code}: {response.text[:200]}"
                )
        except Exception as e:
            logging.error(f"ScraperAPI request failed (attempt {attempt}): {e}")

        if attempt < retries:
            time.sleep(5)

    return None


# ---------------------------------------------------------------------------
# Idealista
# ---------------------------------------------------------------------------


def scrape_idealista(seen_listings, seen_set):
    logging.debug("Scraping Idealista...")

    response = fetch_via_scraperapi(IDEALISTA_URL, render=False)
    error_status = load_error_status()

    if response is None or response.status_code == 403:
        logging.warning("⚠️ Error 403 — Idealista has blocked access after all retries")
        if error_status.get("last_error") != 403:
            send_telegram_notification(
                "🚨 <b>Error 403 detectado</b>\nIdealista ha bloqueado el acceso."
            )
            save_error_status(403)
        return []

    if response.status_code == 200 and error_status.get("last_error") == 403:
        save_error_status(None)

    soup = BeautifulSoup(response.text, "html.parser")
    new_listings = []
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
            price_raw = price_el.get_text(strip=True) if price_el else ""
            price = (
                price_raw.split("€")[0].strip() + " €/mes"
                if price_raw
                else "Precio no disponible"
            )

            details = listing.find_all("span", class_="item-detail")
            rooms_raw = details[0].get_text(strip=True) if len(details) > 0 else ""
            size_raw = details[1].get_text(strip=True) if len(details) > 1 else ""
            floor = details[2].get_text(strip=True) if len(details) > 2 else "—"

            rooms = rooms_raw or "—"
            size = size_raw or "—"

            # Location: dedicated span or fall back to title
            location_el = listing.find(
                "span", class_="item-detail-location"
            ) or listing.find("p", class_="item-detail-location")
            location = (
                location_el.get_text(strip=True)
                if location_el
                else title.split(",")[-1].strip()
            )

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

            if link in seen_set:
                continue

            new_listings.append(
                {
                    "link": link,
                    "price_raw": price_raw,
                    "rooms_raw": rooms_raw,
                    "size_raw": size_raw,
                    "location": location,
                }
            )
            seen_listings.append(link)
            seen_set.add(link)

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

            ATICO_TERMS = ["Atico", "Ático", "Atic"]
            is_atico = any(
                t.lower() in title.lower() or t.lower() in description.lower()
                for t in ATICO_TERMS
            )

            header = (
                "🚨 <b>ÁTICO DISPONIBLE</b> 🚨\n"
                if is_atico
                else "🏠 <b>Nuevo piso en alquiler (Idealista)</b>\n"
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
            logging.debug(f"Error parsing Idealista listing: {e}")

    if pending_notifications:
        logging.debug(
            f"Sending {len(pending_notifications)} Idealista notifications..."
        )
        for notif in pending_notifications:
            send_telegram_notification(notif["message"], image_url=notif["image_url"])
            time.sleep(0.5)

    return new_listings


# ---------------------------------------------------------------------------
# Pisos.com
# ---------------------------------------------------------------------------


def scrape_pisos(seen_listings, seen_set):
    logging.debug("Scraping Pisos.com...")

    response = fetch_via_scraperapi(PISOS_URL, render=False)
    if response is None:
        logging.warning("Failed to fetch Pisos.com")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    new_listings = []
    pending_notifications = []

    for listing in soup.find_all("div", class_="ad-preview"):
        try:
            relative_url = listing.get("data-lnk-href", "")
            if not relative_url:
                continue

            link = "https://www.pisos.com" + relative_url

            if link in seen_set:
                continue

            # Title
            title_el = listing.find("a", class_="ad-preview__title")
            title = title_el.get_text(strip=True) if title_el else "Sin título"

            # Location — subtitle paragraph
            subtitle_el = listing.find("p", class_="ad-preview__subtitle")
            location = subtitle_el.get_text(strip=True) if subtitle_el else "Sevilla"

            # Price — strip the /mes child span
            price_raw = ""
            price_el = listing.find("span", class_="ad-preview__price")
            if price_el:
                for child in price_el.find_all("span"):
                    child.decompose()
                price_raw = price_el.get_text(strip=True)

            # Details: rooms, size, floor — each in <p class="ad-preview__char ...">
            details = listing.find_all("p", class_="ad-preview__char")
            rooms_raw = details[0].get_text(strip=True) if len(details) > 0 else ""
            size_raw = details[1].get_text(strip=True) if len(details) > 1 else ""
            floor = details[2].get_text(strip=True) if len(details) > 2 else "—"

            # Image
            image_url = None
            carousel = listing.find(class_="carousel__main-photo")
            if carousel:
                img_el = carousel.find("img") if hasattr(carousel, "find") else None
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src")

            new_listings.append(
                {
                    "link": link,
                    "price_raw": price_raw,
                    "rooms_raw": rooms_raw,
                    "size_raw": size_raw,
                    "location": location,
                }
            )
            seen_listings.append(link)
            seen_set.add(link)

            message = (
                f"🏠 <b>Nuevo piso en alquiler (Pisos.com)</b>\n\n"
                f"<b>{title}</b>\n"
                f"📍 {location}\n\n"
                f"💰 {price_raw or 'Precio no disponible'}\n"
                f"🛏 {rooms_raw or '—'}\n"
                f"📐 {size_raw or '—'}\n"
                f"🏢 {floor}\n\n"
                f'🔗 <a href="{link}">Ver anuncio</a>'
            )

            pending_notifications.append({"message": message, "image_url": image_url})

        except Exception as e:
            logging.debug(f"Error parsing Pisos.com listing: {e}")

    if pending_notifications:
        logging.debug(
            f"Sending {len(pending_notifications)} Pisos.com notifications..."
        )
        for notif in pending_notifications:
            send_telegram_notification(notif["message"], image_url=notif["image_url"])
            time.sleep(0.5)

    return new_listings


# ---------------------------------------------------------------------------
# Fotocasa
# ---------------------------------------------------------------------------


def scrape_fotocasa(seen_listings, seen_set):
    logging.debug("Scraping Fotocasa...")

    # Use render=true so ScraperAPI executes the React SPA and returns full HTML
    response = fetch_via_scraperapi(FOTOCASA_URL, render=True)
    if response is None:
        logging.warning("Failed to fetch Fotocasa")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    new_listings = []
    pending_notifications = []

    for listing in soup.find_all("article"):
        try:
            link_el = listing.find("a", href=lambda h: h and "/es/alquiler/" in h)
            if not link_el:
                continue

            href = link_el.get("href", "")
            link = "https://www.fotocasa.es" + href if href.startswith("/") else href

            if link in seen_set:
                continue

            title_el = listing.find("h3") or listing.find("h2") or link_el
            title = title_el.get_text(strip=True) if title_el else "Sin título"

            # Location — subtitle or address span
            location_el = listing.find(
                class_=lambda c: (
                    c
                    and (
                        "location" in c.lower()
                        or "address" in c.lower()
                        or "subtitle" in c.lower()
                    )
                )
            )
            location = location_el.get_text(strip=True) if location_el else "Sevilla"

            price_raw = ""
            price_el = listing.find(class_=lambda c: c and "price" in c.lower())
            if price_el:
                price_raw = price_el.get_text(strip=True)

            detail_els = listing.find_all(
                lambda tag: (
                    tag.name in ("span", "li")
                    and tag.get("class")
                    and any(
                        "feature" in c or "detail" in c or "char" in c
                        for c in tag.get("class", [])
                    )
                )
            )
            rooms_raw = (
                detail_els[0].get_text(strip=True) if len(detail_els) > 0 else ""
            )
            size_raw = detail_els[1].get_text(strip=True) if len(detail_els) > 1 else ""
            floor = detail_els[2].get_text(strip=True) if len(detail_els) > 2 else "—"

            image_url = None
            img_el = listing.find("img")
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src")

            new_listings.append(
                {
                    "link": link,
                    "price_raw": price_raw,
                    "rooms_raw": rooms_raw,
                    "size_raw": size_raw,
                    "location": location,
                }
            )
            seen_listings.append(link)
            seen_set.add(link)

            message = (
                f"🏠 <b>Nuevo piso en alquiler (Fotocasa)</b>\n\n"
                f"<b>{title}</b>\n"
                f"📍 {location}\n\n"
                f"💰 {price_raw or 'Precio no disponible'}\n"
                f"🛏 {rooms_raw or '—'}\n"
                f"📐 {size_raw or '—'}\n"
                f"🏢 {floor}\n\n"
                f'🔗 <a href="{link}">Ver anuncio</a>'
            )

            pending_notifications.append({"message": message, "image_url": image_url})

        except Exception as e:
            logging.debug(f"Error parsing Fotocasa listing: {e}")

    if pending_notifications:
        logging.debug(f"Sending {len(pending_notifications)} Fotocasa notifications...")
        for notif in pending_notifications:
            send_telegram_notification(notif["message"], image_url=notif["image_url"])
            time.sleep(0.5)

    return new_listings


# ---------------------------------------------------------------------------
# Casas.com (silent-fail)
# ---------------------------------------------------------------------------


def scrape_casas(seen_listings, seen_set):
    logging.debug("Scraping Casas.com...")

    try:
        response = fetch_via_scraperapi(CASAS_URL, render=False, retries=2)
        if response is None:
            logging.warning("Casas.com: failed after retries — skipping silently")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        new_listings = []
        pending_notifications = []

        # Casas.com listing cards — typically <article> or <div> with listing data
        for listing in soup.find_all("article"):
            try:
                link_el = listing.find("a", href=True)
                if not link_el:
                    continue

                href = link_el.get("href", "")
                if href.startswith("/"):
                    link = "https://www.casas.com" + href
                elif href.startswith("http"):
                    link = href
                else:
                    continue

                if link in seen_set:
                    continue

                title_el = listing.find("h2") or listing.find("h3") or link_el
                title = title_el.get_text(strip=True) if title_el else "Sin título"

                location_el = listing.find(
                    class_=lambda c: (
                        c
                        and (
                            "location" in c.lower()
                            or "address" in c.lower()
                            or "subtitle" in c.lower()
                        )
                    )
                )
                location = (
                    location_el.get_text(strip=True) if location_el else "Sevilla"
                )

                price_raw = ""
                price_el = listing.find(class_=lambda c: c and "price" in c.lower())
                if price_el:
                    price_raw = price_el.get_text(strip=True)

                detail_els = listing.find_all(
                    lambda tag: (
                        tag.name in ("span", "li")
                        and tag.get("class")
                        and any(
                            "feature" in c or "detail" in c or "char" in c
                            for c in tag.get("class", [])
                        )
                    )
                )
                rooms_raw = (
                    detail_els[0].get_text(strip=True) if len(detail_els) > 0 else ""
                )
                size_raw = (
                    detail_els[1].get_text(strip=True) if len(detail_els) > 1 else ""
                )
                floor = (
                    detail_els[2].get_text(strip=True) if len(detail_els) > 2 else "—"
                )

                image_url = None
                img_el = listing.find("img")
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src")

                new_listings.append(
                    {
                        "link": link,
                        "price_raw": price_raw,
                        "rooms_raw": rooms_raw,
                        "size_raw": size_raw,
                        "location": location,
                    }
                )
                seen_listings.append(link)
                seen_set.add(link)

                message = (
                    f"🏠 <b>Nuevo piso en alquiler (Casas.com)</b>\n\n"
                    f"<b>{title}</b>\n"
                    f"📍 {location}\n\n"
                    f"💰 {price_raw or 'Precio no disponible'}\n"
                    f"🛏 {rooms_raw or '—'}\n"
                    f"📐 {size_raw or '—'}\n"
                    f"🏢 {floor}\n\n"
                    f'🔗 <a href="{link}">Ver anuncio</a>'
                )

                pending_notifications.append(
                    {"message": message, "image_url": image_url}
                )

            except Exception as e:
                logging.debug(f"Error parsing Casas.com listing: {e}")

        if pending_notifications:
            logging.debug(
                f"Sending {len(pending_notifications)} Casas.com notifications..."
            )
            for notif in pending_notifications:
                send_telegram_notification(
                    notif["message"], image_url=notif["image_url"]
                )
                time.sleep(0.5)

        return new_listings

    except Exception as e:
        logging.warning(f"Casas.com scraper failed — skipping silently: {e}")
        return []


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    while True:
        try:
            # Load seen listings once — shared across all scrapers
            seen_listings = load_seen_listings()
            seen_set = set(seen_listings)

            total_new = 0
            run_start = time.monotonic()

            for scraper_fn, name in [
                (scrape_idealista, "idealista"),
                (scrape_pisos, "pisos"),
                (scrape_fotocasa, "fotocasa"),
                (scrape_casas, "casas"),
            ]:
                t0 = time.monotonic()
                success = True
                new = []
                try:
                    new = scraper_fn(seen_listings, seen_set)
                    total_new += len(new)
                    logging.debug(f"{name}: {len(new)} new listings")
                    for listing_data in new:
                        publish_listing_metrics(scraper=name, listing=listing_data)
                except Exception as e:
                    success = False
                    logging.error(f"Unhandled error in {name} scraper: {e}")
                finally:
                    publish_metrics(
                        scraper=name,
                        listings_new=len(new),
                        duration_seconds=time.monotonic() - t0,
                        success=success,
                    )

            # Save once after all scrapers
            save_seen_listings(seen_listings)

            run_duration = time.monotonic() - run_start
            publish_run_summary(
                total_new=total_new, total_duration_seconds=run_duration
            )

            if total_new:
                logging.debug(f"Total new listings this run: {total_new}")
            else:
                logging.debug("No new listings found.")

        except Exception as e:
            logging.error(f"Unexpected error in main loop: {e}")

        time.sleep(10800)  # Wait 3 hours before next scrape
