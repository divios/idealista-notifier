#!/usr/bin/env python3
"""
Idealista Property Scraper - Simple Table Display
Scrapes property listings from Idealista and displays them in a formatted table.
"""

import cloudscraper
from bs4 import BeautifulSoup
import logging
from fake_useragent import UserAgent
import argparse
import time
import random
import unicodedata

logging.basicConfig(level=logging.ERROR, format='%(levelname)s - %(message)s')

def build_url(distrito, vecindario):
    """
    Build Idealista search URL with distrito and vecindario parameters
    
    Args:
        distrito: District name (e.g., 'barrio-de-salamanca')
        vecindario: Neighborhood name (e.g., 'goya')
    
    Returns:
        Complete Idealista search URL
    """
    return f"https://www.idealista.com/alquiler-viviendas/madrid/{distrito}/{vecindario}/con-sin-inquilinos,inquilino/?ordenado-por=fecha-publicacion-desc"

def build_url_with_page(distrito, vecindario, page=1):
    """
    Build Idealista search URL with page number
    
    Args:
        distrito: District name (e.g., 'barrio-de-salamanca')
        vecindario: Neighborhood name (e.g., 'goya')
        page: Page number (default: 1)
    
    Returns:
        Complete Idealista search URL with pagination
    """
    base = f"https://www.idealista.com/alquiler-viviendas/madrid/{distrito}/{vecindario}"
    if page == 1:
        return f"{base}/?ordenado-por=fecha-publicacion-desc"
    return f"{base}/pagina-{page}.htm?ordenado-por=fecha-publicacion-desc"

def extract_total_pages(soup):
    """
    Extract total number of pages from pagination section
    
    Args:
        soup: BeautifulSoup parsed HTML
    
    Returns:
        Maximum page number found in pagination (defaults to 1)
    """
    try:
        pagination = soup.find("div", class_="pagination")
        if not pagination:
            return 1
        
        # Find all page links in pagination
        page_links = pagination.find_all("a")
        max_page = 1
        
        for link in page_links:
            text = link.get_text(strip=True)
            # Extract numbers from pagination links
            if text.isdigit():
                max_page = max(max_page, int(text))
        
        return max_page
    except Exception as e:
        logging.warning(f"Error extracting pagination: {e}")
        return 1

def shorten_link(url, max_length=50):
    """Shorten URL for better display in table"""
    if len(url) <= max_length:
        return url
    return url[:max_length-3] + "..."

def clean_price(price_str):
    """
    Convert price string to numeric value (handles rental format)
    
    Args:
        price_str: Price string (e.g., "1.200€/mes", "1.200 €/mes")
    
    Returns:
        Float price value or None if invalid
    """
    try:
        # Remove /mes, currency symbols, spaces, and dots used as thousands separator
        cleaned = price_str.replace('€', '').replace('/mes', '').replace('.', '').replace(' ', '').strip()
        return float(cleaned)
    except (ValueError, AttributeError):
        return None

def clean_size(size_str):
    """
    Convert size string to numeric value
    
    Args:
        size_str: Size string (e.g., "85 m²", "120m²")
    
    Returns:
        Float size value or None if invalid
    """
    try:
        # Remove m², spaces, and other non-numeric characters
        cleaned = size_str.replace('m²', '').replace('m2', '').replace(' ', '').strip()
        return float(cleaned)
    except (ValueError, AttributeError):
        return None

def calculate_pam(price_str, size_str):
    """
    Calculate PAM (Price per square meter)
    
    Args:
        price_str: Price string
        size_str: Size string
    
    Returns:
        Float PAM value or None if calculation not possible
    """
    price = clean_price(price_str)
    size = clean_size(size_str)
    
    if price and size and size > 0:
        return round(price / size, 2)
    return None

def scrape_idealista(url, max_retries=3):
    """
    Scrape property listings from Idealista with retry policy
    
    Args:
        url: Idealista search URL to scrape
        max_retries: Maximum number of retry attempts (default: 3)
    
    Returns:
        Tuple of (list of property dictionaries, total pages found in pagination)
    """
    for attempt in range(max_retries):
        try:
            # Add delay between retries (exponential backoff)
            if attempt > 0:
                wait_time = random.uniform(2, 5) * (attempt + 1)
                logging.info(f"⏳ Retry attempt {attempt + 1}/{max_retries}, waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
            
            # Log the URL being requested
            logging.info(f"🔗 Requesting URL: {url}")
            
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

            # Use cloudscraper to bypass Cloudflare protection
            session = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                }
            )
            session.headers.update(headers)
            
            response = session.get(url)

            # Handle 404 - Don't retry, page doesn't exist
            if response.status_code == 404:
                logging.error(f"❌ 404 Not Found - URL does not exist: {url}")
                return [], 1

            # Handle error responses with retry logic
            if response.status_code == 403:
                if attempt < max_retries - 1:
                    logging.warning(f"⚠️ 403 Forbidden on attempt {attempt + 1}, retrying...")
                    continue
                else:
                    logging.error("❌ Failed after max retries (403 Forbidden)")
                    return [], 1
            
            if response.status_code != 200:
                if attempt < max_retries - 1:
                    logging.warning(f"⚠️ Status {response.status_code} on attempt {attempt + 1}, retrying...")
                    continue
                else:
                    logging.error(f"❌ Failed after max retries (Status: {response.status_code})")
                    return [], 1

            # Parse HTML
            soup = BeautifulSoup(response.text, "html.parser")
            properties = []

            # Extract each listing
            for listing in soup.find_all("article", class_="item"):
                try:
                    # Extract title and link
                    title_element = listing.find("a", class_="item-link")
                    if not title_element:
                        continue
                        
                    title = title_element.get_text(strip=True)
                    link = "https://www.idealista.com" + title_element["href"]
                    short_link = shorten_link(link)

                    # Extract price
                    price_element = listing.find("span", class_="item-price")
                    price = price_element.get_text(strip=True) if price_element else "N/A"

                    # Extract details (rooms, size, floor)
                    details_elements = listing.find_all("span", class_="item-detail")
                    rooms = details_elements[0].get_text(strip=True) if len(details_elements) > 0 else "N/A"
                    size = details_elements[1].get_text(strip=True) if len(details_elements) > 1 else "N/A"
                    floor = details_elements[2].get_text(strip=True) if len(details_elements) > 2 else "N/A"

                    # Calculate PAM (Price per m²)
                    pam = calculate_pam(price, size)
                    pam_display = f"{pam:,.0f} €/m²" if pam else "N/A"

                    # Add to properties list
                    properties.append({
                        "Título": title,
                        "Precio": price,
                        "Habitaciones": rooms,
                        "m²": size,
                        "€/m²": pam_display,
                        "Piso": floor,
                        "Link": short_link,
                        "_pam_value": pam  # Store numeric value for PPAM calculation
                    })

                except Exception as e:
                    logging.warning(f"Error parsing listing: {e}")
                    continue

            # Extract total pages from pagination
            total_pages = extract_total_pages(soup)
            
            # Success! Return the results
            return properties, total_pages
            
        except Exception as e:
            if attempt < max_retries - 1:
                logging.warning(f"⚠️ Error on attempt {attempt + 1}: {e}")
                continue
            else:
                logging.error(f"❌ Failed after max retries: {e}")
                return [], 1
    
    # Should not reach here, but return empty as fallback
    return [], 1

def scrape_all_pages(distrito, vecindario):
    """
    Scrape all pages dynamically - pagination info updates as we progress
    
    Args:
        distrito: District name
        vecindario: Neighborhood name
    
    Returns:
        Tuple of (all properties list, total pages scraped)
    """
    all_properties = []
    current_page = 1
    max_pages_seen = 1
    
    logging.info(f"🚀 Starting multi-page scraping for {distrito}/{vecindario}")
    
    while current_page <= max_pages_seen:
        # Build URL for current page
        url = build_url_with_page(distrito, vecindario, current_page)
        
        logging.info(f"📄 Scraping page {current_page}/{max_pages_seen}")
        
        # Scrape current page
        properties, new_max_pages = scrape_idealista(url)
        
        # Update max pages if we found more
        if new_max_pages > max_pages_seen:
            max_pages_seen = new_max_pages
            logging.info(f"🔍 Total pages updated to: {max_pages_seen}")
        
        # If no properties found, we've likely reached the end
        if not properties:
            logging.info(f"❌ No properties found on page {current_page}. Stopping.")
            break
        
        # Add properties to collection
        all_properties.extend(properties)
        logging.info(f"✅ Found {len(properties)} properties on page {current_page} (Total so far: {len(all_properties)})")
        
        # Move to next page
        current_page += 1
    
    return all_properties, max_pages_seen

def normalize_location_for_url(location_text):
    """
    Convert human-readable location to URL-friendly format
    Removes accents and special characters
    
    Args:
        location_text: Human-readable location name (e.g., "Barrio de Salamanca", "Águilas")
    
    Returns:
        URL-friendly string (e.g., "barrio-de-salamanca", "aguilas") or None if invalid
    
    Examples:
        "Barrio de Salamanca" -> "barrio-de-salamanca"
        "Águilas" -> "aguilas"
        "Móstoles" -> "mostoles"
        "Alcorcón" -> "alcorcon"
    """
    if not location_text:
        return None
    
    # Remove accents/diacritics using Unicode normalization
    nfd = unicodedata.normalize('NFD', location_text)
    without_accents = ''.join(char for char in nfd if unicodedata.category(char) != 'Mn')
    
    # Convert to lowercase and replace spaces with hyphens
    return without_accents.lower().replace(' ', '-').strip()

def get_property_metrics(distrito, vecindario, area):
    """
    Get property metrics (PPAM, PEA, PBN) for a specific location
    
    Args:
        distrito: District name in URL-friendly format (e.g., 'barrio-de-salamanca')
        vecindario: Neighborhood name in URL-friendly format (e.g., 'goya')
        area: Target area in m² (float)
    
    Returns:
        Tuple (ppam, pea, pbn) with calculated metrics, or (None, None, None) if calculation fails
        
    Example:
        >>> ppam, pea, pbn = get_property_metrics('barrio-de-salamanca', 'goya', 80.0)
        >>> print(f"PPAM: {ppam}, PEA: {pea}, PBN: {pbn}")
    """
    try:
        # Scrape all pages for this location
        properties, _ = scrape_all_pages(distrito, vecindario)
        
        if not properties:
            logging.warning(f"No properties found for {distrito}/{vecindario}")
            return None, None, None
        
        # Calculate PPAM (average price per m²)
        valid_pams = [prop['_pam_value'] for prop in properties if prop['_pam_value'] is not None]
        
        if not valid_pams:
            logging.warning(f"No valid PAM values found for {distrito}/{vecindario}")
            return None, None, None
        
        ppam = sum(valid_pams) / len(valid_pams)
        pea = ppam * area
        pbn = (pea * 100) / 0.6
        
        logging.info(f"✅ Metrics calculated: PPAM={ppam:.2f}, PEA={pea:.2f}, PBN={pbn:.2f}")
        return round(ppam, 2), round(pea, 2), round(pbn, 2)
        
    except Exception as e:
        logging.error(f"Error calculating metrics for {distrito}/{vecindario}: {e}")
        return None, None, None

def display_properties_table(properties, area):
    """Calculate and display PPAM, PEA, and PBN in simple format"""
    if not properties:
        return
    
    # Calculate PPAM (average price per m²)
    valid_pams = [prop['_pam_value'] for prop in properties if prop['_pam_value'] is not None]
    
    if valid_pams:
        ppam = sum(valid_pams) / len(valid_pams)
        pea = ppam * area
        pbn = (pea * 100) / 0.6
        
        # Print header and values
        print("PPAM|PEA|PBN")
        print(f"{ppam:.2f}|{pea:.2f}|{pbn:.2f}")

def main():
    """Main execution function"""
    # Set up argument parser
    parser = argparse.ArgumentParser(
        description='Scrape property listings from Idealista and display them in a formatted table.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --distrito barrio-de-salamanca --vecindario goya
  %(prog)s --distrito chamberi --vecindario trafalgar
  %(prog)s --distrito retiro --vecindario jeronimos
        """
    )
    
    parser.add_argument(
        '--distrito',
        type=str,
        required=True,
        help='District name (e.g., "barrio-de-salamanca", "chamberi")'
    )
    
    parser.add_argument(
        '--vecindario',
        type=str,
        required=True,
        help='Neighborhood name (e.g., "goya", "trafalgar")'
    )
    
    parser.add_argument(
        '--area',
        type=float,
        required=True,
        help='Target area in m² for PEA and PBN calculations (e.g., 80, 120)'
    )
    
    # Parse arguments
    args = parser.parse_args()
    
    # Display search information
    print(f"\n🔍 Searching in: {args.distrito.upper()} - {args.vecindario.upper()}")
    
    # Scrape all pages dynamically
    properties, total_pages = scrape_all_pages(args.distrito, args.vecindario)
    
    # Display summary
    print(f"\n{'='*120}")
    print(f"📊 SCRAPING COMPLETE")
    print(f"{'='*120}")
    print(f"📄 Total pages scraped: {total_pages}")
    print(f"🏡 Total properties found: {len(properties)}")
    print(f"{'='*120}\n")
    
    # Display properties table
    display_properties_table(properties, area=args.area)

if __name__ == "__main__":
    main()
