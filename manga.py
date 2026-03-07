import os
import re
import sys
import time
import requests
import urllib3
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def get_headers(referer_url):
    """Generate browser-like headers to bypass anti-hotlink protection."""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
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
        .../manga-name-105/  → '105'
        .../chapter-42/      → '42'
        .../manga-name/      → None
    """
    path = urlparse(url).path.rstrip('/')
    last_segment = path.split('/')[-1] if '/' in path else path
    last_segment = unquote(last_segment)
    match = re.search(r'[-_](\d+)$', last_segment)
    if match:
        return match.group(1)
    match = re.match(r'^(\d+)$', last_segment)
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

        console.print("[cyan]📜 Scrolling page to trigger lazy-loading...[/cyan]")
        last_height = driver.execute_script('return document.body.scrollHeight')
        for _ in range(5):
            driver.execute_script('window.scrollTo(0, document.body.scrollHeight);')
            time.sleep(1)
            new_height = driver.execute_script('return document.body.scrollHeight')
            if new_height == last_height:
                break
            last_height = new_height

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


def download_single_image(img_url, filepath, headers, retries=MAX_RETRIES):
    """Download a single image with retry logic. Returns (filepath, success, status, size_bytes)."""
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
            if attempt < retries:
                time.sleep(RETRY_DELAY)
                continue
            return (filepath, False, str(e), 0)
    return (filepath, False, "Max retries exceeded", 0)


def download_manga_chapter(url, folder_name=None, parallel=False, max_workers=4):
    """Download all images from a manga chapter page.
    
    If folder_name is None, auto-generates from manga title.
    Files are named by chapter number (e.g., 105.jpg).
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

        if not target_links:
            console.print("\n[yellow]⚠️  No images found with basic scraping.[/yellow]")
            console.print("[cyan]🔄 This page likely uses JavaScript — trying Selenium...[/cyan]")

            soup = fetch_page_with_browser(url)
            if soup:
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
                return 0

        console.print(f"\n[green]🎯 Found {len(target_links)} target images![/green]")

        success_count = 0
        fail_count = 0
        total_bytes = 0
        start_time = time.time()


        file_tasks = []
        for index, img_url in enumerate(target_links, start=1):
            ext = os.path.splitext(urlparse(img_url).path)[1] or '.jpg'
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

        return success_count

    except requests.exceptions.ConnectionError:
        console.print("[red]❌ Connection error! Check your internet connection.[/red]")
        return 0
    except requests.exceptions.Timeout:
        console.print("[red]❌ Request timed out! The server might be down.[/red]")
        return 0
    except Exception as e:
        console.print(f"[red]❌ An error occurred: {e}[/red]")
        return 0


def batch_download(urls):
    """Download multiple chapters at once. Auto-generates folders per manga title."""
    total = len(urls)
    console.print(f"\n[bold magenta]📚 Batch download: {total} chapter(s)[/bold magenta]")
    console.rule()

    results = []
    for i, url in enumerate(urls, start=1):
        console.print(f"\n[bold cyan]━━━ Chapter {i}/{total} ━━━[/bold cyan]")
        count = download_manga_chapter(url)
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
    console.print(Panel(
        BANNER_TEXT,
        border_style="bright_blue",
        padding=(1, 4),
    ))

    while True:
        console.print("\n┌───────────────────────────────────────┐")
        console.print("│           [bold]📋 MAIN MENU[/bold]               │")
        console.print("├───────────────────────────────────────┤")
        console.print("│  [bold cyan][1][/bold cyan] Download single chapter          │")
        console.print("│  [bold cyan][2][/bold cyan] Download multiple chapters       │")
        console.print("│  [bold cyan][3][/bold cyan] Quick download (fast mode)       │")
        console.print("│  [bold cyan][4][/bold cyan] Paste batch links                │")
        console.print("│  [bold red][0][/bold red] Exit                              │")
        console.print("└───────────────────────────────────────┘")

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
