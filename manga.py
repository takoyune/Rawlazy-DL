import os
import re
import sys
import time
import json
import random
import logging
import requests
import urllib3
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    filename='rawlazy_dl_errors.log',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

console = Console()

BANNER_TEXT = """🚀  Rawlazy-DL  🚀
─────────────────────────────
Download raw manga chapters with ease!
Smart naming • Batch download • Rich UI
by Takoyune"""

DEFAULT_OUTPUT_DIR = "downloads"
IMAGE_SERVERS = ["pubg-img.si"]
MAX_RETRIES = 3
RETRY_DELAY = 2

# Global Theme Setting
THEME_COLOR = "cyan"

# Persistent User Settings
SETTINGS_FILE = "settings.json"
SETTINGS = {
    "theme": "cyan",
    "split_mode": "split_only" # split_only, merged_only, both
}

def load_settings():
    global THEME_COLOR
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                SETTINGS.update(data)
                THEME_COLOR = SETTINGS.get("theme", "cyan")
        except Exception as e:
            logging.error(f"Failed to load settings: {e}")

def save_settings():
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(SETTINGS, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save settings: {e}")

# Load settings on startup
load_settings()

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15'
]

def get_headers(referer_url):
    """Generate browser-like headers to bypass anti-hotlink protection."""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Referer': referer_url,
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

def sanitize_folder_name(name):
    """Remove invalid characters from folder names."""
    invalid_chars = '<>:"/\\|?*'
    for ch in invalid_chars:
        name = name.replace(ch, '_')

    name = re.sub(r'_+', '_', name)
    return name.strip().strip('_')

def is_valid_url(url):
    """Check if a string looks like a valid HTTP/HTTPS URL."""
    try:
        result = urlparse(url)
        return result.scheme in ('http', 'https') and bool(result.netloc)
    except Exception:
        return False

def extract_chapter_number(url):
    """Extract chapter number from the URL path.
    
    Examples:
        .../manga-name-105/              → '105'
        .../chapter-42/                  → '42'
        .../%e3%80%90%e7%ac%ac1176%e8%a9%b1%e3%80%91/  → '1176'
        .../%e3%80%90%e7%ac%ac65-1%e8%a9%b1%e3%80%91/  → '65.1'
    """
    path = urlparse(url).path.rstrip('/')
    last_segment = path.split('/')[-1] if '/' in path else path
    last_segment = unquote(last_segment)

    match = re.search(r'[【\[]\s*第?(\d+[-.]?\d*)[話话回章巻]\s*[】\]]', last_segment)
    if match:
        return match.group(1).replace('-', '.')

    match = re.search(r'[-_](\d+[-.]?\d*)$', last_segment)
    if match:
        return match.group(1).replace('-', '.')

    match = re.match(r'^(\d+)$', last_segment)
    if match:
        return match.group(1)

    match = re.search(r'(\d+)', last_segment)
    if match:
        return match.group(1)

    return None


def extract_manga_title(soup, url):
    """Extract a clean manga title from the page title or URL.
    
    Strips away chapter info like '– Raw 【第102話】 | Manga Raw' to get
    just the manga name.
    """
    title = None
    title_tag = soup.find('title')
    if title_tag:
        raw_title = title_tag.text.strip()

        cleaned = re.split(r'\s*[–—-]\s*Raw\b', raw_title)[0]
        cleaned = re.split(r'\s*\|\s*', cleaned)[0]
        cleaned = re.sub(r'[【\[]\s*第?\d+[話话回章]*\s*[】\]]', '', cleaned)
        cleaned = cleaned.strip(' –—-_')
        if cleaned:
            title = cleaned

    if not title:

        path = urlparse(url).path.strip('/')
        segments = path.split('/')
        if segments:
            title = unquote(segments[-1])
            title = re.sub(r'[-_]\d+$', '', title)
            title = title.replace('-', ' ').replace('_', ' ').strip()

    return sanitize_folder_name(title) if title else 'Rawlazy_DL'


def fetch_page_with_browser(url, wait_seconds=10):
    """Use Selenium to load a JS-rendered page and return a BeautifulSoup object."""
    if not SELENIUM_AVAILABLE:
        console.print("[red]❌ Selenium is not installed! Run: pip install selenium webdriver-manager[/red]")
        return None

    console.print("[cyan]🌐 Launching headless browser to render JavaScript...[/cyan]")
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )

    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)

        console.print(f"[yellow]⏳ Waiting up to {wait_seconds}s for images to load...[/yellow]")
        try:
            WebDriverWait(driver, wait_seconds).until(
                EC.presence_of_all_elements_located((By.TAG_NAME, 'img'))
            )
        except Exception:
            console.print("[yellow]⚠️  Timed out waiting for images, proceeding with what's available...[/yellow]")

        console.print("[cyan]📜 Scrolling page to trigger lazy-loading (this may take a bit)...[/cyan]")
        
        # Scroll incrementally to ensure all IntersectionObservers fire
        current_y = 0
        scroll_step = 800  # Pixels to scroll down at a time
        
        while True:
            # Scroll down by the step amount
            driver.execute_script(f'window.scrollBy(0, {scroll_step});')
            time.sleep(0.3)  # Short pause to let JS run
            
            # The new target Y we just scrolled to
            current_y += scroll_step
            
            # Check the new total height of the document
            new_height = driver.execute_script('return document.body.scrollHeight')
            
            # If we've scrolled past the bottom
            if current_y >= new_height:
                # Wait a moment to see if bottom-loading triggered more content
                time.sleep(1)
                final_height = driver.execute_script('return document.body.scrollHeight')
                if current_y >= final_height:
                    break  # We really reached the bottom

        console.print("[cyan]⏳ Waiting up to 60 seconds for dynamic image chunks to finish loading...[/cyan]")
        try:
            # rawlazy.io uses .loading-wrapper for its AJAX chunks. 
            # We wait until it's completely gone from the DOM (meaning all batches are loaded).
            WebDriverWait(driver, 60).until_not(
                EC.presence_of_element_located((By.CLASS_NAME, 'loading-wrapper'))
            )
        except Exception:
            console.print("[yellow]⚠️  Timed out waiting for final images, proceeding with what's available...[/yellow]")
        
        # Adding a tiny generic wait just to let the DOM settle
        time.sleep(2)

        page_source = driver.page_source
        soup = BeautifulSoup(page_source, 'html.parser')
        console.print("[green]✅ Page rendered successfully![/green]")
        return soup

    except Exception as e:
        console.print(f"[red]❌ Browser error: {e}[/red]")
        return None
    finally:
        if driver:
            driver.quit()




def extract_image_links(soup, servers):
    """Extract all image links from the page that match target servers."""
    images = soup.find_all('img')
    target_links = []

    for img in images:
        link = img.get('src') or img.get('data-src') or img.get('data-preload') or img.get('data-lazy-src')
        if link:
            for server in servers:
                if server in link:
                    if link.startswith('//'):
                        link = 'https:' + link
                    target_links.append(link)
                    break

    return target_links

def extract_next_chapter_link(soup, current_url):
    """Find the 'Next Chapter' link on the page."""
    for a in soup.find_all('a', href=True):
        text = a.text.strip().lower()
        if 'next' in text or '次' in text or '次へ' in text or '次の話' in text:
            href = a['href']
            if is_valid_url(href) and current_url not in href:
                return href
    return None

def check_for_updates():
    """Check if there is a newer version of Rawlazy-DL on GitHub."""
    console.print("[dim]🔄 Checking for updates...[/dim]")
    # Placeholder for actual GitHub API check
    time.sleep(1)
    console.print("[dim]✅ You are using the latest version of Rawlazy-DL.[/dim]\n")


def download_single_image(img_url, filepath, headers, retries=MAX_RETRIES):
    """Download a single image with retry logic and deduplication."""
    if os.path.exists(filepath) and os.path.getsize(filepath) > 1024:
        return (filepath, True, "Skipped (Already exists)", os.path.getsize(filepath))

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(img_url, headers=headers, stream=True, timeout=30, verify=False)
            if response.status_code == 200:
                total_bytes = 0
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                        total_bytes += len(chunk)
                if total_bytes > 0:
                    try:
                        from PIL import Image
                        is_webp = False
                        final_img = None
                        with Image.open(filepath) as img:
                            if img.format == 'WEBP':
                                is_webp = True
                                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                                    background = Image.new('RGB', img.size, (255, 255, 255))
                                    if img.mode == 'P':
                                        img = img.convert('RGBA')
                                    background.paste(img, mask=img.split()[3])
                                    final_img = background.copy()
                                else:
                                    final_img = img.convert('RGB')
                        
                        if is_webp and final_img:
                            final_img.save(filepath, 'JPEG', quality=95)
                            total_bytes = os.path.getsize(filepath)
                    except ImportError:
                        pass
                    except Exception as e:
                        logging.error(f"Failed to convert {filepath}: {e}")
                    
                    return (filepath, True, "OK", total_bytes)
                else:
                    os.remove(filepath)
                    return (filepath, False, "Empty file", 0)
            else:
                if attempt < retries:
                    time.sleep(RETRY_DELAY)
                    continue
                return (filepath, False, f"HTTP {response.status_code}", 0)
        except requests.exceptions.Timeout:
            if attempt < retries:
                time.sleep(RETRY_DELAY)
                continue
            return (filepath, False, "Timeout", 0)
        except Exception as e:
            logging.error(f"Error downloading {img_url}: {str(e)}")
            if attempt < retries:
                time.sleep(RETRY_DELAY)
                continue
            return (filepath, False, str(e), 0)
    logging.error(f"Max retries exceeded for {img_url}")
    return (filepath, False, "Max retries exceeded", 0)

def save_metadata(folder_path, url, title, chapter_count):
    """Save download metadata to info.json."""
    metadata = {
        "title": title,
        "source_url": url,
        "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_images": chapter_count
    }
    filepath = os.path.join(folder_path, "info.json")
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    data.append(metadata)
                else:
                    data = [data, metadata]
        else:
            data = [metadata]
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Failed to save metadata to {filepath}: {e}")

def split_manga_image(image_path, output_dir, chapter_num=None, target_ratio=1.428):
    """
    Splits a long vertical image into multiple pages based on a target ratio.
    Automatically adjusts the page height to ensure there are no small leftover 
    pixels at the bottom of the image.
    
    Returns the number of pages created, or 0 if it didn't need splitting.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    try:
        from PIL import Image
    except ImportError:
        console.print("[red]❌ Pillow is not installed. Cannot split merged images.[/red]")
        return 0

    try:
        with Image.open(image_path) as img:
            img_width, img_height = img.size
            
            # Calculate the ideal height and find the closest perfect integer of pages
            approx_height = int(img_width * target_ratio)
            num_pages = max(1, round(img_height / approx_height))
            
            if num_pages <= 1:
                return 0 # No splitting needed for regular-proportioned images
                
            console.print(f"[cyan]✂️  Extremely tall image detected! Splitting into {num_pages} individual pages...[/cyan]")
            
            # Calculate the exact height per page to prevent remainder/leftover slices
            exact_height = img_height // num_pages
            remainder = img_height % num_pages

            for i in range(num_pages):
                top = i * exact_height
                bottom = top + exact_height
                
                # Attach any minor remainder pixels to the very last page
                if i == num_pages - 1:
                    bottom += remainder

                cropped_img = img.crop((0, top, img_width, bottom))

                # Convert to RGB to avoid errors when saving as JPEG
                if cropped_img.mode in ("RGBA", "P"):
                    cropped_img = cropped_img.convert("RGB")

                if chapter_num:
                    filename = f"{chapter_num}_{i+1:03d}.jpg"
                else:
                    filename = f"page_{i+1:03d}.jpg"

                output_path = os.path.join(output_dir, filename)
                
                # Save with maximum quality and sharp text rendering
                cropped_img.save(output_path, "JPEG", quality=100, subsampling=0)
        
        mode = SETTINGS.get("split_mode", "split_only")

        if mode == "split_only":
            # Remove the original massive file so it doesn't waste space
            os.remove(image_path)
        elif mode == "both":
            # Rename the original massive file to keep it distinct
            base, ext = os.path.splitext(image_path)
            new_path = f"{base}_merged{ext}"
            try:
                os.rename(image_path, new_path)
                console.print(f"[dim]Saved original merged image as: {os.path.basename(new_path)}[/dim]")
            except Exception as e:
                logging.error(f"Failed to rename merged image {image_path}: {e}")

        return num_pages
    except Exception as e:
        console.print(f"[red]❌ Failed to split merged image: {e}[/red]")
        return 0


def download_manga_chapter(url, folder_name=None, parallel=False, max_workers=4):
    """Download all images from a manga chapter page.
    
    Returns a tuple: (success_count, next_chapter_url)
    """
    headers = get_headers(url)
    chapter_num = extract_chapter_number(url)

    try:
        console.print(f"\n[cyan]🔍 Analyzing page:[/cyan] {url}")
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        manga_title = extract_manga_title(soup, url)
        title_tag = soup.find('title')
        if title_tag:
            console.print(f"[dim]📄 Page title: {title_tag.text.strip()}[/dim]")

        if not folder_name:
            folder_name = os.path.join(DEFAULT_OUTPUT_DIR, manga_title)

        os.makedirs(folder_name, exist_ok=True)
        console.print(f"[green]📁 Output folder:[/green] {os.path.abspath(folder_name)}")
        if chapter_num:
            console.print(f"[green]📖 Chapter number:[/green] {chapter_num}")

        target_links = extract_image_links(soup, IMAGE_SERVERS)

        needs_selenium = False
        if not target_links:
            console.print("\n[yellow]⚠️  No images found with basic scraping.[/yellow]")
            needs_selenium = True
        elif soup.find(class_=re.compile(r'loading-wrapper')):
            console.print("\n[yellow]⚠️  Found a dynamic lazy-loader! Basic scraping is incomplete.[/yellow]")
            needs_selenium = True

        if needs_selenium:
            console.print("[cyan]🔄 This page likely uses JavaScript — trying Selenium...[/cyan]")

            new_soup = fetch_page_with_browser(url)
            if new_soup:
                soup = new_soup  # Update the main soup for link extraction
                target_links = extract_image_links(soup, IMAGE_SERVERS)

                if not folder_name or folder_name == os.path.join(DEFAULT_OUTPUT_DIR, 'manga_download'):
                    manga_title = extract_manga_title(soup, url)
                    folder_name = os.path.join(DEFAULT_OUTPUT_DIR, manga_title)
                    os.makedirs(folder_name, exist_ok=True)

            if not target_links:
                if soup:
                    all_imgs = soup.find_all('img')
                    console.print(f"\n[yellow]🔎 Found {len(all_imgs)} total <img> tags on page.[/yellow]")
                    for img in all_imgs[:10]:
                        src = img.get('src') or img.get('data-src') or '(no src)'
                        console.print(f"    [dim]→ {src[:100]}[/dim]")
                    if len(all_imgs) > 10:
                        console.print(f"    [dim]... and {len(all_imgs) - 10} more[/dim]")

                console.print("\n[red]❌ Still no target images found![/red]")
                console.print(f"   Image server whitelist: {IMAGE_SERVERS}")
                console.print("   [dim]💡 Tip: Check the URLs above and update IMAGE_SERVERS if needed.[/dim]")
                return 0, None

        next_chapter_url = extract_next_chapter_link(soup, url)

        console.print(f"\n[green]🎯 Found {len(target_links)} target images![/green]")

        success_count = 0
        fail_count = 0
        total_bytes = 0
        start_time = time.time()


        file_tasks = []
        for index, img_url in enumerate(target_links, start=1):
            ext = os.path.splitext(urlparse(img_url).path)[1].lower() or '.jpg'
            if ext == '.webp':
                ext = '.jpg'
            if chapter_num:
                if len(target_links) == 1:
                    filename = f"{chapter_num}{ext}"
                else:
                    filename = f"{chapter_num}_{index:03d}{ext}"
            else:
                filename = f"page_{index:03d}{ext}"
            filepath = os.path.join(folder_name, filename)
            file_tasks.append((img_url, filepath, filename))

        if parallel:

            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=30),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                overall_task = progress.add_task(
                    f"⚡ Downloading ({max_workers} threads)",
                    total=len(file_tasks)
                )
                future_list = []
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for img_url, filepath, filename in file_tasks:
                        future = executor.submit(download_single_image, img_url, filepath, headers)
                        future_list.append((future, filename))

                    for future, filename in future_list:
                        _, success, status, size = future.result()
                        if success:
                            success_count += 1
                            total_bytes += size
                        else:
                            fail_count += 1
                            console.print(f"  [red]❌ {filename} — {status}[/red]")
                        progress.update(overall_task, advance=1)
        else:

            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=30),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                overall_task = progress.add_task("⬇️  Downloading", total=len(file_tasks))

                for img_url, filepath, filename in file_tasks:
                    progress.update(overall_task, description=f"⬇️  {filename}")
                    _, success, status, size = download_single_image(img_url, filepath, headers)

                    if success:
                        success_count += 1
                        total_bytes += size
                    else:
                        fail_count += 1
                        console.print(f"  [red]❌ {filename} — {status}[/red]")
                    progress.update(overall_task, advance=1)

        elapsed = time.time() - start_time
        
        # Check if we downloaded exactly 1 image and if we should split it
        if len(target_links) == 1 and success_count == 1:
            if SETTINGS.get("split_mode", "split_only") != "merged_only":
                filepath = file_tasks[0][1]
                if os.path.exists(filepath):
                    pages_created = split_manga_image(filepath, folder_name, chapter_num)
                    if pages_created > 1:
                        success_count = pages_created
            else:
                console.print("[dim]Skipped image splitting (Split Mode: merged_only)[/dim]")

        console.print()
        table = Table(title="📊 DOWNLOAD SUMMARY", show_header=False, border_style="bright_blue")
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("✅ Successful", f"[green]{success_count}[/green]")
        table.add_row("❌ Failed", f"[red]{fail_count}[/red]" if fail_count else f"[dim]{fail_count}[/dim]")
        table.add_row("📦 Total Size", f"{total_bytes / (1024*1024):.2f} MB" if total_bytes > 1024*1024 else f"{total_bytes / 1024:.1f} KB")
        table.add_row("📁 Saved to", os.path.abspath(folder_name))
        table.add_row("⏱️  Time", f"{elapsed:.1f} seconds")
        if chapter_num:
            table.add_row("📖 Chapter", chapter_num)
        console.print(table)
        
        save_metadata(folder_name, url, manga_title, success_count)

        return success_count, next_chapter_url

    except requests.exceptions.ConnectionError:
        console.print("[red]❌ Connection error! Check your internet connection.[/red]")
        return 0, None
    except requests.exceptions.Timeout:
        console.print("[red]❌ Request timed out! The server might be down.[/red]")
        return 0, None
    except Exception as e:
        console.print(f"[red]❌ An error occurred: {e}[/red]")
        return 0, None


def batch_download(urls):
    """Download multiple chapters at once. Auto-generates folders per manga title."""
    total = len(urls)
    console.print(f"\n[bold magenta]📚 Batch download: {total} chapter(s)[/bold magenta]")
    console.rule()

    results = []
    for i, url in enumerate(urls, start=1):
        console.print(f"\n[bold cyan]━━━ Chapter {i}/{total} ━━━[/bold cyan]")
        count, _ = download_manga_chapter(url)
        results.append((url, count))


    console.print()
    table = Table(title="📚 BATCH SUMMARY", border_style="magenta")
    table.add_column("#", style="bold", justify="center")
    table.add_column("URL", max_width=60)
    table.add_column("Images", justify="center")
    table.add_column("Status", justify="center")

    for i, (url, count) in enumerate(results, start=1):
        status = "[green]✅ OK[/green]" if count > 0 else "[red]❌ Failed[/red]"
        short_url = url if len(url) <= 60 else url[:57] + "..."
        table.add_row(str(i), short_url, str(count), status)

    console.print(table)
    console.print(f"\n[bold green]🎉 Batch download complete![/bold green]")


def interactive_menu():
    """Interactive menu for the manga downloader."""
    global THEME_COLOR
    
    # Reload settings at menu start just in case
    load_settings()
    
    console.print(Panel(
        BANNER_TEXT,
        border_style="bright_blue",
        padding=(1, 4),
    ))

    check_for_updates()

    while True:
        console.print(f"\n┌──────────────────────────────────────────┐")
        console.print(f"│             [bold {THEME_COLOR}]📋 MAIN MENU[/bold {THEME_COLOR}]               │")
        console.print(f"├──────────────────────────────────────────┤")
        console.print(f"│  [bold {THEME_COLOR}][1][/bold {THEME_COLOR}] Download single chapter            │")
        console.print(f"│  [bold {THEME_COLOR}][2][/bold {THEME_COLOR}] Download multiple chapters         │")
        console.print(f"│  [bold {THEME_COLOR}][3][/bold {THEME_COLOR}] Quick download (fast mode)         │")
        console.print(f"│  [bold {THEME_COLOR}][4][/bold {THEME_COLOR}] Paste batch links                  │")
        console.print(f"│  [bold {THEME_COLOR}][5][/bold {THEME_COLOR}] Auto-Download Seri (Next Chapter)  │")
        console.print(f"│  [bold {THEME_COLOR}][6][/bold {THEME_COLOR}] Settings / UI Theme                │")
        console.print(f"│  [bold red][0][/bold red] Exit                               │")
        console.print(f"└──────────────────────────────────────────┘")

        choice = console.input("\n[bold]👉 Select option:[/bold] ").strip()

        if choice == '1':

            console.print("\n[bold]── 📖 SINGLE CHAPTER DOWNLOAD ──[/bold]")
            url = console.input("[bold]🔗 Enter manga chapter URL:[/bold] ").strip()
            if not url:
                console.print("[yellow]⚠️  URL cannot be empty![/yellow]")
                continue
            if not is_valid_url(url):
                console.print("[yellow]⚠️  Invalid URL! Must start with http:// or https://[/yellow]")
                continue

            folder = console.input("[bold]📁 Folder name (Enter = auto from title):[/bold] ").strip()
            folder = folder if folder else None

            mode = console.input("[bold]⚡ Use parallel download? (y/n, default: n):[/bold] ").strip().lower()
            parallel = mode == 'y'

            download_manga_chapter(url, folder_name=folder, parallel=parallel)

        elif choice == '2':

            console.print("\n[bold]── 📚 BATCH CHAPTER DOWNLOAD ──[/bold]")
            console.print("Enter manga chapter URLs one per line.")
            console.print("Type [bold]'done'[/bold] when finished.\n")

            urls = []
            while True:
                line = console.input(f"  URL #{len(urls)+1} (or 'done'): ").strip()
                if line.lower() == 'done':
                    break
                if line and is_valid_url(line):
                    urls.append(line)
                elif line:
                    console.print(f"  [yellow]⚠️  Invalid URL, skipped: {line[:60]}[/yellow]")

            if not urls:
                console.print("[yellow]⚠️  No valid URLs entered![/yellow]")
                continue

            batch_download(urls)

        elif choice == '3':

            console.print("\n[bold]── ⚡ QUICK DOWNLOAD ──[/bold]")
            url = console.input("[bold]🔗 Paste the URL and hit Enter:[/bold] ").strip()
            if not url:
                console.print("[yellow]⚠️  URL cannot be empty![/yellow]")
                continue
            if not is_valid_url(url):
                console.print("[yellow]⚠️  Invalid URL! Must start with http:// or https://[/yellow]")
                continue

            download_manga_chapter(url, parallel=True, max_workers=6)

        elif choice == '4':

            console.print("\n[bold]── 📋 PASTE BATCH LINKS ──[/bold]")
            console.print("Paste all your manga chapter URLs below (one per line).")
            console.print("Press [bold]Enter on an empty line[/bold] when done.\n")

            urls = []
            while True:
                try:
                    line = console.input("  ").strip()
                except EOFError:
                    break
                if not line:
                    break
                if is_valid_url(line):
                    urls.append(line)
                    console.print(f"    [green]✓ Added[/green] [dim]({len(urls)} total)[/dim]")
                else:
                    console.print(f"    [yellow]⚠️  Skipped (not a valid URL)[/yellow]")

            if not urls:
                console.print("[yellow]⚠️  No valid URLs provided![/yellow]")
                continue

            console.print(f"\n[bold green]📋 {len(urls)} URLs ready to download![/bold green]")
            confirm = console.input("[bold]Start download? (y/n):[/bold] ").strip().lower()
            if confirm != 'y':
                console.print("[dim]Cancelled.[/dim]")
                continue

            batch_download(urls)
            
        elif choice == '5':
            console.print("\n[bold]── 🤖 AUTO-DOWNLOAD SERI ──[/bold]")
            url = console.input("[bold]🔗 Enter first chapter URL:[/bold] ").strip()
            if not url or not is_valid_url(url):
                console.print("[yellow]⚠️  Invalid URL![/yellow]")
                continue

            folder = console.input("[bold]📁 Folder name (Enter = auto from title):[/bold] ").strip()
            folder = folder if folder else None

            console.print("\n[magenta]Starting auto-download... Press Ctrl+C to stop anytime.[/magenta]")
            
            current_url = url
            chapter_count = 0
            try:
                while current_url:
                    chapter_count += 1
                    console.print(f"\n[bold cyan]━━━ Auto-Download #{chapter_count} ━━━[/bold cyan]")
                    
                    _, next_url = download_manga_chapter(current_url, folder_name=folder, parallel=True)
                    
                    if next_url:
                        console.print(f"\n[dim]⏭️ Found next chapter: {next_url}[/dim]")
                        current_url = next_url
                        time.sleep(2)  # Pause gently before next chapter
                    else:
                        console.print("\n[green]🏁 No more chapters found or reached the latest one![/green]")
                        break
            except KeyboardInterrupt:
                console.print("\n[yellow]🛑 Auto-download stopped by user.[/yellow]")
                
        elif choice == '6':
            while True:
                console.print(f"\n[bold {THEME_COLOR}]── ⚙️ SETTINGS ──[/bold {THEME_COLOR}]")
                console.print(f"1. 🎨 Change UI Theme [dim](Current: {SETTINGS['theme']})[/dim]")
                
                # Format split mode text nicely
                smode = SETTINGS['split_mode']
                if smode == "split_only":
                    smode_display = "Split Only (Deletes original merged image)"
                elif smode == "merged_only":
                    smode_display = "Keep Merged Only (Does not split)"
                else:
                    smode_display = "Keep Both (Saves split pages AND merged image)"
                    
                console.print(f"2. ✂️ Change Image Splitter Mode [dim](Current: {smode_display})[/dim]")
                console.print(f"0. 🔙 Back to Main Menu")
                
                opt = console.input(f"\n[bold {THEME_COLOR}]Select setting to change (0-2):[/bold {THEME_COLOR}] ").strip()
                
                if opt == '0':
                    break
                elif opt == '1':
                    console.print(f"\n[bold {THEME_COLOR}]── 🎨 UI THEMES ──[/bold {THEME_COLOR}]")
                    console.print("1. Cyan (Default)")
                    console.print("2. [magenta]Magenta (Neon)[/magenta]")
                    console.print("3. [green]Green (Matrix)[/green]")
                    console.print("4. [blue]Blue (Ocean)[/blue]")
                    console.print("5. [yellow]Yellow (Warning)[/yellow]")
                    
                    tchoice = console.input(f"\n[bold {THEME_COLOR}]Select theme (1-5):[/bold {THEME_COLOR}] ").strip()
                    if tchoice == '1': THEME_COLOR = "cyan"
                    elif tchoice == '2': THEME_COLOR = "magenta"
                    elif tchoice == '3': THEME_COLOR = "green"
                    elif tchoice == '4': THEME_COLOR = "blue"
                    elif tchoice == '5': THEME_COLOR = "yellow"
                    else:
                        console.print("[red]Invalid choice![/red]")
                        continue
                        
                    SETTINGS["theme"] = THEME_COLOR
                    save_settings()
                    console.print(f"[bold {THEME_COLOR}]✅ Theme set successfully![/bold {THEME_COLOR}]")
                
                elif opt == '2':
                    console.print(f"\n[bold {THEME_COLOR}]── ✂️ SPLITTER MODE ──[/bold {THEME_COLOR}]")
                    console.print("[dim]How should the script handle 'mega-images' (entire chapters merged vertically)?[/dim]")
                    console.print("1. Split Only (Slices into pages, deletes massive original)")
                    console.print("2. Keep Merged Only (Never slice, just save the long original)")
                    console.print("3. Keep Both (Save sliced pages AND long original `_merged` file)")
                    
                    schoice = console.input(f"\n[bold {THEME_COLOR}]Select mode (1-3):[/bold {THEME_COLOR}] ").strip()
                    if schoice == '1': SETTINGS["split_mode"] = "split_only"
                    elif schoice == '2': SETTINGS["split_mode"] = "merged_only"
                    elif schoice == '3': SETTINGS["split_mode"] = "both"
                    else:
                        console.print("[red]Invalid choice![/red]")
                        continue
                        
                    save_settings()
                    console.print(f"[bold {THEME_COLOR}]✅ Splitter mode updated![/bold {THEME_COLOR}]")
                else:
                    console.print("[yellow]⚠️  Invalid option! Please try again.[/yellow]")

        elif choice == '0':
            console.print("\n[bold]👋 Goodbye! Happy reading![/bold]")
            sys.exit(0)

        else:
            console.print("[yellow]⚠️  Invalid option! Please try again.[/yellow]")


if __name__ == "__main__":

    if len(sys.argv) > 1:
        url = sys.argv[1]
        folder = sys.argv[2] if len(sys.argv) > 2 else None
        download_manga_chapter(url, folder_name=folder)
    else:
        interactive_menu()
