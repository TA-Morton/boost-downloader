"""
Boost Learning Bulk Downloader
==============================
Downloads all resources from a Boost Learning course page.

Because the site is a JavaScript/Angular app, this tool uses Selenium
to drive a real Chrome browser — handling login, page navigation, and
clicking the Download button on each resource's landing page.

Usage:
    python boost_downloader.py --url <course_url> --email <email>

See README.md for full setup instructions.
"""

import argparse
import getpass
import json
import re
import shutil
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ── Defaults (overridden by CLI args) ────────────────────────────────────────
DEFAULT_SAVED_HTML  = "course_page.html"
DEFAULT_DOWNLOAD_DIR = "boost_downloads"
FILE_WAIT_S = 30   # max seconds to wait for each file to download
NAV_WAIT_S  = 8    # seconds to wait for a landing page to load
# ─────────────────────────────────────────────────────────────────────────────

VIDEO_TYPES = {"video", "mp4", "animation"}


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Bulk-download resources from a Boost Learning course page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python boost_downloader.py \\
      --url https://boost-learning.com/course/resources/contents-list/9781398385511 \\
      --email teacher@school.ac.uk

  # Resume an interrupted run (already-downloaded files are skipped):
  python boost_downloader.py \\
      --url https://boost-learning.com/course/resources/contents-list/9781398385511 \\
      --email teacher@school.ac.uk \\
      --output my_downloads

  # Run headlessly (no visible browser window):
  python boost_downloader.py ... --headless
        """,
    )
    parser.add_argument(
        "--url", required=True,
        help="Full URL of the Boost Learning course contents page.",
    )
    parser.add_argument(
        "--email", required=True,
        help="Your Boost Learning login email address.",
    )
    parser.add_argument(
        "--html", default=DEFAULT_SAVED_HTML, metavar="FILE",
        help=f"Path to the manually-saved course HTML file (default: {DEFAULT_SAVED_HTML}).",
    )
    parser.add_argument(
        "--output", default=DEFAULT_DOWNLOAD_DIR, metavar="DIR",
        help=f"Directory to save downloaded files (default: {DEFAULT_DOWNLOAD_DIR}).",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run Chrome without a visible window.",
    )
    return parser.parse_args()


# ── File / directory helpers ──────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def load_log(log_file: Path) -> dict:
    if log_file.exists():
        return json.loads(log_file.read_text())
    return {"downloaded": [], "failed": [], "skipped": []}


def save_log(log: dict, log_file: Path) -> None:
    log_file.write_text(json.dumps(log, indent=2))


def clear_temp(temp_dir: Path) -> None:
    """Delete all completed (non-.crdownload) files from the temp directory."""
    for f in list(temp_dir.iterdir()):
        if f.is_file() and f.suffix != ".crdownload":
            try:
                f.unlink()
            except Exception:
                pass


def wait_for_download(temp_dir: Path, timeout: int = FILE_WAIT_S) -> Path | None:
    """
    Wait for a completed file to appear in temp_dir.
    Call AFTER clear_temp() so any file that appears belongs to the current download.
    Polls every 0.25 s so fast small-file downloads are not missed.
    Extends the deadline once if a .crdownload is still actively in progress.
    Returns the Path of the downloaded file, or None on timeout.
    """
    deadline = time.time() + timeout
    extended = False
    while time.time() < deadline:
        time.sleep(0.25)
        in_progress = list(temp_dir.glob("*.crdownload"))
        complete = [f for f in temp_dir.iterdir()
                    if f.is_file() and f.suffix != ".crdownload"]
        if complete and not in_progress:
            time.sleep(0.3)  # ensure the file is fully written before we move it
            return complete[0]
        if in_progress and not extended and time.time() >= deadline:
            deadline = time.time() + 30
            extended = True
    return None


def move_and_rename(temp_file: Path, title: str, download_dir: Path) -> Path:
    """Move a downloaded file from temp dir to download_dir with a clean name."""
    ext = temp_file.suffix
    safe_title = sanitize_filename(title)
    dest = download_dir / f"{safe_title}{ext}"
    counter = 2
    while dest.exists():
        dest = download_dir / f"{safe_title}_{counter}{ext}"
        counter += 1
    shutil.move(str(temp_file), str(dest))
    return dest


# ── HTML card extraction ──────────────────────────────────────────────────────

def extract_cards_from_file(html_path: Path) -> list[dict]:
    """
    Parse the manually-saved course HTML to get all resource card GUIDs,
    titles, and type badges (e.g. 'Video', 'Infographic', 'Practical Guidance').

    Reading the type badge from the saved HTML is more reliable than checking
    the live landing page, because Brightcove JS loads on every page and would
    cause false-positive video detection.
    """
    print(f"Reading cards from: {html_path}")
    soup = BeautifulSoup(
        html_path.read_text(encoding="utf-8", errors="ignore"), "html.parser"
    )

    cards = []
    seen = set()

    for el in soup.find_all(id=re.compile(r"^thumbnail-")):
        card_guid = el["id"].replace("thumbnail-", "")
        if card_guid in seen:
            continue
        seen.add(card_guid)

        title = ""
        resource_type = ""
        parent = el.parent

        for _ in range(8):
            if parent is None:
                break

            if not title:
                for tag in ["h1", "h2", "h3", "h4", "h5"]:
                    t = parent.find(tag)
                    if t and t.get_text(strip=True):
                        title = t.get_text(strip=True)
                        break
                if not title:
                    for cls in ["title", "name", "heading", "card-title", "resource-title"]:
                        t = parent.find(class_=re.compile(cls, re.I))
                        if t and t.get_text(strip=True):
                            title = t.get_text(strip=True)
                            break

            if not resource_type:
                for cls in ["type", "badge", "label", "format", "resource-type", "file-type"]:
                    t = parent.find(class_=re.compile(cls, re.I))
                    if t and t.get_text(strip=True):
                        resource_type = t.get_text(strip=True).lower()
                        break
                if not resource_type:
                    mp4_img = parent.find("img", alt=re.compile(r"mp4|video", re.I))
                    if mp4_img:
                        resource_type = "video"

            if title and resource_type:
                break
            parent = parent.parent

        is_video = any(vt in resource_type for vt in VIDEO_TYPES)
        cards.append({
            "card_guid": card_guid,
            "title": title or card_guid,
            "resource_type": resource_type,
            "is_video": is_video,
        })

    total = len(cards)
    videos = sum(1 for c in cards if c["is_video"])
    print(f"Found {total} resource cards  ({videos} videos skipped, {total - videos} to download)")
    return cards


# ── Selenium / browser helpers ────────────────────────────────────────────────

def get_driver(temp_dir: Path, headless: bool = False) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    prefs = {
        "download.default_directory": str(temp_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True,  # PDFs download, not open
    }
    options.add_experimental_option("prefs", prefs)
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,900")
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )


def login(driver: webdriver.Chrome, email: str, password: str) -> None:
    print("Logging in...")
    driver.get("https://boost-learning.com/login")
    wait = WebDriverWait(driver, 20)
    email_field = wait.until(EC.presence_of_element_located((
        By.CSS_SELECTOR,
        "input[type='email'], input[name='email'], input[placeholder*='email' i]",
    )))
    email_field.clear()
    email_field.send_keys(email)
    pass_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
    pass_field.send_keys(password)
    pass_field.submit()
    wait.until(EC.url_changes("https://boost-learning.com/login"))
    time.sleep(3)
    print(f"Logged in ✓  ({driver.current_url})")


def dismiss_overlay(driver: webdriver.Chrome) -> None:
    """
    Boost Learning landing pages show a full-page overlay that intercepts clicks.
    Press Escape to dismiss it, then hide it via JS as a belt-and-braces measure.
    """
    try:
        overlay = driver.find_element(By.CSS_SELECTOR, "div.overlay.active")
        if overlay.is_displayed():
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.8)
    except NoSuchElementException:
        pass
    driver.execute_script("""
        document.querySelectorAll('.overlay, .overlay.active').forEach(el => {
            el.style.display = 'none';
            el.style.pointerEvents = 'none';
        });
    """)


def try_download(driver: webdriver.Chrome, card_guid: str, title: str,
                 temp_dir: Path) -> tuple[str, object]:
    """
    Navigate to the resource landing page and click Download.

    Strategy 1 — direct download button:
        The landing page has <div class="download-tool"> which triggers the
        download immediately when clicked.

    Strategy 2 — hamburger menu:
        Some pages may use <img id="dropdown-img"> to open a menu; we then
        click the Download item inside it.

    Returns ('downloaded', Path) or ('failed', error_message).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "resource"
    driver.get(f"https://boost-learning.com/resource-landing/{card_guid}/{slug}")
    time.sleep(NAV_WAIT_S)

    wait = WebDriverWait(driver, 10)
    dismiss_overlay(driver)
    clear_temp(temp_dir)

    # Diagnostic: warn if sidebar has extra download buttons
    tool_count = driver.execute_script(
        "return document.querySelectorAll('div.download-tool').length;"
    )
    if tool_count > 1:
        print(f"  → {tool_count} download buttons found (sidebar present — clicking first)")

    # Strategy 1: direct download-tool div
    btns = driver.find_elements(By.CSS_SELECTOR, "div.download-tool")
    if btns:
        driver.execute_script("arguments[0].click();", btns[0])
        result = wait_for_download(temp_dir)
        if result:
            return "downloaded", result
        return "failed", "Download button clicked but no file appeared within timeout"

    # Strategy 2: hamburger → Download menu item
    try:
        burger = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "img#dropdown-img"))
        )
        driver.execute_script("arguments[0].click();", burger)
        time.sleep(1.5)
        item = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH,
                "//*[contains(@class,'download-tool') and contains(text(),'Download')]"
                "|//*[contains(@class,'menu-item') and contains(text(),'Download')]"
                "|//*[contains(@class,'option-item') and contains(text(),'Download')]"
            ))
        )
        driver.execute_script("arguments[0].click();", item)
        result = wait_for_download(temp_dir)
        if result:
            return "downloaded", result
    except (TimeoutException, NoSuchElementException):
        pass

    return "failed", "No download button found on landing page"


# ── Main ──────────────────────────────────────────────────────────────────────

def print_scroll_instructions(html_file: str) -> None:
    print(f"\nERROR: '{html_file}' not found.\n")
    print("You need to save a fully-scrolled copy of the course page first:\n")
    print("  1. In Chrome, open the course URL and log in")
    print("  2. Open DevTools (F12) → Console tab, then paste:\n")
    print("""     (async () => {
       const containers = [
         document.querySelector('#scrollable'),
         document.querySelector('.ng-scroll-view'),
         document.querySelector('.sub-part'),
         document.querySelector('cdk-virtual-scroll-viewport'),
         document.body
       ];
       const el = containers.find(c => c && c.scrollHeight > c.clientHeight + 100);
       console.log('Scrolling:', el?.tagName, el?.id);
       let last = -1;
       while (el.scrollTop !== last) {
         last = el.scrollTop;
         el.scrollTop += 800;
         await new Promise(r => setTimeout(r, 2000));
         console.log('Cards found:', document.querySelectorAll('[id^="thumbnail-"]').length);
       }
       console.log('DONE! Total:', document.querySelectorAll('[id^="thumbnail-"]').length);
     })();\n""")
    print("  3. When 'DONE!' appears, press Ctrl+S")
    print(f"     → Save as 'Webpage, Complete' and name the file: {html_file}\n")


def main() -> None:
    args = parse_args()

    html_path    = Path(args.html)
    download_dir = Path(args.output).absolute()
    temp_dir     = Path(args.output + "_temp").absolute()
    log_file     = download_dir / "download_log.json"

    # Validate saved HTML exists
    if not html_path.exists():
        print_scroll_instructions(args.html)
        sys.exit(1)

    download_dir.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)

    # Prompt for password (never passed as a CLI argument)
    password = getpass.getpass(f"Password for {args.email}: ")

    log = load_log(log_file)
    downloaded_titles = {d["title"] for d in log["downloaded"]}
    skipped_titles    = {s["title"] for s in log.get("skipped", [])}

    cards = extract_cards_from_file(html_path)
    if not cards:
        print("ERROR: No resource cards found in saved HTML. Did you scroll before saving?")
        sys.exit(1)

    driver = get_driver(temp_dir, headless=args.headless)
    try:
        login(driver, args.email, password)
        log["failed"] = []  # clear previous failures so this run is a clean retry

        for i, card in enumerate(cards, 1):
            title         = card["title"]
            card_guid     = card["card_guid"]
            is_video      = card.get("is_video", False)
            resource_type = card.get("resource_type", "")

            if title in downloaded_titles:
                print(f"[{i}/{len(cards)}] SKIP (already downloaded): {title}")
                continue
            if title in skipped_titles:
                print(f"[{i}/{len(cards)}] SKIP (video): {title}")
                continue
            if is_video:
                print(f"[{i}/{len(cards)}] SKIP (video/{resource_type}): {title}")
                log.setdefault("skipped", []).append({
                    "title": title, "card_guid": card_guid,
                    "reason": f"Video resource ({resource_type})",
                })
                save_log(log, log_file)
                continue

            print(f"[{i}/{len(cards)}] {title}  [{resource_type or 'unknown type'}]")

            try:
                status, result = try_download(driver, card_guid, title, temp_dir)

                if status == "downloaded":
                    dest = move_and_rename(result, title, download_dir)
                    size_kb = dest.stat().st_size // 1024
                    print(f"  ✓  {dest.name}  ({size_kb} KB)")
                    log["downloaded"].append({
                        "title": title, "card_guid": card_guid,
                        "resource_type": resource_type, "file": str(dest),
                    })
                else:
                    print(f"  ✗  {result}")
                    log["failed"].append({
                        "title": title, "card_guid": card_guid, "error": result,
                    })

            except Exception as exc:
                print(f"  ✗  Unexpected error: {exc}")
                log["failed"].append({
                    "title": title, "card_guid": card_guid, "error": str(exc),
                })

            save_log(log, log_file)
            time.sleep(0.5)

    finally:
        driver.quit()
        for f in temp_dir.glob("*.crdownload"):
            f.unlink(missing_ok=True)

    print()
    print("=" * 55)
    print(f"  Downloaded : {len(log['downloaded'])}")
    print(f"  Skipped    : {len(log.get('skipped', []))}  (videos / non-downloadable)")
    print(f"  Failed     : {len(log['failed'])}")
    print(f"  Files in   : {download_dir}")
    if log["failed"]:
        print(f"\n  Re-run the same command to retry the {len(log['failed'])} failed item(s).")
    print("=" * 55)


if __name__ == "__main__":
    main()
