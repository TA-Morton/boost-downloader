# Boost Learning Bulk Downloader

A Python tool that bulk-downloads all resources from a [Boost Learning](https://boost-learning.com) course page — PDFs, Word documents, PowerPoints, and more.

Because Boost Learning is a JavaScript/Angular app, the tool uses Selenium to drive a real Chrome browser. It logs in with your credentials, navigates to each resource's landing page, and clicks the Download button automatically.

---

## How it works

Boost Learning lazy-loads resources as you scroll, which means a simple web scraper can't see all the content. This tool works around that with a two-step process:

1. **You** scroll the course page in your browser (using a console snippet) and save the fully-loaded HTML
2. **The script** reads that HTML to build the list of resources, then logs in and downloads each one

---

## Requirements

- Python 3.10 or later
- Google Chrome (any recent version)
- A valid Boost Learning account with access to the course

---

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/boost-downloader.git
cd boost-downloader
pip install -r requirements.txt
```

`webdriver-manager` automatically downloads the correct ChromeDriver version for your installed Chrome — you don't need to install it separately.

---

## Step 1 — Save the course page

Before running the script, you need to save a fully-scrolled copy of the course contents page.

1. Open Chrome and log in to Boost Learning
2. Navigate to your course contents page — the URL will look like:
   ```
   https://boost-learning.com/course/resources/contents-list/YOUR_ISBN
   ```
3. Open DevTools with **F12**, click the **Console** tab, and paste this snippet:

   ```javascript
   (async () => {
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
   })();
   ```

4. Wait for the console to print `DONE! Total: X` — this means all resources are loaded
5. Press **Ctrl+S** (or **Cmd+S** on Mac), choose **Webpage, Complete**, and save the file as `course_page.html` in the same folder as `boost_downloader.py`

---

## Step 2 — Run the downloader

```bash
python boost_downloader.py \
    --url https://boost-learning.com/course/resources/contents-list/YOUR_ISBN \
    --email your@email.com
```

You will be prompted for your password. It is never stored or logged.

The script opens a Chrome window, logs in, and works through every resource automatically. Progress is printed to the terminal and saved to `boost_downloads/download_log.json` after each file.

---

## Options

| Flag | Default | Description |
|---|---|---|
| `--url` | *(required)* | Full URL of the course contents page |
| `--email` | *(required)* | Your Boost Learning login email |
| `--html` | `course_page.html` | Path to your saved course HTML file |
| `--output` | `boost_downloads` | Folder to save downloaded files |
| `--headless` | off | Run Chrome without a visible window |

### Examples

```bash
# Save files to a custom folder
python boost_downloader.py \
    --url https://boost-learning.com/course/resources/contents-list/9781398385511 \
    --email teacher@school.ac.uk \
    --output ks3_science

# Run without a browser window opening
python boost_downloader.py \
    --url https://boost-learning.com/course/resources/contents-list/9781398385511 \
    --email teacher@school.ac.uk \
    --headless
```

---

## Resuming an interrupted run

The script saves progress after every file. If it's interrupted (or some files fail), just run the same command again — already-downloaded files are skipped automatically.

Failed items are cleared and retried on each run, so repeated runs will eventually catch transient errors (network hiccups, slow page loads, etc.).

---

## What gets downloaded

| Resource type | Downloaded? |
|---|---|
| PDF (slides, worksheets, notes) | ✅ Yes |
| Word documents (.docx) | ✅ Yes |
| PowerPoint (.pptx) | ✅ Yes |
| Other file types | ✅ Yes |
| Videos (MP4 / Brightcove) | ⏭ Skipped — these are streaming only |
| Interactive assessments | ⏭ Skipped — web-only format |

Skipped items are logged in `download_log.json` but do not count as failures.

---

## Output

Files are saved to `boost_downloads/` (or your `--output` folder) with clean filenames based on the resource title. A log file at `boost_downloads/download_log.json` records every downloaded, skipped, and failed item.

A temporary folder (`boost_downloads_temp/`) is used as Chrome's download location and cleaned up automatically.

---

## Troubleshooting

**Script says `course_page.html` not found**
Make sure you completed Step 1 and saved the file with the correct name in the same folder as the script.

**Resources are showing as failed**
Re-run the script — transient failures (slow page loads, timeouts) often succeed on retry.

**Chrome version mismatch error**
Update Chrome to the latest version, then run again. `webdriver-manager` will automatically fetch the matching ChromeDriver.

**Login fails**
Check your email and password are correct. If your school uses SSO (single sign-on), you may need to handle the login step manually — contact your IT team.

---

## Disclaimer

This tool is intended for users who have a legitimate, paid subscription to Boost Learning, to help them access resources they are entitled to. Please use it in accordance with Boost Learning's terms of service. Downloaded resources remain subject to the publisher's copyright.
