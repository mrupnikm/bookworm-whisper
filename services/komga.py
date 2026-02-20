import os
import re
import time
import logging
from threading import Lock

import requests

logger = logging.getLogger(__name__)

# Cache for book data
_cache = {
    "books": None,
    "books_by_normalized_title": {},
    "last_fetch": 0,
}
_cache_lock = Lock()
CACHE_TTL = 60  # seconds


def get_komga_config():
    """Get Komga configuration from environment."""
    url = os.environ.get("KOMGA_URL", "")
    api_key = os.environ.get("KOMGA_API_KEY", "")
    return url, api_key


def _get_session():
    """Get a requests session with connection pooling."""
    if not hasattr(_get_session, "_session"):
        _get_session._session = requests.Session()
    return _get_session._session


def _is_cache_valid():
    """Check if cache is still valid."""
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


def is_komga_available():
    """Check if Komga API is configured and accessible.
    
    Returns True if Komga is configured and responding to API calls.
    """
    base_url, api_key = get_komga_config()
    if not base_url or not api_key:
        return False
    
    session = _get_session()
    headers = {"X-API-Key": api_key}
    
    try:
        # Simple health check - try to get libraries
        r = session.get(
            f"{base_url}/api/v1/libraries",
            headers=headers,
            timeout=10,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def scan_all_libraries():
    """Trigger a scan on all Komga libraries to discover new books.

    Returns True if at least one library was scanned successfully.
    """
    base_url, api_key = get_komga_config()
    if not base_url or not api_key:
        logger.warning("Komga not configured, cannot trigger library scan")
        return False

    session = _get_session()
    headers = {"X-API-Key": api_key}

    try:
        # Get all libraries
        r = session.get(
            f"{base_url}/api/v1/libraries",
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            logger.error(f"Failed to get Komga libraries: {r.status_code}")
            return False

        libraries = r.json()
        if not libraries:
            logger.warning("No Komga libraries found")
            return False

        # Trigger scan on each library
        scanned = 0
        for library in libraries:
            lib_id = library.get("id")
            lib_name = library.get("name", "Unknown")
            if not lib_id:
                continue

            r = session.post(
                f"{base_url}/api/v1/libraries/{lib_id}/scan",
                headers=headers,
                timeout=10,
            )
            if r.status_code in [200, 202, 204]:
                logger.debug(f"Triggered scan for Komga library: {lib_name}")
                scanned += 1
            else:
                logger.warning(f"Failed to scan Komga library {lib_name}: {r.status_code}")

        if scanned > 0:
            logger.info(f"Triggered Komga library scan ({scanned} libraries)")
            # Invalidate cache since new books may be discovered
            invalidate_cache()
            return True
        return False

    except requests.RequestException as e:
        logger.error(f"Failed to trigger Komga library scan: {e}")
        return False


def get_all_books(force_refresh=False):
    """Fetch all books with reading progress from Komga.

    Uses caching to avoid repeated API calls. Cache is valid for CACHE_TTL seconds.
    """
    with _cache_lock:
        if not force_refresh and _is_cache_valid():
            return _cache["books"]

    base_url, api_key = get_komga_config()
    if not base_url or not api_key:
        return None

    session = _get_session()
    headers = {"X-API-Key": api_key}
    books = []
    page = 0

    logger.debug(f"Fetching books from Komga: {base_url}")
    try:
        while True:
            r = session.get(
                f"{base_url}/api/v1/books",
                headers=headers,
                params={"page": page, "size": 100},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            logger.debug(f"Fetched page {page}, got {len(data['content'])} books")

            for book in data["content"]:
                progress = book.get("readProgress")
                pages_total = book.get("media", {}).get("pagesCount", 0)
                media_type = book.get("media", {}).get("mediaType", "")

                if progress:
                    pages_read = progress.get("page", 0)
                    completed = progress.get("completed", False)
                    if completed:
                        status = "Completed"
                    elif pages_read > 0:
                        status = "Reading"
                    else:
                        status = "Unread"
                else:
                    pages_read = 0
                    status = "Unread"

                title = book.get("metadata", {}).get("title", book.get("name", "Unknown"))

                books.append({
                    "id": book["id"],
                    "title": title,
                    "pages_read": pages_read,
                    "pages_total": pages_total,
                    "status": status,
                    "chapter": None,  # Fetched lazily if needed
                    "format": (
                        "EPUB" if "epub" in media_type
                        else "PDF" if "pdf" in media_type
                        else "Other"
                    ),
                    "_media_type": media_type,
                })

            if data.get("last", True):
                break
            page += 1

        # Update cache
        with _cache_lock:
            _cache["books"] = books
            _cache["last_fetch"] = time.time()
            # Build normalized title index
            _cache["books_by_normalized_title"] = {}
            for book in books:
                normalized = normalize_title(book["title"])
                _cache["books_by_normalized_title"][normalized] = book

        logger.debug(f"Komga: fetched {len(books)} books")
        return books

    except requests.RequestException as e:
        logger.error(f"Failed to fetch books from Komga: {e}")
        # Return cached data if available, even if stale
        if _cache["books"] is not None:
            logger.warning("Returning stale cache due to fetch error")
            return _cache["books"]
        return None


def _extract_href_variants(href):
    """Extract multiple href variants for matching.

    Returns a list of possible hrefs to match against.
    """
    variants = [href]

    # Extract path after /resource/
    if "/resource/" in href:
        resource_path = href.split("/resource/")[-1]
        variants.append(resource_path)

    # Get basename (last path component)
    if "/" in href:
        basename = href.rsplit("/", 1)[-1]
        variants.append(basename)

    # Remove query string and fragment
    for i, v in enumerate(list(variants)):
        if "?" in v:
            variants.append(v.split("?")[0])
        if "#" in v:
            variants.append(v.split("#")[0])

    return variants


def _flatten_toc_with_href(toc_items):
    """Flatten TOC hierarchy into a list of (title, href) tuples including children."""
    result = []
    for item in toc_items:
        title = item.get("title", "")
        href = item.get("href", "")
        if title and href:
            result.append((title, href))
        # Recurse into children
        children = item.get("children", [])
        if children:
            result.extend(_flatten_toc_with_href(children))
    return result


def _extract_chapters_from_reading_order(reading_order):
    """Extract chapter info from reading order when TOC is empty.

    Looks for chapter patterns in filenames like _c01_, _c02_, chapter1, etc.
    Returns list of (title, href) tuples, or empty list if extraction unreliable.
    """
    if not reading_order:
        return []

    result = []
    # Pattern to find chapter numbers in filenames
    chapter_pattern = re.compile(r'[_/]c(\d+)[_.]|chapter[_\-]?(\d+)|ch[_\-]?(\d+)', re.IGNORECASE)

    for entry in reading_order:
        href = entry.get("href", "")
        if not href:
            continue

        # Extract filename from href
        filename = href.split("/")[-1] if "/" in href else href

        # Try to find chapter number in filename
        match = chapter_pattern.search(filename)
        if match:
            chapter_num = next((g for g in match.groups() if g is not None), None)
            if chapter_num:
                title = f"Chapter {int(chapter_num)}"
                result.append((title, href))

    # Only use pattern-matched chapters if we found a significant portion (>30%)
    # Otherwise the pattern matching is unreliable for this EPUB
    if len(result) >= len(reading_order) * 0.3:
        return result

    # Pattern matching failed - log for debugging but return empty
    # The caller will use page-based estimation instead
    if reading_order:
        sample_filenames = [e.get("href", "").split("/")[-1] for e in reading_order[:5]]
        logger.info(f"[CHAPTER] Pattern matched only {len(result)}/{len(reading_order)} entries. "
                   f"Sample: {sample_filenames}. Will use page-based estimation.")

    return []


def get_current_chapter(book_id, current_page, pages_total):
    """Get current chapter info for EPUB books.

    Returns dict with chapter_name, chapter_num, and num_chapters, or None.
    """
    base_url, api_key = get_komga_config()
    if not base_url or not api_key:
        return None

    session = _get_session()
    headers = {"X-API-Key": api_key}

    try:
        # Get manifest with TOC
        r = session.get(
            f"{base_url}/api/v1/books/{book_id}/manifest",
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        manifest = r.json()

        toc = manifest.get("toc", [])

        # Flatten TOC to include subchapters
        flattened_toc = _flatten_toc_with_href(toc)

        # Fallback: if TOC is empty, try to extract chapters from reading order
        reading_order = manifest.get("readingOrder", [])
        if not flattened_toc:
            flattened_toc = _extract_chapters_from_reading_order(reading_order)
            if flattened_toc:
                logger.debug(f"Using reading order fallback: {len(flattened_toc)} chapters extracted")

        # If still no chapters, the EPUB has unreliable structure
        # Mark it for incompatibility detection
        if not flattened_toc:
            if reading_order and len(reading_order) > 10:
                # EPUB has many files but no extractable chapters - likely incompatible
                logger.warning(f"[CHAPTER] Book {book_id}: No TOC and pattern matching failed on "
                              f"{len(reading_order)} reading order entries. Marking as potentially incompatible.")
                return {
                    "chapter_name": "Chapter 1",
                    "chapter_num": 1,
                    "num_chapters": 1,
                    "_unreliable_chapters": True,  # Flag for incompatibility detection
                }
            return None

        num_chapters = len(flattened_toc)

        # Build multiple href variant -> (chapter_name, chapter_num) mappings
        href_to_chapter = {}
        for i, (title, href) in enumerate(flattened_toc):
            chapter_info = (title, i + 1)  # 1-indexed chapter num
            for variant in _extract_href_variants(href):
                href_to_chapter[variant] = chapter_info

        # First try to get Readium progression (most accurate for EPUBs)
        r = session.get(
            f"{base_url}/api/v1/books/{book_id}/progression",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            progression_data = r.json()
            locator = progression_data.get("locator", {})
            prog_href = locator.get("href", "")
            if prog_href:
                # Try to match the progression href to a TOC chapter
                for variant in _extract_href_variants(prog_href):
                    chapter_info = href_to_chapter.get(variant)
                    if chapter_info:
                        logger.debug(f"Found chapter from Readium progression: {chapter_info[0]}")
                        return {
                            "chapter_name": chapter_info[0],
                            "chapter_num": chapter_info[1],
                            "num_chapters": num_chapters,
                        }

        # Fallback: Get positions and use page-based calculation
        r = session.get(
            f"{base_url}/api/v1/books/{book_id}/positions",
            headers=headers,
            timeout=10,
        )

        # If positions aren't available, estimate chapter from page progress
        if r.status_code != 200:
            logger.debug(f"Positions not available for book {book_id}, estimating from page progress")
            # Estimate chapter based on page progression
            if pages_total > 0 and num_chapters > 0:
                progress_ratio = current_page / pages_total
                estimated_chapter = max(1, min(num_chapters, int(progress_ratio * num_chapters) + 1))
            else:
                estimated_chapter = 1

            chapter_name = flattened_toc[estimated_chapter - 1][0] if estimated_chapter <= len(flattened_toc) else flattened_toc[0][0]
            return {
                "chapter_name": chapter_name,
                "chapter_num": estimated_chapter,
                "num_chapters": num_chapters,
            }

        positions_data = r.json()
        positions = positions_data.get("positions", [])

        if not positions or current_page < 1:
            # Estimate chapter from page progress if no positions
            if pages_total > 0 and num_chapters > 0 and current_page > 0:
                progress_ratio = current_page / pages_total
                estimated_chapter = max(1, min(num_chapters, int(progress_ratio * num_chapters) + 1))
            else:
                estimated_chapter = 1

            chapter_name = flattened_toc[estimated_chapter - 1][0] if estimated_chapter <= len(flattened_toc) else flattened_toc[0][0]
            return {
                "chapter_name": chapter_name,
                "chapter_num": estimated_chapter,
                "num_chapters": num_chapters,
            }

        # Calculate target progression based on page/total
        target_progression = current_page / pages_total if pages_total > 0 else 0

        # Find position closest to target progression
        best_pos = None
        best_diff = float("inf")
        for pos in positions:
            prog = pos.get("locations", {}).get("totalProgression", 0)
            diff = abs(prog - target_progression)
            if diff < best_diff:
                best_diff = diff
                best_pos = pos

        if best_pos:
            pos_href = best_pos.get("href", "")
            # Try matching with multiple variants of the position href
            for variant in _extract_href_variants(pos_href):
                chapter_info = href_to_chapter.get(variant)
                if chapter_info:
                    return {
                        "chapter_name": chapter_info[0],
                        "chapter_num": chapter_info[1],
                        "num_chapters": num_chapters,
                    }

            # Fallback: find chapter by progression
            # Build list of (progression, chapter_info) for each flattened TOC entry
            toc_progressions = []
            for i, (title, href) in enumerate(flattened_toc):
                toc_variants = _extract_href_variants(href)
                # Find first position that matches this TOC entry
                for pos in positions:
                    pos_variants = _extract_href_variants(pos.get("href", ""))
                    if any(v in toc_variants for v in pos_variants):
                        prog = pos.get("locations", {}).get("totalProgression", 0)
                        toc_progressions.append((prog, title, i + 1))
                        break

            if toc_progressions:
                # Sort by progression and find the chapter we're in
                toc_progressions.sort(key=lambda x: x[0])
                current_chapter = None
                for prog, title, num in toc_progressions:
                    if prog <= target_progression:
                        current_chapter = (title, num)
                    else:
                        break

                if current_chapter:
                    return {
                        "chapter_name": current_chapter[0],
                        "chapter_num": current_chapter[1],
                        "num_chapters": num_chapters,
                    }

        return None

    except requests.RequestException as e:
        logger.error(f"Failed to get chapter for book {book_id}: {e}")
        return None


def normalize_title(title):
    """Normalize a title for comparison."""
    # Remove extension
    if title.lower().endswith(".epub"):
        title = title[:-5]
    # Remove everything after -- BEFORE replacing hyphens
    title = re.sub(r"\s*--\s*.*$", "", title)
    # Replace underscores and hyphens with spaces
    title = title.replace("_", " ").replace("-", " ")
    # Remove common suffixes/patterns
    title = re.sub(r"\s*\(.*?\)", "", title)  # Remove parenthetical content
    # Remove quotation marks and other special characters that break matching
    title = re.sub(r'["\'\u201c\u201d\u2018\u2019]', "", title)  # Remove quotes
    title = re.sub(r"\s+", " ", title)  # Normalize whitespace
    return title.lower().strip()


def find_book_progress(search_title):
    """Find a book by fuzzy title match and return its progress.

    Uses cached book data to avoid repeated API calls.
    """
    logger.debug(f"Looking up Komga progress for: {search_title}")
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

        # Exact match after normalization
        if title_normalized == search_normalized:
            return _enrich_with_chapter(book)

        # Check if one contains the other
        if title_normalized in search_normalized:
            # Komga title is contained in search - prefer shorter/simpler titles
            score = len(title_normalized) / len(search_normalized)
            # Boost score for titles at the start of search term
            if search_normalized.startswith(title_normalized):
                score += 0.5
            if score > best_score:
                best_score = score
                best_match = book
            continue
        elif search_normalized in title_normalized:
            # Search is contained in Komga title
            score = len(search_normalized) / len(title_normalized)
            if score > best_score:
                best_score = score
                best_match = book
            continue

        # Check for word overlap
        search_words = set(search_normalized.split())
        title_words = set(title_normalized.split())
        common_words = search_words & title_words

        if len(common_words) >= 2:  # At least 2 words in common
            score = len(common_words) / max(len(search_words), len(title_words))
            if score > best_score:
                best_score = score
                best_match = book

    if best_match and best_score > 0.3:
        return _enrich_with_chapter(best_match)

    return {"error": "not_found"}


def _enrich_with_chapter(book):
    """Add chapter info to book if it's an EPUB with reading progress."""
    result = dict(book)  # Copy to avoid modifying cache

    # Remove internal fields
    result.pop("_media_type", None)

    # Get chapter info for EPUB books (always get num_chapters, current chapter only if started)
    if "epub" in book.get("_media_type", ""):
        # Pass current_page as 1 if unread, to still get chapter count
        page_for_lookup = result["pages_read"] if result["pages_read"] > 0 else 1
        chapter_info = get_current_chapter(
            book["id"], page_for_lookup, result["pages_total"]
        )
        if chapter_info:
            result["num_chapters"] = chapter_info["num_chapters"]
            # Only set current chapter info if the book has been started
            if result["pages_read"] > 0:
                result["chapter"] = chapter_info["chapter_name"]
                result["current_chapter_num"] = chapter_info["chapter_num"]
            else:
                # For unread books, show first chapter
                result["chapter"] = chapter_info.get("chapter_name")
                result["current_chapter_num"] = 1 if chapter_info["num_chapters"] > 0 else 0

            # Pass through unreliable chapters flag for incompatibility detection
            if chapter_info.get("_unreliable_chapters"):
                result["_unreliable_chapters"] = True

    return result


def get_chapters_for_book(search_title):
    """Get the full chapter list (TOC) from Komga for a book.

    Uses the same fuzzy matching as find_book_progress.
    Returns list of chapter name strings, or None if not available.
    """
    logger.debug(f"Looking up Komga chapters for: {search_title}")
    books = get_all_books()
    if books is None:
        logger.debug("get_chapters_for_book: No books returned from get_all_books")
        return None

    search_normalized = normalize_title(search_title)
    logger.debug(f"get_chapters_for_book: search_normalized='{search_normalized}'")

    # Fast path: exact match from index
    book_id = None
    with _cache_lock:
        if search_normalized in _cache["books_by_normalized_title"]:
            book_id = _cache["books_by_normalized_title"][search_normalized]["id"]
            logger.debug(f"get_chapters_for_book: Exact cache match for '{search_normalized}'")

    # Fuzzy matching if no exact match
    if not book_id:
        best_match = None
        best_score = 0

        for book in books:
            title_normalized = normalize_title(book["title"])

            if title_normalized == search_normalized:
                book_id = book["id"]
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
            logger.debug(f"get_chapters_for_book: Fuzzy matched '{best_match['title']}' (score={best_score:.2f})")

    if not book_id:
        logger.debug(f"get_chapters_for_book: No book found for '{search_title}'")
        return None

    # Get manifest with TOC
    base_url, api_key = get_komga_config()
    if not base_url or not api_key:
        return None

    session = _get_session()
    headers = {"X-API-Key": api_key}

    try:
        r = session.get(
            f"{base_url}/api/v1/books/{book_id}/manifest",
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            return None

        manifest = r.json()
        toc = manifest.get("toc", [])

        # Flatten TOC hierarchy to include subchapters
        def flatten_toc(items, depth=0):
            """Recursively flatten TOC, including children."""
            result = []
            for item in items:
                title = item.get("title", "")
                if title:
                    result.append(title)
                # Recurse into children
                children = item.get("children", [])
                if children:
                    result.extend(flatten_toc(children, depth + 1))
            return result

        chapters = flatten_toc(toc)

        # Fallback: if TOC is empty, try to extract chapters from reading order
        if not chapters:
            reading_order = manifest.get("readingOrder", [])
            extracted = _extract_chapters_from_reading_order(reading_order)
            if extracted:
                chapters = [title for title, href in extracted]
                logger.debug(f"get_chapters_for_book: Using reading order fallback, {len(chapters)} chapters")

        logger.debug(f"get_chapters_for_book: Found {len(chapters)} chapters (flattened) for book_id={book_id}")
        return chapters if chapters else None

    except Exception as e:
        logger.error(f"Error getting Komga chapters: {e}")
        return None
