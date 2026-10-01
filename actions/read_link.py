"""
actions/read_link.py — fetches a URL and returns its readable text content, so
MIA can actually read and extract information from a link the user pastes or
mentions, instead of only searching the web (actions/web_search.py) or
browsing interactively (actions/browser_control.py, which needs Playwright —
not installed on this server, see 2026-09-29 finding).

Deliberately dependency-light and self-contained (requests + bs4, both
already installed) — one tool call, no chain the model has to orchestrate,
same reasoning as actions/clone_and_learn.py.
"""
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

FETCH_TIMEOUT_SECONDS = 20
DOWNLOAD_TIMEOUT_SECONDS = 120
_OUTPUT_LIMIT = 8000
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100MB — a sane cap, not a real quota
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MIA/1.0; +https://mark-liii)"}
BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads"


def _download(url: str, resp: requests.Response) -> str:
    """Save a non-HTML response to BASE_DIR/downloads — same safe, scoped
    location self_dev/clone_and_learn already use, so file_processor (via
    self_dev's path prefix convention) can read it afterwards."""
    name = Path(urlparse(url).path).name or "downloaded_file"
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS_DIR / name
    if target.exists():
        stem, suffix = target.stem, target.suffix
        i = 2
        while target.exists():
            target = DOWNLOADS_DIR / f"{stem}_{i}{suffix}"
            i += 1
    size = 0
    with open(target, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > _MAX_DOWNLOAD_BYTES:
                f.close()
                target.unlink(missing_ok=True)
                return f"Refused: {url} is larger than the {_MAX_DOWNLOAD_BYTES // (1024*1024)}MB download limit."
            f.write(chunk)
    # [FILE: ...] is a convention the Command Center bridge scans for (see runtime.py) to
    # actually fetch and show the file in the chat, not just mention its path in text.
    return (f"Downloaded to downloads/{target.name} ({size / 1024:.1f} KB). "
            f"[FILE: downloads/{target.name}]\n"
            f"Use file_processor with file_path 'downloads/{target.name}' to read or convert it, "
            f"or self_dev(action=\"read\", path=\"downloads/{target.name}\") for plain text.")


def read_link(parameters: dict, player=None, session_memory=None) -> str:
    url = str((parameters or {}).get("url") or "").strip()
    if not url:
        return "I need a URL to read."
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    force_download = bool((parameters or {}).get("download"))

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=(FETCH_TIMEOUT_SECONDS if not force_download
                                                            else DOWNLOAD_TIMEOUT_SECONDS), stream=True)
    except requests.exceptions.Timeout:
        return f"Timed out fetching {url}."
    except Exception as e:
        return f"Could not fetch {url}: {e}"

    if resp.status_code >= 400:
        return f"{url} returned HTTP {resp.status_code}."

    content_type = resp.headers.get("content-type", "")
    if force_download or ("html" not in content_type and "text" not in content_type):
        return _download(url, resp)

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        text = soup.get_text(separator="\n", strip=True)
    except Exception as e:
        return f"Could not parse the page at {url}: {e}"

    if not text:
        return f"{url} loaded but had no readable text (may need JavaScript to render)."

    truncated = len(text) > _OUTPUT_LIMIT
    text = text[:_OUTPUT_LIMIT]

    if player:
        try:
            player.write_log(f"MIA: [read_link] {url} -> {len(text)} chars" + (" (truncated)" if truncated else ""))
        except Exception:
            pass

    header = f"Title: {title}\nURL: {url}\n\n" if title else f"URL: {url}\n\n"
    return header + text + (f"\n\n... [truncated, page had more content]" if truncated else "")


TOOL = {
    "name": "read_link",
    "description": (
        "Fetches a web page by URL and returns its readable text content, so you can extract "
        "real information from it — call this whenever the user pastes or mentions a link and "
        "wants to know what's on it, have it summarized, or asks a question about its content. "
        "A non-HTML link (PDF, image, zip, any file) is downloaded automatically instead — or "
        "set download=true to force-download an HTML link's target instead of reading it. "
        "Do not guess what a page says from its URL alone; always fetch it first."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "url": {"type": "STRING", "description": "The URL to fetch, read, or download"},
            "download": {"type": "BOOLEAN",
                        "description": "Force saving the file instead of reading it as text (default: false)"},
        },
        "required": ["url"],
    },
    "handler": read_link,
}
