import requests
from bs4 import BeautifulSoup
import json
import os
import re
import hashlib
import schedule
import time
from datetime import datetime

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
AFFILIATE_TAG = "dealsinusa0ab-20"
OUTPUT_FILE   = os.path.expanduser("~/dealsinusa/deals.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Keywords that indicate Amazon deals in title
AMAZON_KEYWORDS = [
    "at amazon", "on amazon", "amazon.com",
    "w/ s&s", "w/ ss", "subscribe & save",
    "prime", "fulfilled by amazon"
]

# ─────────────────────────────
# HELPERS
# ─────────────────────────────
def make_id(title, url):
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()

def make_affiliate_link(asin):
    return f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}"

def clean_title(title):
    title = re.sub(r'&#\d+;', '', title)
    title = re.sub(r'&amp;', '&', title)
    title = re.sub(r'&quot;', '"', title)
    title = re.sub(r'&lt;', '<', title)
    title = re.sub(r'&gt;', '>', title)
    title = re.sub(r'&#039;', "'", title)
    title = re.sub(r'&039;', "'", title)
    return title.strip()

def is_amazon_deal(title, url, description=""):
    """Check if this deal is for an Amazon product."""
    text = f"{title} {url} {description}".lower()
    # Check for Amazon keywords
    for keyword in AMAZON_KEYWORDS:
        if keyword.lower() in text:
            return True
    # Check if URL contains amazon
    if "amazon" in url.lower():
        return True
    return False

def find_asin(url):
    """
    Aggressively find Amazon ASIN from any deal page URL.
    Returns ASIN string or None.
    """
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        r = session.get(url, timeout=15, allow_redirects=True)

        # Check if redirected directly to Amazon product page
        if "amazon.com" in r.url:
            asin = re.search(r'/dp/([A-Z0-9]{10})', r.url)
            if asin:
                return asin.group(1)

        # Search entire raw HTML for ASINs
        asins = re.findall(r'/dp/([A-Z0-9]{10})', r.text)
        if asins:
            return asins[0]

        # Search for ASIN in data attributes
        asins = re.findall(r'data-asin=\"([A-Z0-9]{10})\"', r.text)
        if asins:
            return asins[0]

        # Search for ASIN in JSON data
        asins = re.findall(r'\"asin\":\s*\"([A-Z0-9]{10})\"', r.text)
        if asins:
            return asins[0]

        # BeautifulSoup fallback — look for Amazon links
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "amazon.com" in href:
                asin = re.search(r'/dp/([A-Z0-9]{10})', href)
                if asin:
                    return asin.group(1)

    except Exception as e:
        print(f"    ⚠️  Error: {e}")
    return None

def load_existing():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r") as f:
            return json.load(f)
    return []

def save_deals(deals):
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(deals, f, indent=2)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💾 Saved {len(deals)} total deals")

# ─────────────────────────────
# SCRAPERS
# ─────────────────────────────
def parse_rss(url, source_name):
    """Generic RSS parser for all deal sources."""
    deals = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.find_all("item")[:50]:
            try:
                title = clean_title(
                    item.find("title").get_text(strip=True)
                )
                link  = item.find("link").get_text(strip=True)
                desc  = item.find("description")
                desc_text = desc.get_text() if desc else ""

                # Extract price from title or description
                price = ""
                m = re.search(r'\$[\d,]+(?:\.\d{2})?', title)
                if m:
                    price = m.group(0)
                if not price and desc_text:
                    m = re.search(r'\$[\d,]+(?:\.\d{2})?', desc_text)
                    if m:
                        price = m.group(0)

                # Only keep Amazon deals
                if not is_amazon_deal(title, link, desc_text):
                    continue

                if title and link:
                    deals.append({
                        "title": title,
                        "price": price or "See price",
                        "source_url": link,
                        "source": source_name,
                        "description": desc_text[:200] if desc_text else ""
                    })
            except:
                continue
        print(f"  📦 {source_name}: {len(deals)} Amazon deals found")
    except Exception as e:
        print(f"  ❌ {source_name} error: {e}")
    return deals

def scrape_slickdeals():
    return parse_rss(
        "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
        "Slickdeals"
    )

def scrape_dealsofamerica():
    return parse_rss(
        "https://www.dealsofamerica.com/rss.xml",
        "DealsOfAmerica"
    )

def scrape_techbargains():
    return parse_rss(
        "https://www.techbargains.com/rss.xml",
        "TechBargains"
    )

# ─────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────
def run():
    print(f"\n{'='*50}")
    print(f"🔄 Running at {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}")

    existing     = load_existing()
    existing_ids = {d["id"] for d in existing}

    # Scrape all sources — Amazon deals only
    all_deals = []
    all_deals += scrape_slickdeals()
    all_deals += scrape_dealsofamerica()
    all_deals += scrape_techbargains()

    new_count = sum(
        1 for d in all_deals
        if make_id(d['title'], d['source_url']) not in existing_ids
    )
    print(f"\n  🔎 Amazon deals scraped: {len(all_deals)} | New: {new_count}")

    # Counters
    direct   = 0
    skipped  = 0

    # Process only new deals
    new_deals = []
    for deal in all_deals:
        deal_id = make_id(deal["title"], deal["source_url"])
        if deal_id in existing_ids:
            continue

        print(f"\n  🔍 {deal['title'][:55]}")

        # Find ASIN
        asin = find_asin(deal["source_url"])

        if asin:
            amazon_url = make_affiliate_link(asin)
            print(f"  ✅ amazon.com/dp/{asin}")
            direct += 1
        else:
            # Skip deals where we can't find direct Amazon link
            print(f"  ⏭️  Skipped — no ASIN found")
            skipped += 1
            continue

        deal["id"]         = deal_id
        deal["amazon_url"] = amazon_url
        deal["asin"]       = asin
        deal["posted_at"]  = datetime.now().isoformat()
        deal["image"]      = ""
        # Remove description from saved data
        deal.pop("description", None)
        new_deals.append(deal)
        time.sleep(1)

    # Save results
    if new_deals:
        updated = new_deals + existing
        updated = updated[:200]
        save_deals(updated)
        print(f"\n{'='*50}")
        print(f"🎉 Added {len(new_deals)} new Amazon deals!")
        print(f"✅ Direct product links: {direct}")
        print(f"⏭️  Skipped (no ASIN): {skipped}")
        print(f"📊 Success rate: {int(direct/(direct+skipped)*100) if (direct+skipped) > 0 else 0}%")
        print(f"{'='*50}")
    else:
        print("\n  ℹ️  No new Amazon deals found this run.")

    print(f"\n⏰ Next check in 10 minutes...")

if __name__ == "__main__":
    print("🛍️  DealsInUSA Scraper Started!")
    print(f"🏷️  Affiliate tag: {AFFILIATE_TAG}")
    print(f"💾  Output file: {OUTPUT_FILE}")
    print(f"🎯  Mode: Amazon deals only")

    # Clear old deals for fresh start
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)
        print("🗑️  Cleared old deals for fresh start\n")

    run()
    schedule.every(10).minutes.do(run)
    while True:
        schedule.run_pending()
        time.sleep(30)
