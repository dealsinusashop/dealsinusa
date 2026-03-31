import requests
from bs4 import BeautifulSoup
import json
import os
import re
import hashlib
import time
import subprocess
from datetime import datetime

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
AFFILIATE_TAG = "dealsinusa0ab-20"
OUTPUT_FILE   = os.path.expanduser("~/dealsinusa/deals.json")
REPO_DIR      = os.path.expanduser("~/dealsinusa")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

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
    text = f"{title} {url} {description}".lower()
    for keyword in AMAZON_KEYWORDS:
        if keyword.lower() in text:
            return True
    if "amazon" in url.lower():
        return True
    return False

def extract_asin_from_url(url):
    m = re.search(r'/dp/([A-Z0-9]{10})', url)
    if m:
        return m.group(1)
    m = re.search(r'/gp/product/([A-Z0-9]{10})', url)
    if m:
        return m.group(1)
    return None

def find_asin(url):
    asin = extract_asin_from_url(url)
    if asin:
        return asin
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        r = session.get(url, timeout=15, allow_redirects=True)
        asin = extract_asin_from_url(r.url)
        if asin:
            return asin
        asins = re.findall(r'/dp/([A-Z0-9]{10})', r.text)
        if asins:
            return asins[0]
        asins = re.findall(r'/gp/product/([A-Z0-9]{10})', r.text)
        if asins:
            return asins[0]
        asins = re.findall(r'data-asin=["\']([A-Z0-9]{10})["\']', r.text)
        if asins:
            return asins[0]
        asins = re.findall(r'"asin":\s*"([A-Z0-9]{10})"', r.text)
        if asins:
            return asins[0]
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "amazon.com" in href:
                asin = extract_asin_from_url(href)
                if asin:
                    return asin
    except Exception as e:
        print(f"    ⚠️  Error: {e}")
    return None

def extract_image(item):
    """
    Extract image URL from RSS item.
    Checks content:encoded (Slickdeals) and description (TechBargains).
    """
    # 1. Try content:encoded — Slickdeals puts image here
    content = item.find("content:encoded") or item.find("encoded")
    if content:
        text = content.get_text() if hasattr(content, 'get_text') else str(content)
        m = re.search(r'src=["\']?(https://static\.slickdealscdn\.com[^"\'>\s]+)', text)
        if m:
            return m.group(1)
        m = re.search(r'src=["\']?(https://[^"\'>\s]+\.(?:jpg|jpeg|png|webp)[^"\'>\s]*)', text, re.I)
        if m:
            return m.group(1)

    # 2. Try description field — TechBargains puts image here as encoded HTML
    desc = item.find("description")
    if desc:
        text = desc.get_text() if hasattr(desc, 'get_text') else str(desc)
        # TechBargains image pattern
        m = re.search(r"src='(https://www\.techbargains\.com/imagery/[^']+)'", text)
        if m:
            return m.group(1)
        m = re.search(r'src=["\']?(https://www\.techbargains\.com/imagery/[^"\'>\s]+)', text)
        if m:
            return m.group(1)
        # Generic image fallback in description
        m = re.search(r'src=["\']?(https://[^"\'>\s]+\.(?:jpg|jpeg|png|webp)[^"\'>\s]*)', text, re.I)
        if m:
            url = m.group(1)
            # Skip placeholder/icon images
            if 'placeholder' not in url.lower() and 'icon' not in url.lower():
                return url

    # 3. Try enclosure tag
    enclosure = item.find("enclosure")
    if enclosure and enclosure.get("url"):
        return enclosure["url"]

    return ""

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
    push_to_github()

def push_to_github():
    try:
        # Stash any unstaged changes so pull --rebase doesn't error out
        stash_result = subprocess.run(
            ["git", "-C", REPO_DIR, "stash"],
            capture_output=True, text=True
        )
        stashed = "No local changes" not in stash_result.stdout

        # Stage deals.json
        r = subprocess.run(
            ["git", "-C", REPO_DIR, "add", "deals.json"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️  git add failed:\n{r.stderr.strip()}")
            return

        # Check if there's anything to commit
        result = subprocess.run(
            ["git", "-C", REPO_DIR, "diff", "--cached", "--quiet"],
            capture_output=True
        )
        if result.returncode == 0:
            if stashed:
                subprocess.run(["git", "-C", REPO_DIR, "stash", "pop"], capture_output=True)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ℹ️  No new deals to push")
            return

        # Commit
        r = subprocess.run(
            ["git", "-C", REPO_DIR, "commit", "-m",
             f"Update deals {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️  git commit failed:\n{r.stderr.strip()}")
            if stashed:
                subprocess.run(["git", "-C", REPO_DIR, "stash", "pop"], capture_output=True)
            return

        # Pull remote changes (rebase keeps history clean)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Syncing with remote...")
        r = subprocess.run(
            ["git", "-C", REPO_DIR, "pull", "--rebase", "origin", "main"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️  git pull --rebase failed:\n{r.stderr.strip()}")
            subprocess.run(["git", "-C", REPO_DIR, "rebase", "--abort"], capture_output=True)
            if stashed:
                subprocess.run(["git", "-C", REPO_DIR, "stash", "pop"], capture_output=True)
            return

        # Restore any stashed changes
        if stashed:
            subprocess.run(["git", "-C", REPO_DIR, "stash", "pop"], capture_output=True)

        # Push
        r = subprocess.run(
            ["git", "-C", REPO_DIR, "push", "origin", "main"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️  git push failed!")
            print(f"  STDERR: {r.stderr.strip()}")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 Pushed to GitHub — website updated!")

    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️  Unexpected error during push: {e}")

# ─────────────────────────────
# SCRAPERS
# ─────────────────────────────
def parse_rss(url, source_name):
    deals = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.find_all("item")[:50]:
            try:
                title = clean_title(item.find("title").get_text(strip=True))
                link  = item.find("link").get_text(strip=True)
                desc  = item.find("description")
                desc_text = desc.get_text() if desc else ""

                # Extract image from RSS item
                image = extract_image(item)

                price = ""
                m = re.search(r'\$[\d,]+(?:\.\d{2})?', title)
                if m:
                    price = m.group(0)
                if not price and desc_text:
                    m = re.search(r'\$[\d,]+(?:\.\d{2})?', desc_text)
                    if m:
                        price = m.group(0)

                if not is_amazon_deal(title, link, desc_text):
                    continue

                if title and link:
                    deals.append({
                        "title": title,
                        "price": price or "See price",
                        "source_url": link,
                        "source": source_name,
                        "image": image,
                        "description": desc_text[:300] if desc_text else ""
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
    return parse_rss("https://www.dealsofamerica.com/rss.xml", "DealsOfAmerica")

def scrape_techbargains():
    return parse_rss("https://www.techbargains.com/rss.xml", "TechBargains")

# ─────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────
def run():
    print(f"\n{'='*50}")
    print(f"🔄 Running at {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*50}")

    existing     = load_existing()
    existing_ids = {d["id"] for d in existing}

    all_deals = []
    all_deals += scrape_slickdeals()
    all_deals += scrape_dealsofamerica()
    all_deals += scrape_techbargains()

    new_count = sum(1 for d in all_deals if make_id(d['title'], d['source_url']) not in existing_ids)
    print(f"\n  🔎 Amazon deals scraped: {len(all_deals)} | New: {new_count}")

    direct  = 0
    skipped = 0
    new_deals = []

    for deal in all_deals:
        deal_id = make_id(deal["title"], deal["source_url"])
        if deal_id in existing_ids:
            continue

        print(f"\n  🔍 {deal['title'][:55]}")
        asin = find_asin(deal["source_url"])

        if not asin:
            print(f"  ⏭️  Skipped — no ASIN found")
            skipped += 1
            continue

        amazon_url = make_affiliate_link(asin)
        print(f"  ✅ amazon.com/dp/{asin}")
        if deal.get('image'):
            print(f"  🖼️  Image: {deal['image'][:60]}")
        else:
            print(f"  🖼️  No image in RSS")

        deal["id"]         = deal_id
        deal["amazon_url"] = amazon_url
        deal["asin"]       = asin
        deal["posted_at"]  = datetime.now().isoformat()
        deal.pop("description", None)
        new_deals.append(deal)
        direct += 1
        time.sleep(1)

    if new_deals:
        updated = new_deals + existing
        updated = updated[:200]
        save_deals(updated)
        print(f"\n{'='*50}")
        print(f"🎉 Added {len(new_deals)} new Amazon deals!")
        print(f"✅ Direct product links: {direct}")
        print(f"⏭️  Skipped (no ASIN): {skipped}")
        print(f"🖼️  With images: {sum(1 for d in new_deals if d.get('image'))}")
        print(f"📊 Success rate: {int(direct/(direct+skipped)*100) if (direct+skipped) > 0 else 0}%")
        print(f"{'='*50}")
    else:
        print("\n  ℹ️  No new Amazon deals found this run.")

    print(f"\n⏰ Next check in 15 minutes...")

INTERVAL_MINUTES = 15

if __name__ == "__main__":
    print("🛍️  DealsInUSA Scraper Started!")
    print(f"🏷️  Affiliate tag: {AFFILIATE_TAG}")
    print(f"💾  Output file: {OUTPUT_FILE}")
    print(f"🎯  Mode: Amazon deals only")
    print(f"🚀  Auto-push to GitHub: enabled")
    print(f"🖼️  Images: Slickdeals CDN + TechBargains")
    print(f"⏱️  Interval: every {INTERVAL_MINUTES} minutes")

    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)
        print("🗑️  Cleared old deals for fresh start\n")

    while True:
        run()
        time.sleep(INTERVAL_MINUTES * 60)
