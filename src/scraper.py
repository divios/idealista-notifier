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

logging.basicConfig(level=logging.DEBUG, format="|%(levelname)s| %(asctime)s - %(message)s")

# Load environment variables
load_dotenv()
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")

# Max price
MAX_PRICE = 250000

# Define the search URL with filters (modify as needed)
IDEALISTA_URL = f"https://www.idealista.com/venta-viviendas/madrid-madrid/con-precio-hasta_{MAX_PRICE},sin-inquilinos,inquilino,publicado_ultimas-24-horas?ordenado-por=fecha-publicacion-desc"

# Neighborhoods to exclude
EXCLUDED_AREAS = [] #["Raval", "Gòtic", "Gotico", "Gótico", "Gotic", "Barceloneta", "Estudio"]

# Keywords to filter out
EXCLUDED_TERMS = [] #["Alquiler de temporada", "alquiler temporal", "estancia corta", "estudio"]

# Exclude unwanted floors
EXCLUDED_FLOORS = [] #["Entreplanta", "Planta 1ᵃ", "Bajo"]

# Track seen listings to avoid duplicates
SEEN_LISTINGS_FILE = "/app/data/seen_listings.json"

# Max listing saved
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

def download_image(session, image_url):
    """
    Download image from Idealista using the same session
    Returns image bytes or None if download fails
    """
    try:
        if not image_url:
            return None
        
        # Make sure URL is absolute
        if image_url.startswith("//"):
            image_url = "https:" + image_url
        elif image_url.startswith("/"):
            image_url = "https://www.idealista.com" + image_url
        
        logging.debug(f"Downloading image from: {image_url}")
        response = session.get(image_url, timeout=10)
        
        if response.status_code == 200:
            # Check if image size is reasonable (< 2.5 MB for Pushover limit)
            if len(response.content) > 2.5 * 1024 * 1024:
                logging.warning(f"Image too large: {len(response.content)} bytes")
                return None
            return response.content
        else:
            logging.warning(f"Failed to download image: {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"Error downloading image: {e}")
        return None

def send_pushover_notification(message, title="Idealista Notifier", priority=0, image_data=None):
    """
    Send notification via Pushover with optional image attachment
    priority: -2 (no notification), -1 (quiet), 0 (normal), 1 (high), 2 (emergency)
    image_data: bytes of image to attach (JPG, PNG, or GIF, max 2.5 MB)
    """
    data = {
        "token": PUSHOVER_API_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "message": message,
        "title": title,
        "priority": priority,
        "html": 1  # Enable HTML formatting
    }
    
    files = None
    if image_data:
        files = {"attachment": ("image.jpg", image_data, "image/jpeg")}
    
    try:
        response = requests.post("https://api.pushover.net/1/messages.json", data=data, files=files)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Failed to send Pushover notification: {e}")
        return False

# Error Handling:
def load_error_status():
    try:
        with open(ERROR_LOG_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {"last_error": None}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_error": None}

def save_error_status(error_code):
    """Save the last recorded error status."""
    os.makedirs(os.path.dirname(ERROR_LOG_FILE), exist_ok=True)
    with open(ERROR_LOG_FILE, "w") as f:
        json.dump({"error": error_code}, f)

# Main logic:
def scrape_idealista():
    logging.debug(f"Scraping Idealista...")
    print("Scraping Idealista...")

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

    # Use cloudscraper to bypass Cloudflare bot protection
    session = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'mobile': False
        }
    )
    session.headers.update(headers)
    response = session.get(IDEALISTA_URL)

    # Load last error status
    error_status = load_error_status()

    if response.status_code == 403:
        logging.debug(f"⚠️ Error 403 - Access Forbidden!")
        print("⚠️ Error 403 - Access Forbidden!")
        
        # Notify Pushover only if this error hasn't been sent yet
        if error_status.get("last_error") != 403:
            message = "🚨 <b>Error 403 Detected!</b><br>Idealista has blocked access."
            send_pushover_notification(message, title="Error 403 - Idealista Blocked", priority=1)
            save_error_status(403)  # Save the error state

        return []  # Stop scraping if blocked
    
    # If the response is successful, reset error log
    if response.status_code == 200 and error_status.get("last_error") == 403:
        save_error_status(None)  # Clear error status

    if response.status_code != 200:
        logging.debug(f"Error fetching page! Status code: {response.status_code}")
        print("Error fetching page! Status code:", response.status_code)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    listings = []
    seen_listings = load_seen_listings()
    seen_set = set(seen_listings)

    # Extract each listing
    for listing in soup.find_all("article", class_="item" if "item" in response.text else "listing-item"):  # Adjust class if needed
        try:
            # Title
            title = listing.find("a", class_="item-link").get_text(strip=True)
            link = "https://www.idealista.com" + listing.find("a", class_="item-link")["href"]

            # Description
            description = listing.find("div", class_="description").get_text(strip=True) if listing.find("div", class_="description") else "No description"

            # Extract price
            price_element = listing.find("span", class_="item-price")
            price = price_element.get_text(strip=True).split("€")[0].strip() + " €" if price_element else "No price available"

            # Extract size (m²) and floor
            # Extract item details (rooms, size, floor)
            details_elements = listing.find_all("span", class_="item-detail")

            rooms = details_elements[0].get_text(strip=True) if len(details_elements) > 0 else "Not available"
            size = details_elements[1].get_text(strip=True) if len(details_elements) > 1 else "Not available"
            floor = details_elements[2].get_text(strip=True) if len(details_elements) > 2 else "Not available"

            # Highlight Atico listings
            ATICO_TERMS = ["Atico", "Ático", "Atic"]
            is_atico = any(term.lower() in title.lower() or term.lower() in description.lower() for term in ATICO_TERMS)

            # Explude low floors
            if any(floor_term.lower() in floor.lower() for floor_term in EXCLUDED_FLOORS):
                continue

            # Explude low floors (sometimes the floor is extracted as the size)
            if any(floor_term.lower() in size.lower() for floor_term in EXCLUDED_FLOORS):
                continue

            # Skip if the listing is from an excluded area
            if any(area.lower() in title.lower() or area.lower() in description.lower() for area in EXCLUDED_AREAS):
                continue

            # Skip listing if description contains any excluded term
            if any(term.lower() in description.lower() for term in EXCLUDED_TERMS):
                continue

            # Check if it's a new listing
            if link not in seen_set:
                listings.append(link)
                seen_listings.append(link)
                seen_set.add(link)

                # Extract first image URL
                image_url = None
                try:
                    # Try to find the main property image
                    # Idealista uses various patterns: img with class "item-multimedia", or within picture elements
                    img_element = listing.find("img", class_="item-multimedia")
                    if not img_element:
                        # Alternative: find any img within the listing
                        img_element = listing.find("img")
                    
                    if img_element:
                        # Check for lazy-loaded images (data-src, data-ondemand-img, etc.)
                        image_url = (
                            img_element.get("data-ondemand-img") or
                            img_element.get("data-src") or
                            img_element.get("src")
                        )
                        logging.debug(f"Found image URL: {image_url}")
                except Exception as e:
                    logging.warning(f"Error extracting image URL: {e}")

                # Download the image
                image_data = None
                if image_url:
                    image_data = download_image(session, image_url)
                    if image_data:
                        logging.debug(f"Successfully downloaded image ({len(image_data)} bytes)")
                    else:
                        logging.warning("Failed to download image, sending notification without image")

                # Send Pushover notification
                notification_title = "🏡 New Apartment Listing!"
                priority = 0
                if is_atico:
                    notification_title = "🚨 ATIC ALERT! 🚨"
                    priority = 1  # High priority for atico listings

                message = f"""📍 <b>{title}</b><br><br>💰 {price}<br>🛏️ {rooms}<br>📐 {size}<br>🏢 {floor}<br><br>🔗 <a href="{link}">Click here to view</a>"""
                send_pushover_notification(message, title=notification_title, priority=priority, image_data=image_data)

        except Exception as e:
            logging.debug(f"Error parsing listing: {e}")
            print("Error parsing listing:", e)

    save_seen_listings(seen_listings)
    return listings

if __name__ == "__main__":
    while True:
        try:
            new_listings = scrape_idealista()
            if new_listings:
                logging.debug(f"Found {len(new_listings)} new listings!")
                print(f"Found {len(new_listings)} new listings!")
            else:
                logging.debug(f"No new listings.")
                print("No new listings.")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            print(f"Unexpected error: {e}")
        
        time.sleep(random.randint(1800, 3600))
