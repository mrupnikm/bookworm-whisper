import os
import re
import time
import logging
from threading import Lock

import requests

logger = logging.getLogger(__name__)

_cache = {
    "books": None,
    "books_by_normalized_title": {},
    "last_fetch": 0,
}
_cache_lock = Lock()
CACHE_TTL = 60  # seconds

_auth = {
    "access_token": None,
    "refresh_token": None,
}
_auth_lock = Lock()


def get_grimmory_config():
    """Get Grimmory configuration from environment."""
    url = os.environ.get("GRIMMORY_URL", "")
    username = os.environ.get("GRIMMORY_USERNAME", "")
    password = os.environ.get("GRIMMORY_PASSWORD", "")
    return url, username, password


def _get_session():
    """Get a requests session with connection pooling."""
    if not hasattr(_get_session, "_session"):
        _get_session._session = requests.Session()
    return _get_session._session


def _login():
    """Authenticate with Grimmory and store the JWT token."""
    base_url, username, password = get_grimmory_config()
    if not base_url or not username or not password:
        return False

    session = _get_session()
    try:
        r = session.post(
            f"{base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if r.status_code != 200:
            logger.error(f"Grimmory login failed: {r.status_code}")
            return False
        data = r.json()
        with _auth_lock:
            _auth["access_token"] = (
                data.get("accessToken") or data.get("token") or data.get("access_token")
            )
            _auth["refresh_token"] = data.get("refreshToken") or data.get("refresh_token")
        return bool(_auth["access_token"])
    except requests.RequestException as e:
        logger.error(f"Grimmory login error: {e}")
        return False


def _get_headers():
    with _auth_lock:
        token = _auth["access_token"]
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _authenticated_get(url, **kwargs):
    """Make an authenticated GET, re-authenticating once on 401."""
    session = _get_session()
    headers = _get_headers()
    if not headers:
        if not _login():
            return None
        headers = _get_headers()

    r = session.get(url, headers=headers, **kwargs)
    if r.status_code == 401:
        logger.debug("Grimmory token expired, re-authenticating")
        if not _login():
            return None
        headers = _get_headers()
        r = session.get(url, headers=headers, **kwargs)
    return r


def _authenticated_put(url, **kwargs):
    """Make an authenticated PUT, re-authenticating once on 401."""
    session = _get_session()
    headers = _get_headers()
    if not headers:
        if not _login():
            return None
        headers = _get_headers()

    r = session.put(url, headers=headers, **kwargs)
    if r.status_code == 401:
        logger.debug("Grimmory token expired, re-authenticating")
        if not _login():
            return None
        headers = _get_headers()
        r = session.put(url, headers=headers, **kwargs)
    return r


def _find_book_id(search_title):
    """Find a book ID by fuzzy title match using the cache."""
    books = get_all_books()
    if not books:
        return None

    search_normalized = normalize_title(search_title)

    with _cache_lock:
        if search_normalized in _cache["books_by_normalized_title"]:
            return _cache["books_by_normalized_title"][search_normalized]["id"]

    best_match = None
    best_score = 0

    for book in books:
        title_normalized = normalize_title(book["title"])

        if title_normalized == search_normalized:
            return book["id"]

        if title_normalized in search_normalized:
            score = len(title_normalized) / len(search_normalized)
            if search_normalized.startswith(title_normalized):
                score += 0.5
            if score > best_score:
                best_score = score
                best_match = book
        elif search_normalized in title_normalized:
            score = len(search_normalized) / len(title_normalized)
            if score > best_score:
                best_score = score
                best_match = book
        else:
            search_words = set(search_normalized.split())
            title_words = set(title_normalized.split())
            common_words = search_words & title_words
            if len(common_words) >= 2:
                score = len(common_words) / max(len(search_words), len(title_words))
                if score > best_score:
                    best_score = score
                    best_match = book

    if best_match and best_score > 0.3:
        return best_match["id"]
    return None


def _is_cache_valid():
    return (
        _cache["books"] is not None
        and (time.time() - _cache["last_fetch"]) < CACHE_TTL
    )


def invalidate_cache():
    """Invalidate the cache to force a refresh on next fetch."""
    with _cache_lock:
        _cache["books"] = None
        _cache["books_by_normalized_title"] = {}
        _cache["last_fetch"] = 0


def is_grimmory_available():
    """Check if Grimmory API is configured and accessible."""
    base_url, username, password = get_grimmory_config()
    if not base_url or not username or not password:
        return False

    with _auth_lock:
        has_token = bool(_auth["access_token"])
    if not has_token and not _login():
        return False

    try:
        r = _authenticated_get(f"{base_url}/api/v1/libraries", timeout=10)
        return r is not None and r.status_code == 200
    except requests.RequestException:
        return False


def scan_all_libraries():
    """Trigger a rescan on all Grimmory libraries to discover new books.

    Returns True if at least one library was scanned successfully.
    """
    base_url, username, password = get_grimmory_config()
    if not base_url or not username or not password:
        logger.warning("Grimmory not configured, cannot trigger library scan")
        return False

    with _auth_lock:
        has_token = bool(_auth["access_token"])
    if not has_token and not _login():
        return False

    session = _get_session()

    try:
        r = _authenticated_get(f"{base_url}/api/v1/libraries", timeout=10)
        if r is None or r.status_code != 200:
            logger.error(f"Failed to get Grimmory libraries: {r.status_code if r else 'None'}")
            return False

        libraries = r.json()
        if not libraries:
            logger.warning("No Grimmory libraries found")
            return False

        scanned = 0
        for library in libraries:
            lib_id = library.get("id")
            lib_name = library.get("name", "Unknown")
            if not lib_id:
                continue

            headers = _get_headers()
            r = session.put(
                f"{base_url}/api/v1/libraries/{lib_id}/refresh",
                headers=headers,
                timeout=10,
            )
            if r.status_code in [200, 202, 204]:
                logger.debug(f"Triggered scan for Grimmory library: {lib_name}")
                scanned += 1
            else:
                logger.warning(f"Failed to scan Grimmory library {lib_name}: {r.status_code}")

        if scanned > 0:
            logger.info(f"Triggered Grimmory library scan ({scanned} libraries)")
            invalidate_cache()
            return True
        return False

    except requests.RequestException as e:
        logger.error(f"Failed to trigger Grimmory library scan: {e}")
        return False


def _map_status(read_status):
    """Map Grimmory readStatus enum to standard status string."""
    if read_status == "READ":
        return "Completed"
    if read_status in ("READING", "RE_READING", "PARTIALLY_READ"):
        return "Reading"
    return "Unread"


def get_all_books(force_refresh=False):
    """Fetch all books with reading progress from Grimmory.

    Uses caching to avoid repeated API calls. Cache is valid for CACHE_TTL seconds.
    """
    with _cache_lock:
        if not force_refresh and _is_cache_valid():
            return _cache["books"]

    base_url, username, password = get_grimmory_config()
    if not base_url or not username or not password:
        return None

    with _auth_lock:
        has_token = bool(_auth["access_token"])
    if not has_token and not _login():
        return None

    books = []
    page = 0

    logger.debug(f"Fetching books from Grimmory: {base_url}")
    try:
        while True:
            r = _authenticated_get(
                f"{base_url}/api/v1/app/books",
                params={"page": page, "size": 100},
                timeout=30,
            )
            if r is None:
                return None
            r.raise_for_status()
            data = r.json()
            content = data.get("content", [])
            logger.debug(f"Fetched page {page}, got {len(content)} books")

            for book in content:
                read_status = book.get("readStatus") or "UNREAD"
                read_progress = book.get("readProgress") or 0.0
                page_count = book.get("pageCount") or 0
                file_type = (book.get("primaryFileType") or "").upper()
                title = book.get("title") or "Unknown"

                pages_read = round(read_progress * page_count) if page_count else 0
                status = _map_status(read_status)

                books.append({
                    "id": book["id"],
                    "title": title,
                    "pages_read": pages_read,
                    "pages_total": page_count,
                    "status": status,
                    "chapter": None,
                    "format": (
                        "EPUB" if file_type == "EPUB"
                        else "PDF" if file_type == "PDF"
                        else "Other"
                    ),
                    "_file_type": file_type,
                })

            if not data.get("hasNext", False):
                break
            page += 1

        with _cache_lock:
            _cache["books"] = books
            _cache["last_fetch"] = time.time()
            _cache["books_by_normalized_title"] = {}
            for book in books:
                normalized = normalize_title(book["title"])
                _cache["books_by_normalized_title"][normalized] = book

        logger.debug(f"Grimmory: fetched {len(books)} books")
        return books

    except requests.RequestException as e:
        logger.error(f"Failed to fetch books from Grimmory: {e}")
        if _cache["books"] is not None:
            logger.warning("Returning stale cache due to fetch error")
            return _cache["books"]
        return None


def _flatten_toc(toc_item):
    """Recursively flatten a nested EpubTocItem tree into (label, href) tuples."""
    result = []
    if not toc_item:
        return result

    label = toc_item.get("label", "")
    href = toc_item.get("href", "")
    if label and href:
        result.append((label, href))

    for child in toc_item.get("children") or []:
        result.extend(_flatten_toc(child))

    return result


def _extract_href_variants(href):
    """Extract multiple href variants for matching."""
    if not href:
        return []

    variants = [href]

    # Strip query string and fragment
    clean = href.split("?")[0].split("#")[0]
    if clean and clean not in variants:
        variants.append(clean)

    # Basename of original
    if "/" in href:
        variants.append(href.rsplit("/", 1)[-1])

    # Basename of clean
    if "/" in clean:
        base = clean.rsplit("/", 1)[-1]
        if base and base not in variants:
            variants.append(base)

    return [v for v in variants if v]


def get_current_chapter(book_id, current_page, pages_total):
    """Get current chapter info for EPUB books.

    Returns dict with chapter_name, chapter_num, and num_chapters, or None.
    """
    base_url, _, _ = get_grimmory_config()
    if not base_url:
        return None

    try:
        r = _authenticated_get(
            f"{base_url}/api/v1/epub/{book_id}/info",
            timeout=10,
        )
        if r is None or r.status_code != 200:
            return None

        epub_info = r.json()
        toc_root = epub_info.get("toc")
        if not toc_root:
            return None

        flat_toc = _flatten_toc(toc_root)
        if not flat_toc:
            return None

        num_chapters = len(flat_toc)

        # Build href variant → (chapter_name, chapter_num) mapping
        href_to_chapter = {}
        for i, (label, href) in enumerate(flat_toc):
            chapter_info = (label, i + 1)
            for variant in _extract_href_variants(href):
                href_to_chapter[variant] = chapter_info

        # Use readProgress float from the progress endpoint for chapter estimation.
        # Grimmory's GET /progress returns {"readProgress": 0-1, "readStatus": ...}
        # — there is no epubProgress.href field in the response.
        read_progress = 0.0
        r = _authenticated_get(
            f"{base_url}/api/v1/app/books/{book_id}/progress",
            timeout=10,
        )
        if r is not None and r.status_code == 200:
            progress_data = r.json()
            # Keep href attempt in case a future API version adds it
            epub_progress = progress_data.get("epubProgress") or {}
            prog_href = epub_progress.get("href", "")
            if prog_href:
                for variant in _extract_href_variants(prog_href):
                    chapter_info = href_to_chapter.get(variant)
                    if chapter_info:
                        logger.debug(f"Found chapter from epubProgress.href: {chapter_info[0]}")
                        return {
                            "chapter_name": chapter_info[0],
                            "chapter_num": chapter_info[1],
                            "num_chapters": num_chapters,
                        }
            read_progress = progress_data.get("readProgress") or 0.0

        # Estimate chapter from readProgress (0-1 float) — primary path for Grimmory
        if read_progress > 0 and num_chapters > 0:
            estimated = max(1, min(num_chapters, int(read_progress * num_chapters) + 1))
        elif pages_total > 0 and num_chapters > 0:
            estimated = max(1, min(num_chapters, int(current_page / pages_total * num_chapters) + 1))
        else:
            estimated = 1

        chapter_name = flat_toc[estimated - 1][0] if estimated <= len(flat_toc) else flat_toc[0][0]
        return {
            "chapter_name": chapter_name,
            "chapter_num": estimated,
            "num_chapters": num_chapters,
        }

    except requests.RequestException as e:
        logger.error(f"Failed to get chapter for book {book_id}: {e}")
        return None


def normalize_title(title):
    """Normalize a title for comparison."""
    if title.lower().endswith(".epub"):
        title = title[:-5]
    title = re.sub(r"\s*--\s*.*$", "", title)
    title = title.replace("_", " ").replace("-", " ")
    title = re.sub(r"\s*\(.*?\)", "", title)
    title = re.sub(r'["\'“”‘’]', "", title)
    title = re.sub(r"\s+", " ", title)
    return title.lower().strip()


def _enrich_with_chapter(book):
    """Add chapter info to book if it's an EPUB with reading progress."""
    result = dict(book)
    result.pop("_file_type", None)

    if result.get("format") == "EPUB":
        page_for_lookup = result["pages_read"] if result["pages_read"] > 0 else 1
        chapter_info = get_current_chapter(
            book["id"], page_for_lookup, result["pages_total"]
        )
        if chapter_info:
            result["num_chapters"] = chapter_info["num_chapters"]
            result["chapter"] = chapter_info.get("chapter_name")
            result["current_chapter_num"] = chapter_info["chapter_num"]

    return result


def find_book_progress(search_title):
    """Find a book by fuzzy title match and return its progress.

    Uses cached book data to avoid repeated API calls.
    """
    logger.debug(f"Looking up Grimmory progress for: {search_title}")
    books = get_all_books()
    if books is None:
        return {"error": "not_configured"}

    search_normalized = normalize_title(search_title)

    # Fast path: exact match from index
    with _cache_lock:
        if search_normalized in _cache["books_by_normalized_title"]:
            book = _cache["books_by_normalized_title"][search_normalized]
            return _enrich_with_chapter(book)

    best_match = None
    best_score = 0

    for book in books:
        title_normalized = normalize_title(book["title"])

        if title_normalized == search_normalized:
            return _enrich_with_chapter(book)

        if title_normalized in search_normalized:
            score = len(title_normalized) / len(search_normalized)
            if search_normalized.startswith(title_normalized):
                score += 0.5
            if score > best_score:
                best_score = score
                best_match = book
            continue
        elif search_normalized in title_normalized:
            score = len(search_normalized) / len(title_normalized)
            if score > best_score:
                best_score = score
                best_match = book
            continue

        search_words = set(search_normalized.split())
        title_words = set(title_normalized.split())
        common_words = search_words & title_words

        if len(common_words) >= 2:
            score = len(common_words) / max(len(search_words), len(title_words))
            if score > best_score:
                best_score = score
                best_match = book

    if best_match and best_score > 0.3:
        return _enrich_with_chapter(best_match)

    return {"error": "not_found"}


def get_chapters_for_book(search_title):
    """Get the full chapter list (TOC) from Grimmory for a book.

    Uses the same fuzzy matching as find_book_progress.
    Returns list of chapter name strings, or None if not available.
    """
    logger.debug(f"Looking up Grimmory chapters for: {search_title}")
    books = get_all_books()
    if books is None:
        return None

    search_normalized = normalize_title(search_title)

    book_id = None
    file_type = None

    # Fast path: exact match from index
    with _cache_lock:
        if search_normalized in _cache["books_by_normalized_title"]:
            b = _cache["books_by_normalized_title"][search_normalized]
            book_id = b["id"]
            file_type = b.get("_file_type", "")
            logger.debug(f"get_chapters_for_book: Exact cache match for '{search_normalized}'")

    # Fuzzy matching if no exact match
    if not book_id:
        best_match = None
        best_score = 0

        for book in books:
            title_normalized = normalize_title(book["title"])

            if title_normalized == search_normalized:
                book_id = book["id"]
                file_type = book.get("_file_type", "")
                break

            if title_normalized in search_normalized:
                score = len(title_normalized) / len(search_normalized)
                if search_normalized.startswith(title_normalized):
                    score += 0.5
                if score > best_score:
                    best_score = score
                    best_match = book
            elif search_normalized in title_normalized:
                score = len(search_normalized) / len(title_normalized)
                if score > best_score:
                    best_score = score
                    best_match = book
            else:
                search_words = set(search_normalized.split())
                title_words = set(title_normalized.split())
                common_words = search_words & title_words
                if len(common_words) >= 2:
                    score = len(common_words) / max(len(search_words), len(title_words))
                    if score > best_score:
                        best_score = score
                        best_match = book

        if best_match and best_score > 0.3:
            book_id = best_match["id"]
            file_type = best_match.get("_file_type", "")
            logger.debug(f"get_chapters_for_book: Fuzzy matched '{best_match['title']}' (score={best_score:.2f})")

    if not book_id:
        logger.debug(f"get_chapters_for_book: No book found for '{search_title}'")
        return None

    if (file_type or "").upper() != "EPUB":
        return None

    base_url, _, _ = get_grimmory_config()
    if not base_url:
        return None

    try:
        r = _authenticated_get(
            f"{base_url}/api/v1/epub/{book_id}/info",
            timeout=10,
        )
        if r is None or r.status_code != 200:
            return None

        epub_info = r.json()
        toc_root = epub_info.get("toc")
        if not toc_root:
            return None

        flat_toc = _flatten_toc(toc_root)
        chapters = [label for label, href in flat_toc]
        logger.debug(f"get_chapters_for_book: Found {len(chapters)} chapters for book_id={book_id}")
        return chapters if chapters else None

    except Exception as e:
        logger.error(f"Error getting Grimmory chapters: {e}")
        return None


def set_progress_to_chapter(book_name, filename, target_chapter):
    """Set Grimmory reading progress to a specific chapter number."""
    base_url, _, _ = get_grimmory_config()
    if not base_url:
        return False

    book_id = _find_book_id(filename) or _find_book_id(book_name)
    if not book_id:
        logger.error(f"Book not found in Grimmory: {book_name}")
        return False

    try:
        r = _authenticated_get(f"{base_url}/api/v1/epub/{book_id}/info", timeout=10)
        if r is None or r.status_code != 200:
            logger.error(f"Could not get EPUB info for Grimmory book {book_id}")
            return False

        epub_info = r.json()
        toc_root = epub_info.get("toc")
        flat_toc = _flatten_toc(toc_root) if toc_root else []
        num_chapters = len(flat_toc)

        if num_chapters > 0 and target_chapter <= num_chapters:
            label, href = flat_toc[target_chapter - 1]
            percentage = round((target_chapter - 1) / num_chapters, 4)
        else:
            href = ""
            percentage = round((target_chapter - 1) / max(target_chapter, 1), 4)

        progress_payload = {
            "epubProgress": {
                "href": href,
                "percentage": percentage,
            },
            "progressValid": True,
        }

        r = _authenticated_put(
            f"{base_url}/api/v1/app/books/{book_id}/progress",
            json=progress_payload,
            timeout=10,
        )
        if r is None or r.status_code not in [200, 204]:
            logger.error(f"Failed to set Grimmory progress for {book_name}: {r.status_code if r else 'None'}")
            return False

        logger.debug(f"Set Grimmory progress for {book_name} to chapter {target_chapter}")
        invalidate_cache()
        return True

    except requests.RequestException as e:
        logger.error(f"Error setting Grimmory progress for {book_name}: {e}")
        return False


def mark_as_completed(book_name, filename):
    """Mark a Grimmory book as completed (READ status)."""
    base_url, _, _ = get_grimmory_config()
    if not base_url:
        return False

    book_id = _find_book_id(filename) or _find_book_id(book_name)
    if not book_id:
        logger.error(f"Book not found in Grimmory for completion: {book_name}")
        return False

    try:
        r = _authenticated_put(
            f"{base_url}/api/v1/app/books/{book_id}/status",
            json={"status": "READ"},
            timeout=10,
        )
        if r is None or r.status_code not in [200, 204]:
            logger.error(f"Failed to mark Grimmory book as completed: {book_name}")
            return False

        logger.debug(f"Marked Grimmory book as completed: {book_name}")
        invalidate_cache()
        return True

    except requests.RequestException as e:
        logger.error(f"Error marking Grimmory book as completed for {book_name}: {e}")
        return False
