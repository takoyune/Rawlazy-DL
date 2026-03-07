# 🚀 Rawlazy-DL

A powerful command-line manga downloader optimized for **rawlazy.io**, featuring smart naming, batch downloading, and a beautiful Rich-powered UI. Built for downloading raw manga chapters from web sources with ease.

**by Takoyune**

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **Smart Folder Naming** | Automatically extracts the manga title from the page and uses it as the download folder name |
| **Chapter-Based File Naming** | Extracts chapter number from URL — files are named like `105.jpg` instead of generic `page_001.jpg` |
| **Selenium JS Rendering** | Auto-fallback to headless Chrome when pages load images via JavaScript |
| **Batch Download** | Download multiple chapters at once — enter URLs one by one or paste them all at once |
| **Rich Progress Bars** | Beautiful real-time download progress with speed, percentage, and ETA |
| **Retry Logic** | Automatic retry on failed downloads (configurable max retries and delay) |
| **Parallel Downloads** | Multi-threaded downloading for faster batch operations |
| **SSL Bypass** | Handles manga servers with self-signed or misconfigured SSL certificates |
| **Command-Line Support** | Use interactively or pass URLs directly as arguments |

---

## 📋 Requirements

- **Python** 3.10+
- **Google Chrome** (required for Selenium JS rendering)

### Python Packages

```
requests
beautifulsoup4
rich
selenium
webdriver-manager
```

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/takoyune/Rawlazy-DL.git
cd Rawlazy-DL
```

### 2. Install dependencies

```bash
pip install requests beautifulsoup4 rich selenium webdriver-manager
```

Or using `requirements.txt`:

```bash
pip install -r requirements.txt
```

---

## 📖 Usage

### Interactive Mode

Simply run the script without arguments to launch the interactive menu:

```bash
python manga.py
```

You will see:

```
╭──────────────────────────────────────────╮
│  📖  MANGA DOWNLOADER  📖               │
│  ─────────────────────────────           │
│  Download raw manga chapters with ease!  │
│  Smart naming • Batch download • Rich UI │
│  by Takoyune                             │
╰──────────────────────────────────────────╯

┌───────────────────────────────────────┐
│           📋 MAIN MENU               │
├───────────────────────────────────────┤
│  [1] Download single chapter          │
│  [2] Download multiple chapters       │
│  [3] Quick download (fast mode)       │
│  [4] Paste batch links                │
│  [0] Exit                             │
└───────────────────────────────────────┘
```

### Menu Options

#### `[1]` Download Single Chapter

Download one chapter at a time. You can specify a custom folder name or let the downloader auto-generate one from the manga title.

```
🔗 Enter manga chapter URL: https://example.com/manga-name-105/
📁 Folder name (Enter = auto from title): [press Enter]
⚡ Use parallel download? (y/n, default: n): n
```

The downloader will:
- Fetch the page and extract the manga title → creates folder `downloads/辺境の薬師、都でSランク冒険者となる～英雄村`
- Extract chapter `105` from URL → names file `105.jpg`
- If basic scraping fails, automatically launches Selenium to render JavaScript

#### `[2]` Download Multiple Chapters

Enter chapter URLs one by one, then type `done` to start downloading:

```
URL #1 (or 'done'): https://example.com/manga-name-101/
URL #2 (or 'done'): https://example.com/manga-name-102/
URL #3 (or 'done'): done
```

Each chapter is automatically organized into its own folder with proper naming.

#### `[3]` Quick Download (Fast Mode)

Paste a URL and it downloads immediately using parallel mode (6 threads) with auto folder naming. No extra prompts.

#### `[4]` Paste Batch Links

Paste multiple URLs at once (one per line), then press Enter on an empty line:

```
  https://example.com/manga-name-101/
    ✓ Added (1 total)
  https://example.com/manga-name-102/
    ✓ Added (2 total)
  [press Enter]

📋 2 URLs ready to download!
Start download? (y/n): y
```

### Command-Line Mode

```bash
python manga.py <url>
python manga.py <url> <folder_name>
```

Examples:

```bash
python manga.py "https://example.com/manga-name-105/"

python manga.py "https://example.com/manga-name-105/" "my_manga"
```

---

## 📁 Output Structure

The downloader automatically organizes files like this:

```
downloads/
└── 辺境の薬師、都でSランク冒険者となる～英雄村/
    ├── 101.jpg
    ├── 102.jpg
    ├── 103.jpg
    ├── 104.jpg
    └── 105.jpg
```

- **Folder** = manga title (extracted from page `<title>` tag)
- **Files** = chapter number (extracted from URL)

---

## ⚙️ Configuration

Edit these variables at the top of `manga.py` to customize behavior:

```python
DEFAULT_OUTPUT_DIR = "downloads"       # Base output directory
IMAGE_SERVERS = ["pubg-img.si"]        # Whitelisted image server domains
MAX_RETRIES = 3                        # Retry attempts for failed downloads
RETRY_DELAY = 2                        # Seconds between retries
```

### Adding New Image Servers

If your manga source uses a different image server, add the domain to `IMAGE_SERVERS`:

```python
IMAGE_SERVERS = ["pubg-img.si", "another-server.com", "cdn.example.net"]
```

The downloader will only grab `<img>` tags whose `src` contains one of these domains.

---

## 🔧 How It Works

```
URL Input
    │
    ▼
┌─────────────────────┐
│  Fetch page (HTTP)  │
└─────────┬───────────┘
          │
    Found images?
    ┌─────┴─────┐
    │ YES       │ NO
    ▼           ▼
Download   ┌──────────────────┐
images     │  Selenium (JS)   │
           │  Headless Chrome │
           └────────┬─────────┘
                    │
              Found images?
              ┌─────┴─────┐
              │ YES       │ NO
              ▼           ▼
           Download    Show debug
           images      info + exit
```

1. **First try**: Uses `requests` + `BeautifulSoup` (fast, no browser needed)
2. **Fallback**: If no images found, launches headless Chrome via Selenium to render JavaScript
3. **Smart scroll**: Scrolls the page to trigger lazy-loaded images
4. **Download**: Downloads images with retry logic and progress tracking

---

## 🐛 Troubleshooting

### "No target images found"

- The image server domain might have changed. Check the debug output for actual image URLs and add the domain to `IMAGE_SERVERS`.

### SSL Certificate Error

- Already handled automatically with `verify=False`. If you still see issues, your network/proxy may be interfering.

### Selenium Not Working

- Make sure **Google Chrome** is installed on your system.
- ChromeDriver is auto-downloaded by `webdriver-manager` — no manual setup needed.
- If Chrome is installed in a non-standard location, you may need to set the `CHROME_BIN` environment variable.

### ModuleNotFoundError

- Make sure all dependencies are installed:
  ```bash
  pip install requests beautifulsoup4 rich selenium webdriver-manager
  ```

---

## 📄 License

This project is open source and available under the [MIT License](LICENSE).

---

## 🙏 Credits

Made with ❤️ by **Takoyune**
