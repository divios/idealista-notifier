import requests
import cloudscraper
from bs4 import BeautifulSoup
import json
import time
import os
import sys
from dotenv import load_dotenv
import logging
from fake_useragent import UserAgent
import random
from collections import deque

# Import metrics functions from scrap_metrics.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrap_metrics import get_property_metrics, normalize_location_for_url, clean_size

logging.basicConfig(level=logging.DEBUG, format="|%(levelname)s| %(asctime)s - %(message)s")

# Load environment variables
load_dotenv()
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")

# Max price
MAX_PRICE = 250000

# Define the search URL with filters (modify as needed)
IDEALISTA_URL = f"https://www.idealista.com/areas/venta-viviendas/con-precio-hasta_{MAX_PRICE},sin-inquilinos,inquilino,publicado_ultimas-24-horas/" + r"?shape=((ksmvFlffWeaHu}NyUowr%40|tC}h]z}LapJbpT`]~pXqwDbnFnu~%40w_%40l~R}jO|jQu|RzyEw{RytA}eEvdG))&ordenado-por=fecha-publicacion-desc"
print(f"Using Idealista URL: {IDEALISTA_URL}")

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

def extract_location_details(session, property_url, max_retries=3):
    """
    Fetch property detail page and extract neighborhood and district
    Retries up to max_retries times if blocked (403)
    Returns: (neighborhood, district) tuple or (None, None) if not found
    """
    for attempt in range(max_retries):
        try:
            # Add random delay to avoid detection
            time.sleep(random.uniform(1, 3))
            
            # Rotate User-Agent on retry attempts
            if attempt > 0:
                session.headers.update({"User-Agent": UserAgent().random})
                logging.debug(f"Retry attempt {attempt + 1}/{max_retries} with new User-Agent")
                # Exponential backoff: wait longer between retries
                wait_time = random.uniform(2, 5) * (attempt + 1)
                logging.debug(f"Waiting {wait_time:.1f}s before retry...")
                time.sleep(wait_time)
            
            response = session.get(property_url, timeout=10)
            
            # Handle 403 - Retry
            if response.status_code == 403:
                logging.warning(f"⚠️ 403 Forbidden on attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    continue  # Try again
                else:
                    logging.error(f"❌ Failed after {max_retries} attempts (403)")
                    return None, None
            
            # Other errors - Don't retry
            if response.status_code != 200:
                logging.warning(f"Failed to fetch detail page: {response.status_code}")
                return None, None
            
            # Parse location data
            soup = BeautifulSoup(response.text, "html.parser")
            header_map = soup.find("div", id="headerMap")
            
            if not header_map:
                logging.warning("headerMap div not found")
                return None, None
            
            location_items = header_map.find_all("li", class_="header-map-list")
            
            if len(location_items) >= 3:
                neighborhood = location_items[1].get_text(strip=True)  # Second item
                district = location_items[2].get_text(strip=True)      # Third item
                
                # Remove "Barrio " or "Distrito " prefix from neighborhood
                if neighborhood.startswith("Barrio "):
                    neighborhood = neighborhood.replace("Barrio ", "", 1)
                elif neighborhood.startswith("Distrito "):
                    neighborhood = neighborhood.replace("Distrito ", "", 1)
                
                # Remove "Distrito " prefix from district
                if district.startswith("Distrito "):
                    district = district.replace("Distrito ", "", 1)
                
                logging.debug(f"✅ Extracted - Neighborhood: {neighborhood}, District: {district}")
                return neighborhood, district
            else:
                logging.warning(f"Not enough location items found: {len(location_items)}")
                return None, None
                
        except Exception as e:
            logging.error(f"Error extracting location details (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                continue  # Try again on exception
            return None, None
    
    return None, None

def extract_numeric_price(price_string):
    """
    Extract numeric value from price string
    Example: "1,200 €" -> 1200.0
    Returns: float or None if extraction fails
    """
    try:
        # Remove currency symbols and spaces
        price_cleaned = price_string.replace("€", "").replace(".", "").replace(",", "").strip()
        return float(price_cleaned)
    except (ValueError, AttributeError) as e:
        logging.warning(f"Failed to extract numeric price from '{price_string}': {e}")
        return None

def calculate_deal_quality(price, pbn):
    """
    Calculate deal quality by comparing price with PBN (Precio Bien Normalizado)
    Returns: (indicator_emoji, quality_text, percentage_diff)
    
    Deal Quality Thresholds:
    - Excellent: 20%+ below PBN
    - Good: 10-20% below PBN
    - Fair: ±10% of PBN
    - Above Market: 10-20% above PBN
    - Overpriced: 20%+ above PBN
    """
    if not price or not pbn or pbn == 0:
        return None, None, None
    
    # Calculate percentage difference: negative means below PBN (good deal)
    percentage_diff = ((price - pbn) / pbn) * 100
    
    if percentage_diff <= -20:
        return "🔥", "Excellent Deal!", percentage_diff
    elif percentage_diff <= -10:
        return "✅", "Good Deal", percentage_diff
    elif percentage_diff <= 10:
        return "👍", "Fair Price", percentage_diff
    elif percentage_diff <= 20:
        return "⚠️", "Above Market", percentage_diff
    else:
        return "❌", "Overpriced", percentage_diff

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

    # Accumulate notifications to send them all at once
    pending_notifications = []

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

                # Extract location details (neighborhood and district) from property page
                logging.debug(f"Extracting location details for: {link}")
                neighborhood, district = extract_location_details(session, link, max_retries=3)
                
                # Build location info for notification
                location_info = ""
                if neighborhood:
                    location_info += f"📍 {neighborhood}<br>"
                if district:
                    location_info += f"🏙️ {district}<br>"
                
                if location_info:
                    location_info += "<br>"  # Add spacing after location info

                # Calculate property metrics (PPAM, PEA, PBN) if location is available
                metrics_info = ""
                if neighborhood and district and size != "Not available":
                    try:
                        # Convert size string to numeric value
                        size_numeric = clean_size(size)
                        
                        if size_numeric and size_numeric > 0:
                            # Normalize location names for URL
                            distrito_url = normalize_location_for_url(district)
                            vecindario_url = normalize_location_for_url(neighborhood)
                            
                            if distrito_url and vecindario_url:
                                logging.debug(f"Calculating metrics for {distrito_url}/{vecindario_url} with size {size_numeric}m²")
                                
                                # Get property metrics (PPAM, PEA, PBN)
                                ppam, pea, pbn = get_property_metrics(distrito_url, vecindario_url, size_numeric)
                                
                                if ppam and pea and pbn:
                                    metrics_info = f"📊 <b>Market Metrics:</b><br>"
                                    metrics_info += f"• PPAM: {ppam:,.2f} €/m²<br>"
                                    metrics_info += f"• PEA: {pea:,.2f} €<br>"
                                    metrics_info += f"• PBN: {pbn:,.2f} €<br><br>"
                                    logging.debug(f"✅ Metrics added: PPAM={ppam}, PEA={pea}, PBN={pbn}")
                                    
                                    # Calculate deal quality
                                    price_numeric = extract_numeric_price(price)
                                    if price_numeric:
                                        emoji, quality_text, percentage_diff = calculate_deal_quality(price_numeric, pbn)
                                        if emoji and quality_text and percentage_diff is not None:
                                            # Format the percentage difference
                                            diff_sign = "+" if percentage_diff > 0 else ""
                                            metrics_info += f"💰 <b>Deal Analysis:</b> {price_numeric:,.0f} € ({diff_sign}{percentage_diff:.1f}% vs PBN)<br>"
                                            metrics_info += f"{emoji} <b>{quality_text}</b><br><br>"
                                            logging.debug(f"✅ Deal analysis: {quality_text} ({percentage_diff:.1f}%)")
                                    else:
                                        logging.warning(f"Could not extract numeric price from '{price}'")
                                else:
                                    logging.warning("Metrics calculation returned None values")
                            else:
                                logging.warning(f"Could not normalize location: {district}/{neighborhood}")
                        else:
                            logging.warning(f"Invalid size value: {size}")
                    except Exception as e:
                        logging.error(f"Error calculating metrics: {e}")
                        # Continue without metrics if calculation fails

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

                # Prepare notification for batch sending
                notification_title = "🏡 New Apartment Listing!"
                priority = 0
                if is_atico:
                    notification_title = "🚨 ATIC ALERT! 🚨"
                    priority = 1  # High priority for atico listings

                message = f"""<b>{title}</b><br><br>{location_info}{metrics_info}💰 {price}<br>🛏️ {rooms}<br>📐 {size}<br>🏢 {floor}<br><br>🔗 <a href="{link}">Click here to view</a>"""
                
                # Accumulate notification instead of sending immediately
                pending_notifications.append({
                    "message": message,
                    "title": notification_title,
                    "priority": priority,
                    "image_data": image_data
                })

        except Exception as e:
            logging.debug(f"Error parsing listing: {e}")
            print("Error parsing listing:", e)

    # Send all accumulated notifications at once
    if pending_notifications:
        logging.debug(f"Sending {len(pending_notifications)} accumulated notifications...")
        print(f"Sending {len(pending_notifications)} accumulated notifications...")
        
        for notification in pending_notifications:
            send_pushover_notification(
                notification["message"],
                title=notification["title"],
                priority=notification["priority"],
                image_data=notification["image_data"]
            )
            # Small delay to avoid rate limiting
            time.sleep(0.5)
        
        logging.debug(f"✅ All {len(pending_notifications)} notifications sent!")
        print(f"✅ All {len(pending_notifications)} notifications sent!")

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
        
        time.sleep(10800) # Wait for 3 hours before next scrape
