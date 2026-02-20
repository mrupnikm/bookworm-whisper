import os
import re
import logging

import requests

logger = logging.getLogger(__name__)


def get_audiobookshelf_config():
    """Get Audiobookshelf configuration from environment."""
    url = os.environ.get("AUDIOBOOKSHELF_URL", "")
    api_key = os.environ.get("AUDIOBOOKSHELF_API_KEY", "")
    return url, api_key


def _get_session():
    """Get a requests session with connection pooling."""
    if not hasattr(_get_session, "_session"):
        _get_session._session = requests.Session()
    return _get_session._session


def _clean_chapter_title(title):
    """Clean up chapter title by removing numeric prefixes and underscores."""
    if not title:
        return title
    # Remove numeric prefix pattern like "0001_" or "0002_"
    if len(title) > 5 and title[:4].isdigit() and title[4] == "_":
        title = title[5:]
    # Replace underscores with spaces
    return title.replace("_", " ")


def _get_current_chapter_info(chapters, current_time):
    """Find the current chapter based on playback position.

    Returns tuple of (chapter_title, chapter_number) where chapter_number is 1-indexed.
    """
    if not chapters or current_time <= 0:
        return None, 0

    for i, chapter in enumerate(chapters):
        start = chapter.get("start", 0)
        end = chapter.get("end", 0)
        if start <= current_time < end:
            return _clean_chapter_title(chapter.get("title", "")), i + 1

    # If past all chapters, return the last chapter
    if chapters and current_time >= chapters[-1].get("start", 0):
        return _clean_chapter_title(chapters[-1].get("title", "")), len(chapters)

    return None, 0


def scan_all_libraries():
    """Trigger a scan on all Audiobookshelf libraries to discover new audiobooks.

    Returns True if at least one library was scanned successfully.
    """
    base_url, api_key = get_audiobookshelf_config()
    if not base_url or not api_key:
        logger.warning("Audiobookshelf not configured, cannot trigger library scan")
        return False

    session = _get_session()
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        # Get all libraries
        r = session.get(
            f"{base_url}/api/libraries",
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            logger.error(f"Failed to get Audiobookshelf libraries: {r.status_code}")
            return False

        libraries_data = r.json()
        libraries = libraries_data.get("libraries", [])
        if not libraries:
            logger.warning("No Audiobookshelf libraries found")
            return False

        # Trigger scan on each library
        scanned = 0
        for library in libraries:
            lib_id = library.get("id")
            lib_name = library.get("name", "Unknown")
            if not lib_id:
                continue

            r = session.post(
                f"{base_url}/api/libraries/{lib_id}/scan",
                headers=headers,
                timeout=10,
            )
            if r.status_code in [200, 202, 204]:
                logger.debug(f"Triggered scan for Audiobookshelf library: {lib_name}")
                scanned += 1
            else:
                logger.warning(f"Failed to scan Audiobookshelf library {lib_name}: {r.status_code}")

        if scanned > 0:
            logger.info(f"Triggered Audiobookshelf library scan ({scanned} libraries)")
            return True
        return False

    except requests.RequestException as e:
        logger.error(f"Failed to trigger Audiobookshelf library scan: {e}")
        return False


def get_all_audiobooks():
    """Fetch all audiobooks with listening progress from Audiobookshelf."""
    base_url, api_key = get_audiobookshelf_config()
    if not base_url or not api_key:
        return None

    session = _get_session()
    headers = {"Authorization": f"Bearer {api_key}"}

    logger.debug(f"Fetching audiobooks from Audiobookshelf: {base_url}")
    try:
        # First get all libraries
        r = session.get(f"{base_url}/api/libraries", headers=headers, timeout=30)
        r.raise_for_status()
        libraries_data = r.json()
        logger.debug(f"Found {len(libraries_data.get('libraries', []))} libraries")

        audiobooks = []

        for library in libraries_data.get("libraries", []):
            if library.get("mediaType") != "book":
                continue

            library_id = library["id"]

            # Get all items in this library
            r = session.get(
                f"{base_url}/api/libraries/{library_id}/items",
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            items_data = r.json()

            for item in items_data.get("results", []):
                media = item.get("media", {})
                metadata = media.get("metadata", {})

                audiobook = {
                    "id": item["id"],
                    "title": metadata.get("title", "Unknown"),
                    "folder_name": item.get("relPath", ""),
                    "duration": media.get("duration", 0),
                    "num_chapters": media.get("numChapters", 0),
                    "chapters": [],
                    "current_time": 0,
                    "progress": 0,
                    "status": "Unplayed",
                    "current_chapter": None,
                }

                # Try to get progress for this item
                current_time = 0
                try:
                    pr = session.get(
                        f"{base_url}/api/me/progress/{item['id']}",
                        headers=headers,
                        timeout=10,
                    )
                    if pr.status_code == 200:
                        progress_data = pr.json()
                        current_time = progress_data.get("currentTime", 0)
                        audiobook["current_time"] = current_time
                        audiobook["progress"] = progress_data.get("progress", 0)

                        if progress_data.get("isFinished"):
                            audiobook["status"] = "Finished"
                        elif current_time > 0:
                            audiobook["status"] = "Listening"
                except requests.RequestException:
                    pass  # No progress data for this item

                # Always get chapters for proper chapter count and current chapter info
                try:
                    ir = session.get(
                        f"{base_url}/api/items/{item['id']}?expanded=1",
                        headers=headers,
                        timeout=10,
                    )
                    if ir.status_code == 200:
                        item_data = ir.json()
                        chapters = item_data.get("media", {}).get("chapters", [])
                        audiobook["chapters"] = chapters
                        audiobook["num_chapters"] = len(chapters)

                        # For finished books, always show the last chapter
                        if audiobook["status"] == "Finished" and chapters:
                            last_title = _clean_chapter_title(chapters[-1].get("title", ""))
                            audiobook["current_chapter"] = last_title if last_title else None
                            audiobook["current_chapter_num"] = len(chapters)
                        elif current_time > 0:
                            chapter_title, chapter_num = _get_current_chapter_info(
                                chapters, current_time
                            )
                            audiobook["current_chapter"] = chapter_title
                            audiobook["current_chapter_num"] = chapter_num
                        elif chapters:
                            # For unstarted audiobooks, show first real chapter
                            # Skip the first chapter if it looks like the book title (intro track)
                            book_title_lower = audiobook["title"].lower()
                            first_title = _clean_chapter_title(chapters[0].get("title", ""))

                            # Check if first chapter is the book title (intro)
                            if first_title and first_title.lower() == book_title_lower and len(chapters) > 1:
                                # First chapter is the book title, use second chapter
                                real_first = _clean_chapter_title(chapters[1].get("title", ""))
                                audiobook["current_chapter"] = real_first if real_first else None
                                audiobook["current_chapter_num"] = 2
                            else:
                                audiobook["current_chapter"] = first_title if first_title else None
                                audiobook["current_chapter_num"] = 1
                except requests.RequestException:
                    pass

                audiobooks.append(audiobook)

        logger.debug(f"Audiobookshelf: fetched {len(audiobooks)} audiobooks")
        return audiobooks

    except requests.RequestException as e:
        logger.error(f"Failed to fetch audiobooks from Audiobookshelf: {e}")
        return None


def normalize_title(title):
    """Normalize a title for comparison."""
    # Remove extension
    if title.lower().endswith(".epub"):
        title = title[:-5]
    # Remove everything after --
    title = re.sub(r"\s*--\s*.*$", "", title)
    # Replace underscores and hyphens with spaces
    title = title.replace("_", " ").replace("-", " ")
    # Remove common suffixes/patterns
    title = re.sub(r"\s*\(.*?\)", "", title)  # Remove parenthetical content
    # Remove quotation marks and other special characters that break matching
    title = re.sub(r'["\'\u201c\u201d\u2018\u2019]', "", title)  # Remove quotes
    title = re.sub(r"\s+", " ", title)  # Normalize whitespace
    return title.lower().strip()


def find_audiobook_progress(search_title):
    """Find an audiobook by title match and return its progress."""
    logger.debug(f"Looking up Audiobookshelf progress for: {search_title}")
    audiobooks = get_all_audiobooks()
    if audiobooks is None:
        return {"error": "not_configured"}

    if len(audiobooks) == 0:
        return {"error": "no_audiobooks"}

    search_normalized = normalize_title(search_title)
    search_words = set(search_normalized.split())

    # Priority 1: Exact match
    for audiobook in audiobooks:
        title_normalized = normalize_title(audiobook["title"])
        if title_normalized == search_normalized:
            return _format_audiobook_result(audiobook)

    # Priority 2: Containment check (audiobook title contained in search or vice versa)
    best_match = None
    best_score = 0

    for audiobook in audiobooks:
        title_normalized = normalize_title(audiobook["title"])

        # Check if audiobook title is contained in search (handles truncated titles)
        if title_normalized in search_normalized:
            score = len(title_normalized) / len(search_normalized)
            # Boost score if search starts with title
            if search_normalized.startswith(title_normalized):
                score += 0.5
            if score > best_score:
                best_score = score
                best_match = audiobook
                logger.debug(f"Containment match: {audiobook['title']} (score: {score:.2f})")
            continue
        elif search_normalized in title_normalized:
            score = len(search_normalized) / len(title_normalized)
            if score > best_score:
                best_score = score
                best_match = audiobook
                logger.debug(f"Reverse containment match: {audiobook['title']} (score: {score:.2f})")
            continue

    if best_match and best_score > 0.3:
        return _format_audiobook_result(best_match)

    # Priority 3: Word overlap match
    best_match = None
    best_score = 0

    for audiobook in audiobooks:
        title_normalized = normalize_title(audiobook["title"])
        title_words = set(title_normalized.split())

        if not title_words:
            continue

        # Calculate word overlap
        overlap = search_words.intersection(title_words)
        if not overlap:
            continue

        similarity = len(overlap) / len(search_words)

        # Only consider high similarity matches (60%+)
        if similarity >= 0.6:
            # Additional check: make sure we're not matching to a much longer title
            search_len = len(search_normalized)
            title_len = len(title_normalized)
            length_ratio = min(search_len, title_len) / max(search_len, title_len)

            # Require similar length to avoid series vs book confusion
            if length_ratio >= 0.5:
                score = similarity * length_ratio
                if score > best_score:
                    best_score = score
                    best_match = audiobook
                    logger.debug(f"High similarity match: {audiobook['title']} (score: {score:.2f})")

    if best_match:
        return _format_audiobook_result(best_match)

    return {"error": "not_found"}


# Enhanced version for better book matching
def enhanced_find_audiobook_progress(search_title):
    """Enhanced audiobook progress finding with strict matching to prevent book confusion."""
    logger = __import__('logging').getLogger(__name__)
    
    audiobooks = get_all_audiobooks()
    if audiobooks is None:
        return {"error": "not_configured"}
    
    if len(audiobooks) == 0:
        return {"error": "no_audiobooks"}
    
    search_normalized = normalize_title(search_title)
    
    # Priority 1: Exact match only
    for audiobook in audiobooks:
        title_normalized = normalize_title(audiobook["title"])
        if title_normalized == search_normalized:
            logger.debug(f"Exact match: {audiobook['title']}")
            return _format_audiobook_result(audiobook)
    
    # Priority 2: Very strict partial match for special cases only
    # Only consider if search title is a substring of audiobook title AND much shorter
    # This handles cases like 'test' matching 'test book'
    for audiobook in audiobooks:
        title_normalized = normalize_title(audiobook["title"])
        
        # Check if search is a substring AND significantly shorter
        if (search_normalized in title_normalized and 
            len(search_normalized) <= len(title_normalized) * 0.6):
            logger.debug(f"Substring match: {audiobook['title']} (search is shorter)")
            return _format_audiobook_result(audiobook)
    
    logger.debug(f"No exact match found for: {search_normalized}")
    return {"error": "not_found"}


def _format_audiobook_result(audiobook):
    """Format audiobook data for the API response."""
    return {
        "id": audiobook["id"],
        "title": audiobook["title"],
        "current_time": audiobook["current_time"],
        "total_duration": audiobook["duration"],
        "progress_percent": round(audiobook["progress"] * 100, 1),
        "status": audiobook["status"],
        "current_chapter": audiobook.get("current_chapter"),
        "current_chapter_num": audiobook.get("current_chapter_num", 0),
        "num_chapters": audiobook.get("num_chapters", 0),
    }
