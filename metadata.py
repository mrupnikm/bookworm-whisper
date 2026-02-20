import json
import logging
import os
import re
from datetime import datetime

from config import EPUB_DIR
from services.komga import find_book_progress, get_all_books, invalidate_cache as invalidate_komga_cache
from services.audiobookshelf import find_audiobook_progress

logger = logging.getLogger(__name__)


def normalize_chapter_name(name):
    """Normalize a chapter name for comparison.

    Extracts the core chapter identifier by:
    - Converting to lowercase
    - Replacing underscores with spaces
    - Removing numeric prefixes like "0001_"
    - Extracting chapter numbers (Roman or Arabic)
    """
    if not name:
        return ""

    name = name.lower().strip()

    # Remove numeric prefixes like "0001_" or "0002_"
    name = re.sub(r'^\d{3,4}_', '', name)

    # Replace underscores with spaces
    name = name.replace('_', ' ')

    # Normalize whitespace
    name = re.sub(r'\s+', ' ', name).strip()

    return name


def extract_chapter_number(name):
    """Extract the chapter number from a chapter name.

    Handles formats like:
    - "Chapter 1", "Chapter 1: Title"
    - "CHAPTER I", "CHAPTER II. Title"
    - "Ch. 5", "Ch 10"
    - "1. Title", "10 - Title"

    Returns int or None if no number found.
    """
    if not name:
        return None

    name = name.lower().strip()

    # Roman numeral mapping
    roman_map = {
        'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
        'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10,
        'xi': 11, 'xii': 12, 'xiii': 13, 'xiv': 14, 'xv': 15,
        'xvi': 16, 'xvii': 17, 'xviii': 18, 'xix': 19, 'xx': 20,
        'xxi': 21, 'xxii': 22, 'xxiii': 23, 'xxiv': 24, 'xxv': 25,
        'xxvi': 26, 'xxvii': 27, 'xxviii': 28, 'xxix': 29, 'xxx': 30,
    }

    # Try to match "chapter X" or "ch. X" with Roman numerals
    roman_match = re.search(r'(?:chapter|ch\.?)\s*([ivxl]+)(?:\s|$|[.:\-])', name)
    if roman_match:
        roman = roman_match.group(1)
        if roman in roman_map:
            return roman_map[roman]

    # Try to match "chapter X" or "ch. X" with Arabic numerals
    arabic_match = re.search(r'(?:chapter|ch\.?)\s*(\d+)', name)
    if arabic_match:
        return int(arabic_match.group(1))

    # Try to match just a leading number like "1. Title" or "10 - Title"
    leading_match = re.match(r'^(\d+)[\s.:\-]', name)
    if leading_match:
        return int(leading_match.group(1))

    return None


def find_chapter_by_name(chapter_name, chapters_list, service_name="service"):
    """Find a chapter in a list by matching its name.

    Args:
        chapter_name: The chapter name to search for
        chapters_list: List of chapter dicts with 'title' key (Audiobookshelf)
                      or list of chapter names (Komga TOC)
        service_name: Name of service for logging

    Returns:
        Tuple of (chapter_index_1_based, matched_chapter_name) or (None, None) if not found.
    """
    if not chapter_name or not chapters_list:
        return None, None

    source_normalized = normalize_chapter_name(chapter_name)
    source_chapter_num = extract_chapter_number(chapter_name)

    logger.debug(f"[SYNC] Looking for chapter '{chapter_name}' (normalized: '{source_normalized}', num: {source_chapter_num}) in {service_name}")

    # First pass: try exact normalized match
    for i, chapter in enumerate(chapters_list):
        # Handle both dict format (Audiobookshelf) and string format
        if isinstance(chapter, dict):
            target_name = chapter.get('title', '')
        else:
            target_name = chapter

        target_normalized = normalize_chapter_name(target_name)

        if source_normalized == target_normalized:
            logger.debug(f"[SYNC] Exact match found at position {i + 1}: '{target_name}'")
            return i + 1, target_name

    # Second pass: match by extracted chapter number
    if source_chapter_num is not None:
        for i, chapter in enumerate(chapters_list):
            if isinstance(chapter, dict):
                target_name = chapter.get('title', '')
            else:
                target_name = chapter

            target_chapter_num = extract_chapter_number(target_name)

            if target_chapter_num == source_chapter_num:
                logger.debug(f"[SYNC] Chapter number match ({source_chapter_num}) found at position {i + 1}: '{target_name}'")
                return i + 1, target_name

    # Third pass: fuzzy match - check if one contains the other
    for i, chapter in enumerate(chapters_list):
        if isinstance(chapter, dict):
            target_name = chapter.get('title', '')
        else:
            target_name = chapter

        target_normalized = normalize_chapter_name(target_name)

        # Check if key parts match (ignoring subtitles after : or .)
        source_key = re.split(r'[:.]\s*', source_normalized)[0].strip()
        target_key = re.split(r'[:.]\s*', target_normalized)[0].strip()

        if source_key and target_key and (source_key == target_key or source_key in target_key or target_key in source_key):
            logger.debug(f"[SYNC] Partial match found at position {i + 1}: '{target_name}'")
            return i + 1, target_name

    logger.debug(f"[SYNC] No match found for '{chapter_name}' in {service_name}")
    return None, None


def get_metadata_path(book_name):
    """Get the path to the metadata JSON file for a book."""
    return os.path.join(EPUB_DIR, f"{book_name}.json")


def _mark_komga_incompatible(book_name):
    """Mark a book as Komga-incompatible (e.g., not Divina compatible).

    These books cannot have their progress tracked by Komga's standard API.
    """
    metadata_path = get_metadata_path(book_name)
    metadata = load_metadata(metadata_path)

    metadata["komga_incompatible"] = True
    metadata["last_updated"] = datetime.now().isoformat()

    save_metadata(metadata_path, metadata)
    logger.info(f"Marked {book_name} as Komga-incompatible")


def load_metadata(metadata_path):
    """Load metadata from JSON file, or return empty dict if not exists."""
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Corrupt metadata file {metadata_path}: {e}, returning empty metadata")
            return {}
    return {}


def save_metadata(metadata_path, metadata):
    """Save metadata to JSON file atomically."""
    tmp_path = metadata_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(metadata, f, indent=2)
    os.replace(tmp_path, metadata_path)


def create_metadata_template(book_name, filename):
    """Create a metadata template for a new book with chapter and reading information."""
    metadata_path = get_metadata_path(book_name)
    
    # If file already exists, load it
    if os.path.exists(metadata_path):
        metadata = load_metadata(metadata_path)
    else:
        metadata = {}
    
    # Basic book information
    metadata["book_name"] = book_name
    metadata["source_file"] = filename
    metadata["created_at"] = datetime.now().isoformat()
    metadata["last_updated"] = datetime.now().isoformat()

    # Initialize reading progress tracking
    if "reading_progress" not in metadata:
        metadata["reading_progress"] = {
            "komga": {
                "current_chapter": None,
                "current_page": 0,
                "total_pages": 0,
                "status": "unknown",
                "last_updated": None
            },
            "audiobookshelf": {
                "current_chapter": None,
                "current_position": 0,
                "total_duration": 0,
                "status": "unknown",
                "last_updated": None
            }
        }
    
    # Initialize chapter information - will be populated from Komga data
    if "chapter_info" not in metadata:
        metadata["chapter_info"] = {
            "total_chapters": 0,
            "chapter_list": [],
            "last_scanned": None
        }

    # Initialize sync availability flag
    if "sync_available" not in metadata:
        metadata["sync_available"] = False

    # Initialize sync enabled checkbox (user preference)
    if "sync_enabled" not in metadata:
        metadata["sync_enabled"] = False

    save_metadata(metadata_path, metadata)
    return metadata


def update_reading_progress(book_name, source="komga", **progress_data):
    """Update reading progress for a specific source (komga or audiobookshelf)."""
    metadata_path = get_metadata_path(book_name)
    metadata = load_metadata(metadata_path)
    
    if "reading_progress" not in metadata:
        metadata["reading_progress"] = {}
    
    if source not in metadata["reading_progress"]:
        metadata["reading_progress"][source] = {}
    
    # Update the specific source's progress
    metadata["reading_progress"][source].update(progress_data)
    metadata["reading_progress"][source]["last_updated"] = datetime.now().isoformat()
    
    metadata["last_updated"] = datetime.now().isoformat()
    save_metadata(metadata_path, metadata)
    return metadata


def sync_komga_progress(book_name, filename):
    """Sync Komga reading progress to metadata.

    Returns True if book was found in Komga, False otherwise.
    """
    try:
        komga_result = find_book_progress(filename)

        if isinstance(komga_result, dict) and "error" not in komga_result:
            # Check if the book has unreliable chapter structure
            if komga_result.get("_unreliable_chapters"):
                logger.warning(f"Book {book_name} has unreliable chapter structure - marking as Komga incompatible")
                _mark_komga_incompatible(book_name)

            progress_data = {
                "current_chapter": komga_result.get("chapter"),
                "current_chapter_num": komga_result.get("current_chapter_num", 0),
                "num_chapters": komga_result.get("num_chapters", 0),
                "current_page": komga_result.get("pages_read", 0),
                "total_pages": komga_result.get("pages_total", 0),
                "status": komga_result.get("status", "unknown"),
                "found": True,
            }

            update_reading_progress(book_name, "komga", **progress_data)
            return True
        else:
            # No progress found or error - mark as unavailable
            update_reading_progress(
                book_name, "komga",
                status="unavailable",
                current_chapter=None,
                current_page=0,
                total_pages=0,
                found=False,
            )
            return False
    except Exception as e:
        # Error occurred - mark as error
        update_reading_progress(
            book_name, "komga",
            status="error",
            error=str(e),
            current_chapter=None,
            current_page=0,
            total_pages=0,
            found=False,
        )
        return False


def ensure_metadata_for_all_books():
    """Ensure all EPUB files in the books directory have corresponding JSON metadata files."""
    if not os.path.isdir(EPUB_DIR):
        return []
    
    epub_files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]
    created_files = []
    
    # Get all Komga books data for chapter information
    komga_books = None
    try:
        komga_books = get_all_books()
    except Exception as e:
        logger.warning(f"Could not fetch Komga data: {e}")
    
    for epub_file in epub_files:
        book_name = os.path.splitext(epub_file)[0]
        json_file = f"{book_name}.json"
        
        if not os.path.exists(os.path.join(EPUB_DIR, json_file)):
            # Create metadata template
            metadata = create_metadata_template(book_name, epub_file)
            created_files.append(book_name)
            
            # Try to sync chapter information from Komga if available
            if komga_books:
                try:
                    sync_komga_progress(book_name, epub_file)
                except Exception as e:
                    logger.warning(f"Could not sync Komga progress for {epub_file}: {e}")
    
    return created_files


def sync_all_komga_data():
    """Sync all books' reading progress from Komga."""
    if not os.path.isdir(EPUB_DIR):
        return []

    epub_files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]
    updated_files = []

    for epub_file in epub_files:
        book_name = os.path.splitext(epub_file)[0]
        try:
            sync_komga_progress(book_name, epub_file)
            updated_files.append(book_name)
            logger.debug(f"Synced Komga progress for: {book_name}")
        except Exception as e:
            logger.warning(f"Could not sync Komga progress for {epub_file}: {e}")

    return updated_files


def sync_audiobookshelf_progress(book_name, filename):
    """Sync Audiobookshelf listening progress to metadata.

    Returns True if book was found in Audiobookshelf, False otherwise.
    """
    try:
        abs_result = find_audiobook_progress(book_name)

        if isinstance(abs_result, dict) and "error" not in abs_result:
            progress_data = {
                "current_chapter": abs_result.get("current_chapter"),
                "current_chapter_num": abs_result.get("current_chapter_num", 0),
                "num_chapters": abs_result.get("num_chapters", 0),
                "current_position": abs_result.get("current_time", 0),
                "total_duration": abs_result.get("total_duration", 0),
                "progress_percent": abs_result.get("progress_percent", 0),
                "status": abs_result.get("status", "unknown"),
                "found": True,
            }

            update_reading_progress(book_name, "audiobookshelf", **progress_data)
            return True
        else:
            # No progress found or error
            error_msg = abs_result.get("error", "unknown") if isinstance(abs_result, dict) else "unknown"
            status_map = {
                "not_configured": "unavailable",
                "no_audiobooks": "no_audiobook",
                "not_found": "not_found",
            }
            update_reading_progress(
                book_name, "audiobookshelf",
                status=status_map.get(error_msg, "error"),
                current_chapter=None,
                current_position=0,
                total_duration=0,
                progress_percent=0,
                found=False,
            )
            return False
    except Exception as e:
        update_reading_progress(
            book_name, "audiobookshelf",
            status="error",
            error=str(e),
            current_chapter=None,
            current_position=0,
            total_duration=0,
            progress_percent=0,
            found=False,
        )
        return False


def sync_all_audiobookshelf_data():
    """Sync all books' listening progress from Audiobookshelf."""
    if not os.path.isdir(EPUB_DIR):
        return []

    epub_files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]
    updated_files = []

    for epub_file in epub_files:
        book_name = os.path.splitext(epub_file)[0]
        try:
            sync_audiobookshelf_progress(book_name, epub_file)
            updated_files.append(book_name)
            logger.debug(f"Synced Audiobookshelf progress for: {book_name}")
        except Exception as e:
            logger.warning(f"Could not sync Audiobookshelf progress for {epub_file}: {e}")

    return updated_files


def is_service_found(service_data):
    """Check if a service has valid data (book was found)."""
    if not service_data:
        return False
    # Check explicit found flag first
    if "found" in service_data:
        return service_data["found"]
    # Fall back to checking status for valid values
    status = service_data.get("status", "unknown")
    invalid_statuses = ["unavailable", "error", "not_found", "no_audiobook", "unknown"]
    return status not in invalid_statuses


def update_sync_available(book_name):
    """Update the sync_available flag based on whether book exists on both services."""
    metadata_path = get_metadata_path(book_name)
    metadata = load_metadata(metadata_path)

    reading_progress = metadata.get("reading_progress", {})
    komga_found = is_service_found(reading_progress.get("komga", {}))
    abs_found = is_service_found(reading_progress.get("audiobookshelf", {}))

    sync_available = komga_found and abs_found

    metadata["sync_available"] = sync_available
    if "sync_enabled" not in metadata:
        metadata["sync_enabled"] = False
    metadata["last_updated"] = datetime.now().isoformat()
    save_metadata(metadata_path, metadata)

    return sync_available


def compare_reading_progress(book_name):
    """Compare reading progress between Komga and Audiobookshelf by chapter.

    Uses chapter numbers extracted from chapter NAMES (like "Chapter 5") for comparison,
    which is consistent with how sync_bidirectional_progress determines who is ahead.

    Returns a dict with comparison info, or None if comparison not possible.
    """
    metadata_path = get_metadata_path(book_name)
    metadata = load_metadata(metadata_path)

    if not metadata.get("sync_enabled", False):
        return None

    if not metadata.get("sync_available", False):
        return None

    reading_progress = metadata.get("reading_progress", {})
    komga = reading_progress.get("komga", {})
    audiobookshelf = reading_progress.get("audiobookshelf", {})

    # Get service positions (for display)
    komga_pos = komga.get("current_chapter_num", 0)
    komga_total = komga.get("num_chapters", 0)
    abs_pos = audiobookshelf.get("current_chapter_num", 0)
    abs_total = audiobookshelf.get("num_chapters", 0)

    # Get chapter names
    komga_chapter_name = komga.get("current_chapter", "")
    abs_chapter_name = audiobookshelf.get("current_chapter", "")

    # Extract chapter numbers from names (consistent with sync logic)
    komga_book_chapter = extract_chapter_number(komga_chapter_name)
    abs_book_chapter = extract_chapter_number(abs_chapter_name)

    # Determine comparison result
    if komga_book_chapter is None and abs_book_chapter is None:
        # Can't extract numbers - compare by name or position
        if normalize_chapter_name(komga_chapter_name) == normalize_chapter_name(abs_chapter_name):
            ahead = "equal"
            chapter_diff = 0
        elif komga_pos == 0 and abs_pos > 0:
            ahead = "audiobookshelf"
            chapter_diff = abs_pos
        elif abs_pos == 0 and komga_pos > 0:
            ahead = "komga"
            chapter_diff = komga_pos
        else:
            # Both have progress but can't compare - will use position fallback in sync
            ahead = "uncertain"
            chapter_diff = abs(komga_pos - abs_pos)
    else:
        # Use extracted chapter numbers
        k_ch = komga_book_chapter if komga_book_chapter is not None else 0
        a_ch = abs_book_chapter if abs_book_chapter is not None else 0
        chapter_diff = k_ch - a_ch

        if chapter_diff == 0:
            ahead = "equal"
        elif chapter_diff > 0:
            ahead = "komga"
        else:
            ahead = "audiobookshelf"
        chapter_diff = abs(chapter_diff)

    return {
        "book_name": book_name,
        "komga_chapter": komga_book_chapter,  # Extracted chapter number from name
        "komga_position": komga_pos,  # Service position
        "komga_total_chapters": komga_total,
        "komga_chapter_name": komga_chapter_name,
        "abs_chapter": abs_book_chapter,  # Extracted chapter number from name
        "abs_position": abs_pos,  # Service position
        "abs_total_chapters": abs_total,
        "abs_chapter_name": abs_chapter_name,
        "ahead": ahead,
        "chapter_diff": chapter_diff,
    }


def check_sync_differences():
    """Check and log progress differences for all sync-enabled books."""
    if not os.path.isdir(EPUB_DIR):
        return []

    epub_files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]
    results = []

    for epub_file in epub_files:
        book_name = os.path.splitext(epub_file)[0]
        comparison = compare_reading_progress(book_name)

        if comparison:
            results.append(comparison)

            # Use extracted chapter number if available, otherwise show position
            k_ch = comparison["komga_chapter"]
            k_pos = comparison["komga_position"]
            k_total = comparison["komga_total_chapters"]
            k_name = comparison["komga_chapter_name"] or "?"
            ab_ch = comparison["abs_chapter"]
            ab_pos = comparison["abs_position"]
            ab_total = comparison["abs_total_chapters"]
            ab_name = comparison["abs_chapter_name"] or "?"
            diff = comparison["chapter_diff"]

            # Format chapter info: show extracted number if available, position otherwise
            k_info = f"Ch.{k_ch}" if k_ch is not None else f"pos {k_pos}/{k_total}"
            ab_info = f"Ch.{ab_ch}" if ab_ch is not None else f"pos {ab_pos}/{ab_total}"

            # Only log when there's a difference (not "Same chapter")
            short_name = book_name[:40] + "..." if len(book_name) > 40 else book_name
            if comparison["ahead"] == "komga":
                logger.debug(f"[SYNC] {short_name}: Komga ahead by {diff} ch.")
            elif comparison["ahead"] == "audiobookshelf":
                logger.debug(f"[SYNC] {short_name}: ABS ahead by {diff} ch.")
            elif comparison["ahead"] == "uncertain":
                logger.debug(f"[SYNC] {short_name}: Uncertain - K: {k_pos}/{k_total} | ABS: {ab_pos}/{ab_total}")
            else:
                logger.debug(f"[SYNC] {short_name}: In sync")

    return results


def update_all_sync_available():
    """Update sync_available flag for all books based on existing data."""
    if not os.path.isdir(EPUB_DIR):
        return []

    epub_files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]
    updated = []

    for epub_file in epub_files:
        book_name = os.path.splitext(epub_file)[0]
        if update_sync_available(book_name):
            updated.append(book_name)

    return updated


def get_komga_chapters(book_name, filename):
    """Get the full chapter list (TOC) from Komga for a book.

    Returns list of chapter name strings, or None if not available.
    """
    try:
        from services.komga import get_chapters_for_book

        # Try with filename first (usually more unique), then book_name
        chapters = get_chapters_for_book(filename)
        if chapters is None:
            chapters = get_chapters_for_book(book_name)

        return chapters

    except Exception as e:
        logger.error(f"Error getting Komga chapters for {book_name}: {e}")
        return None


def get_audiobookshelf_chapters(book_name):
    """Get the full chapter list from Audiobookshelf for a book.

    Returns list of chapter dicts with 'title', 'start', 'end' keys, or None if not available.
    """
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    try:
        from examples.mark_audiobookshelf_chapters import find_book_by_title

        audiobook = find_book_by_title(book_name)
        if not audiobook:
            return None

        return audiobook.get("chapters", [])

    except Exception as e:
        logger.error(f"Error getting Audiobookshelf chapters for {book_name}: {e}")
        return None


def set_komga_progress_to_chapter(book_name, filename, target_chapter):
    """Set Komga reading progress to a specific chapter using progression API."""
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    try:
        # Import function from our working example
        from examples.mark_komga_books_as_read import get_book_positions
        from examples.mark_komga_books_as_read import make_api_request
        from examples.mark_komga_books_as_read import get_komga_config
        import datetime
        
        # Get Komga config
        base_url, api_key = get_komga_config()
        if not base_url or not api_key:
            logger.error(f"Komga not configured for {book_name}")
            return False
        
        # Find the book in Komga to get book ID
        books_data, status = make_api_request(f"{base_url}/api/v1/books?unpaged=true&size=10000")
        if status != 200 or not books_data or not books_data.get("content"):
            logger.error(f"Could not fetch Komga books for {book_name}")
            return False
        
        # Find specific book - use both book_name and filename for matching
        book_id = None
        total_pages = 0
        search_terms = [
            book_name.lower(),  # Try exact book name first
            filename.lower().replace('.epub', ''),  # Try filename without extension
            os.path.splitext(filename)[0].lower()  # Try filename without extension
        ]
        
        for book in books_data["content"]:
            book_title = book.get("name", "").lower()
            book_metadata = book.get("metadata", {}).get("title", "").lower()
            
            # Check all search terms against both title and metadata
            for search_term in search_terms:
                if (search_term in book_title or 
                    search_term in book_metadata or 
                    book_title in search_term or 
                    book_metadata in search_term):
                    book_id = book["id"]
                    total_pages = book.get("media", {}).get("pagesCount", 0)
                    logger.debug(f"Found Komga book '{book_title}' for {book_name}")
                    break
            if book_id:
                break
        
        if not book_id:
            logger.error(f"Book {book_name} (tried: {search_terms}) not found in Komga")
            # Debug: log available books
            available_books = [book.get("name", "Unknown") for book in books_data["content"][:5]]
            logger.error(f"Available Komga books (first 5): {available_books}")
            return False
        

        
        # Get manifest (TOC) to find the target chapter's href
        import requests
        session = requests.Session()
        headers = {"X-API-Key": api_key}

        r = session.get(f"{base_url}/api/v1/books/{book_id}/manifest", headers=headers, timeout=10)
        if r.status_code != 200:
            logger.error(f"Could not get manifest for book {book_name}")
            return False

        manifest = r.json()
        toc = manifest.get("toc", [])

        # Get the href for the target chapter (if TOC available)
        target_href = ""
        target_title = ""
        if toc and target_chapter <= len(toc):
            target_toc_entry = toc[target_chapter - 1]
            target_href = target_toc_entry.get("href", "")
            target_title = target_toc_entry.get("title", "")
            logger.debug(f"Target TOC entry: '{target_title}'")

        # Get positions
        positions_data = get_book_positions(book_id)
        positions = positions_data.get("positions", []) if positions_data else []

        # If no positions available, use page-based fallback
        if not positions:
            logger.info(f"Book {book_name} has no positions - using page-based progress fallback")

            # Get num_chapters from reading order if TOC is empty
            reading_order = manifest.get("readingOrder", [])
            num_chapters = len(toc) if toc else len(reading_order)

            if num_chapters > 0 and total_pages > 0:
                # Calculate target page based on chapter progress
                progress_ratio = target_chapter / num_chapters
                target_page = max(1, min(int(progress_ratio * total_pages), total_pages))

                logger.info(f"Page-based sync: chapter {target_chapter}/{num_chapters} = {progress_ratio:.1%} -> page {target_page}/{total_pages}")

                # Set page-based progress
                page_progress_data = {
                    "page": target_page,
                    "completed": target_chapter >= num_chapters
                }

                page_data, page_status = make_api_request(
                    f"{base_url}/api/v1/books/{book_id}/read-progress",
                    method="PATCH",
                    data=page_progress_data
                )

                if page_status in [200, 204]:
                    logger.info(f"Set Komga page progress for {book_name} to page {target_page}/{total_pages}")
                    invalidate_komga_cache()
                    return True
                else:
                    # Check for Divina compatibility error
                    error_msg = str(page_data) if page_data else ""
                    if "Divina compatible" in error_msg or "not Divina" in error_msg:
                        logger.warning(f"Book {book_name} is not Divina compatible - Komga cannot track progress for this EPUB format")
                        # Mark book as incompatible in metadata
                        _mark_komga_incompatible(book_name)
                        return False
                    logger.error(f"Failed to set Komga page progress. Status: {page_status}, Response: {page_data}")
                    return False
            else:
                logger.warning(f"Cannot calculate page progress for {book_name}: chapters={num_chapters}, pages={total_pages}")
                return False

        # Find the position that matches the target href
        target_position = None

        # Extract basename and variants for matching
        def get_href_variants(href):
            variants = [href]
            # Extract resource path from Komga URLs (e.g., .../resource/OEBPS/text/file.xhtml)
            if "/resource/" in href:
                resource_path = href.split("/resource/")[-1]
                variants.append(resource_path)
                # Also without fragment
                if "#" in resource_path:
                    variants.append(resource_path.split("#")[0])
            if "/" in href:
                variants.append(href.rsplit("/", 1)[-1])
            if "#" in href:
                variants.append(href.split("#")[0])
            if "?" in href:
                variants.append(href.split("?")[0])
            return list(set(variants))  # Remove duplicates

        target_variants = get_href_variants(target_href)

        for pos in positions:
            pos_href = pos.get("href", "")
            pos_variants = get_href_variants(pos_href)

            # Check if any variant matches
            if any(tv in pos_variants or pv in target_variants for tv in target_variants for pv in pos_variants):
                target_position = pos
                logger.debug(f"Found matching position for href: {pos_href}")
                break

        if not target_position:
            # Fallback: use position by index
            logger.debug(f"Using position index {target_chapter - 1} as fallback")
            if len(positions) >= target_chapter:
                target_position = positions[target_chapter - 1]
            else:
                target_position = positions[-1]

        # Prepare progression data (using Readium progression format)
        progression_data = {
            "locator": {
                "href": target_position.get("href", ""),
                "type": target_position.get("type", "application/xhtml+xml"),
                "locations": {
                    "progression": target_position.get("locations", {}).get("progression", 0.0),
                    "totalProgression": target_position.get("locations", {}).get("totalProgression", 0.75)
                }
            },
            "device": {
                "id": "bookworm-whisper-sync",
                "name": "BookWorm Whisper Sync"
            },
            "modified": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        data, status = make_api_request(
            f"{base_url}/api/v1/books/{book_id}/progression",
            method="PUT",
            data=progression_data
        )

        if status not in [200, 204]:
            logger.error(f"Failed to set Komga progression for {book_name}. Status: {status}, Response: {data}")
            return False

        # Also update page-based progress so it's reflected when we read back
        # Calculate target page from totalProgression
        total_progression = target_position.get("locations", {}).get("totalProgression", 0)
        target_page = max(1, round(total_progression * total_pages)) if total_pages > 0 else 1

        page_progress_data = {
            "page": target_page,
            "completed": False
        }

        page_data, page_status = make_api_request(
            f"{base_url}/api/v1/books/{book_id}/read-progress",
            method="PATCH",
            data=page_progress_data
        )

        if page_status in [200, 204]:
            logger.debug(f"Set Komga progress for {book_name} to chapter {target_chapter} (page {target_page})")
        else:
            logger.warning(f"Readium progression set, but page progress update failed. Status: {page_status}")

        # Invalidate cache so next fetch gets fresh data
        invalidate_komga_cache()
        return True
            
    except Exception as e:
        logger.error(f"Error setting Komga progress for {book_name}: {e}")
        return False


def set_audiobookshelf_progress_to_chapter(book_name, target_chapter):
    """Set Audiobookshelf listening progress to a specific chapter."""
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    try:
        from examples.mark_audiobookshelf_chapters import find_book_by_title, update_chapter_as_read, get_audiobookshelf_config, make_api_request
        
        # Find audiobook in Audiobookshelf - use flexible matching like Komga
        url, api_key = get_audiobookshelf_config()
        if not url or not api_key:
            logger.error(f"Audiobookshelf not configured for {book_name}")
            return False
        
        # Get library and find audiobook
        audiobook = find_book_by_title(book_name)
        if not audiobook:
            logger.error(f"Audiobook {book_name} not found in Audiobookshelf")
            return False
        
        item_id = audiobook["id"]
        chapters = audiobook["chapters"]
        
        # Mark chapters up to target_chapter as read
        success = update_chapter_as_read(item_id, target_chapter, chapters)
        
        if success:
            logger.debug(f"Successfully set Audiobookshelf progress for {book_name} to chapter {target_chapter}")
            return True
        else:
            logger.error(f"Failed to set Audiobookshelf progress for {book_name}")
            return False
            
    except Exception as e:
        logger.error(f"Error setting Audiobookshelf progress for {book_name}: {e}")
        return False


def mark_komga_as_completed(book_name, filename):
    """Mark a Komga book as completed."""
    try:
        from examples.mark_komga_books_as_read import make_api_request, get_komga_config

        base_url, api_key = get_komga_config()
        if not base_url or not api_key:
            return False

        # Find the book
        books_data, status = make_api_request(f"{base_url}/api/v1/books?unpaged=true&size=10000")
        if status != 200 or not books_data or not books_data.get("content"):
            return False

        book_id = None
        search_terms = [book_name.lower(), filename.lower().replace('.epub', '')]

        for book in books_data["content"]:
            book_title = book.get("name", "").lower()
            book_metadata = book.get("metadata", {}).get("title", "").lower()
            for search_term in search_terms:
                if (search_term in book_title or search_term in book_metadata or
                    book_title in search_term or book_metadata in search_term):
                    book_id = book["id"]
                    break
            if book_id:
                break

        if not book_id:
            return False

        # Mark as completed
        progress_data = {"completed": True}
        _, status = make_api_request(
            f"{base_url}/api/v1/books/{book_id}/read-progress",
            method="PATCH",
            data=progress_data
        )
        return status in [200, 204]

    except Exception as e:
        logger.error(f"Error marking Komga as completed for {book_name}: {e}")
        return False


def mark_audiobookshelf_as_completed(book_name):
    """Mark an Audiobookshelf book as completed."""
    try:
        from examples.mark_audiobookshelf_chapters import find_book_by_title, get_audiobookshelf_config, make_api_request
        import time

        url, api_key = get_audiobookshelf_config()
        if not url or not api_key:
            return False

        audiobook = find_book_by_title(book_name)
        if not audiobook:
            return False

        item_id = audiobook["id"]
        chapters = audiobook.get("chapters", [])
        total_duration = chapters[-1].get("end", 1) if chapters else 1

        progress_data = {
            "currentTime": total_duration,
            "duration": total_duration,
            "progress": 1.0,
            "isFinished": True,
            "finishedAt": int(time.time() * 1000),
            "lastUpdate": int(time.time() * 1000),
        }

        _, status = make_api_request(
            f"{url}/api/me/progress/{item_id}",
            method="PATCH",
            data=progress_data
        )
        return status == 200

    except Exception as e:
        logger.error(f"Error marking Audiobookshelf as completed for {book_name}: {e}")
        return False


def sync_bidirectional_progress(book_name):
    """Perform bi-directional sync using chapter NAME matching.

    Compares chapter names (not numbers) to determine which service is ahead,
    then syncs the behind service to the same chapter by finding the matching
    chapter name in the target service's chapter list.
    """
    metadata_path = get_metadata_path(book_name)
    metadata = load_metadata(metadata_path)

    # Short name for display
    short_name = book_name[:50] + "..." if len(book_name) > 50 else book_name

    if not metadata.get("sync_enabled", False):
        logger.debug(f"[SYNC] {short_name}: Sync disabled")
        return None

    logger.debug(f"[SYNC] Starting sync for: {short_name}")

    filename = metadata.get("source_file", f"{book_name}.epub")

    # Always refresh data before sync to get current state
    sync_komga_progress(book_name, filename)
    sync_audiobookshelf_progress(book_name, filename)

    # Reload metadata after refresh to get current data
    metadata = load_metadata(metadata_path)

    # Check sync availability with fresh data
    if not metadata.get("sync_available", False):
        logger.debug(f"[SYNC] {short_name}: Not available in both services")
        return None

    reading_progress = metadata.get("reading_progress", {})
    komga = reading_progress.get("komga", {})
    audiobookshelf = reading_progress.get("audiobookshelf", {})

    # Validate that both services actually found the book
    komga_found = komga.get("found", False)
    abs_found = audiobookshelf.get("found", False)

    if not komga_found:
        logger.warning(f"[SYNC] {short_name}: Komga book not found")
        return {"action": "failed", "reason": "komga_book_not_found"}

    if not abs_found:
        logger.warning(f"[SYNC] {short_name}: ABS book not found")
        return {"action": "failed", "reason": "audiobookshelf_book_not_found"}

    # Get chapter names and service-specific chapter numbers
    komga_chapter_name = komga.get("current_chapter", "")
    abs_chapter_name = audiobookshelf.get("current_chapter", "")
    komga_svc_num = komga.get("current_chapter_num", 0)
    abs_svc_num = audiobookshelf.get("current_chapter_num", 0)
    komga_total = komga.get("num_chapters", 0)
    abs_total = audiobookshelf.get("num_chapters", 0)
    komga_status = komga.get("status", "")
    abs_status = audiobookshelf.get("status", "")

    logger.debug(f"[SYNC] {short_name}: K #{komga_svc_num}/{komga_total} '{komga_chapter_name}' ({komga_status}) | ABS #{abs_svc_num}/{abs_total} '{abs_chapter_name}' ({abs_status})")

    # ABS uses "Finished", Komga uses "Completed"
    abs_is_completed = abs_status in ["Completed", "Finished"]
    komga_is_completed = komga_status == "Completed"

    # If BOTH services are completed, no sync needed
    if komga_is_completed and abs_is_completed:
        logger.info(f"[SYNC] {short_name}: Both services completed - no sync needed")
        return {"action": "none", "reason": "both_completed"}

    # If both are at their last chapter (100% progress), no sync needed
    komga_at_end = komga_total > 0 and komga_svc_num >= komga_total
    abs_at_end = abs_total > 0 and abs_svc_num >= abs_total
    if komga_at_end and abs_at_end:
        logger.info(f"[SYNC] {short_name}: Both at final chapter - no sync needed")
        return {"action": "none", "reason": "both_at_end"}

    # Handle completed status - sync completion to the other service
    if komga_is_completed and not abs_is_completed:
        logger.info(f"[SYNC] {short_name}: Komga completed -> marking ABS as completed")
        if mark_audiobookshelf_as_completed(book_name):
            sync_audiobookshelf_progress(book_name, filename)
            return {"action": "updated_audiobookshelf", "reason": "synced_completion"}
        else:
            logger.warning(f"[SYNC] {short_name}: Failed to mark ABS as completed")
            return {"action": "failed", "reason": "audiobookshelf_completion_failed"}

    if abs_is_completed and not komga_is_completed:
        logger.info(f"[SYNC] {short_name}: ABS completed ({abs_status}) -> marking Komga as completed")
        if mark_komga_as_completed(book_name, filename):
            sync_komga_progress(book_name, filename)
            return {"action": "updated_komga", "reason": "synced_completion"}
        else:
            logger.warning(f"[SYNC] {short_name}: Failed to mark Komga as completed")
            return {"action": "failed", "reason": "komga_completion_failed"}

    # Extract chapter numbers from the chapter NAMES for comparison
    komga_book_chapter = extract_chapter_number(komga_chapter_name)
    abs_book_chapter = extract_chapter_number(abs_chapter_name)

    # If we can't extract chapter numbers, fall back to comparing normalized names
    if komga_book_chapter is None and abs_book_chapter is None:
        # Compare normalized names - if same, no sync needed
        if normalize_chapter_name(komga_chapter_name) == normalize_chapter_name(abs_chapter_name):
            logger.debug(f"[SYNC] {short_name}: Same chapter - no sync needed")
            return {"action": "none", "reason": "equal", "chapter_name": komga_chapter_name}

        # If one service has progress and the other doesn't, use chapter name matching
        if komga_svc_num == 0 and abs_svc_num > 0:
            logger.debug(f"[SYNC] One-sided progress: ABS at #{abs_svc_num} '{abs_chapter_name}', Komga at start")
            # Find the ABS chapter in Komga's chapter list by name
            komga_chapters = get_komga_chapters(book_name, filename)
            target_komga_num = None
            if komga_chapters:
                logger.debug(f"[SYNC] {short_name}: Komga has {len(komga_chapters)} chapters")
                target_komga_num, matched_name = find_chapter_by_name(abs_chapter_name, komga_chapters, "Komga")
                if target_komga_num:
                    logger.debug(f"[SYNC] Found '{abs_chapter_name}' in Komga at #{target_komga_num}: '{matched_name}'")
            else:
                logger.debug(f"[SYNC] {short_name}: No Komga chapters returned")

            if not target_komga_num:
                # Use chapter list length as fallback for komga_total if not set
                effective_komga_total = komga_total if komga_total > 0 else (len(komga_chapters) if komga_chapters else 0)
                logger.info(f"[SYNC] {short_name}: Fallback check - komga_total={komga_total}, effective={effective_komga_total}, abs_total={abs_total}, abs_svc_num={abs_svc_num}")

                if effective_komga_total > 0 and abs_total > 0:
                    ratio = max(effective_komga_total, abs_total) / min(effective_komga_total, abs_total)
                    if ratio <= 2.0:  # Allow more variance in chapter counts
                        target_komga_num = abs_svc_num
                        logger.info(f"[SYNC] {short_name}: Using position fallback: #{target_komga_num}")
                    else:
                        logger.info(f"[SYNC] {short_name}: Chapter counts differ too much (ratio={ratio:.2f})")
                elif effective_komga_total > 0 and abs_svc_num <= effective_komga_total:
                    # ABS total unknown but position is within Komga's range
                    target_komga_num = abs_svc_num
                    logger.info(f"[SYNC] {short_name}: Using position fallback (ABS total unknown): #{target_komga_num}")
                elif effective_komga_total == 0 and abs_total > 0 and abs_svc_num > 0:
                    # Komga has no chapter data - trust ABS position
                    target_komga_num = abs_svc_num
                    logger.info(f"[SYNC] {short_name}: Komga has no TOC, using ABS position: #{target_komga_num}")

            if target_komga_num:
                if set_komga_progress_to_chapter(book_name, filename, target_komga_num):
                    sync_komga_progress(book_name, filename)
                    logger.debug(f"[SYNC] Updated Komga to ch.{target_komga_num} (from ABS '{abs_chapter_name}')")
                    return {"action": "updated_komga", "target_chapter": target_komga_num, "source_chapter_name": abs_chapter_name}
                else:
                    logger.error(f"[SYNC] Failed to update Komga")
                    return {"action": "failed", "reason": "komga_update_failed"}
            else:
                logger.warning(f"[SYNC] {short_name}: Cannot find matching chapter in Komga for '{abs_chapter_name}'")
                return {"action": "failed", "reason": "chapter_not_found_in_komga"}

        elif abs_svc_num == 0 and komga_svc_num > 0:
            logger.debug(f"[SYNC] One-sided progress: Komga at #{komga_svc_num} '{komga_chapter_name}', ABS at start")
            # Find the Komga chapter in ABS's chapter list by name
            abs_chapters = get_audiobookshelf_chapters(book_name)
            target_abs_num = None
            if abs_chapters:
                logger.debug(f"[SYNC] {short_name}: ABS has {len(abs_chapters)} chapters")
                target_abs_num, matched_name = find_chapter_by_name(komga_chapter_name, abs_chapters, "Audiobookshelf")
                if target_abs_num:
                    logger.debug(f"[SYNC] Found '{komga_chapter_name}' in ABS at #{target_abs_num}: '{matched_name}'")
            else:
                logger.debug(f"[SYNC] {short_name}: No ABS chapters returned")

            if not target_abs_num:
                # Use chapter list length as fallback for abs_total if not set
                effective_abs_total = abs_total if abs_total > 0 else (len(abs_chapters) if abs_chapters else 0)
                logger.info(f"[SYNC] {short_name}: Fallback check - komga_total={komga_total}, abs_total={abs_total}, effective={effective_abs_total}, komga_svc_num={komga_svc_num}")

                if komga_total > 0 and effective_abs_total > 0:
                    ratio = max(komga_total, effective_abs_total) / min(komga_total, effective_abs_total)
                    if ratio <= 2.0:  # Allow more variance
                        target_abs_num = komga_svc_num
                        logger.info(f"[SYNC] {short_name}: Using position fallback: #{target_abs_num}")
                    else:
                        logger.info(f"[SYNC] {short_name}: Chapter counts differ too much (ratio={ratio:.2f})")
                elif effective_abs_total > 0 and komga_svc_num <= effective_abs_total:
                    # Komga total unknown but position is within ABS's range
                    target_abs_num = komga_svc_num
                    logger.info(f"[SYNC] {short_name}: Using position fallback (Komga total unknown): #{target_abs_num}")
                elif effective_abs_total == 0 and komga_total > 0 and komga_svc_num > 0:
                    # ABS has no chapter data - trust Komga position
                    target_abs_num = komga_svc_num
                    logger.info(f"[SYNC] {short_name}: ABS has no TOC, using Komga position: #{target_abs_num}")

            if target_abs_num:
                if set_audiobookshelf_progress_to_chapter(book_name, target_abs_num):
                    sync_audiobookshelf_progress(book_name, filename)
                    logger.debug(f"[SYNC] Updated ABS to ch.{target_abs_num} (from Komga '{komga_chapter_name}')")
                    return {"action": "updated_audiobookshelf", "target_chapter": target_abs_num, "source_chapter_name": komga_chapter_name}
                else:
                    logger.error(f"[SYNC] Failed to update Audiobookshelf")
                    return {"action": "failed", "reason": "audiobookshelf_update_failed"}
            else:
                logger.warning(f"[SYNC] {short_name}: Cannot find matching chapter in ABS for '{komga_chapter_name}'")
                return {"action": "failed", "reason": "chapter_not_found_in_audiobookshelf"}

        else:
            # Both have progress but no chapter numbers in names - use cross-service name matching
            logger.debug(f"[SYNC] Both services have progress, attempting cross-service chapter matching...")

            # Get full chapter lists for name-based matching
            komga_chapters = get_komga_chapters(book_name, filename)
            abs_chapters = get_audiobookshelf_chapters(book_name)

            if not komga_chapters or not abs_chapters:
                logger.warning(f"[SYNC] WARNING: Cannot get chapter lists for cross-matching")
                # Fallback: use service positions if chapter counts are similar
                if komga_total > 0 and abs_total > 0:
                    ratio = max(komga_total, abs_total) / min(komga_total, abs_total)
                    if ratio <= 1.5:  # Very similar chapter counts
                        if komga_svc_num > abs_svc_num:
                            logger.debug(f"[SYNC] Using position fallback: Komga #{komga_svc_num} > ABS #{abs_svc_num}")
                            if set_audiobookshelf_progress_to_chapter(book_name, komga_svc_num):
                                sync_audiobookshelf_progress(book_name, filename)
                                logger.debug(f"[SYNC] SUCCESS: Updated Audiobookshelf to position #{komga_svc_num}")
                                return {"action": "updated_audiobookshelf", "target_chapter": komga_svc_num, "source_chapter_name": komga_chapter_name}
                        elif abs_svc_num > komga_svc_num:
                            logger.debug(f"[SYNC] Using position fallback: ABS #{abs_svc_num} > Komga #{komga_svc_num}")
                            if set_komga_progress_to_chapter(book_name, filename, abs_svc_num):
                                sync_komga_progress(book_name, filename)
                                logger.debug(f"[SYNC] SUCCESS: Updated Komga to position #{abs_svc_num}")
                                return {"action": "updated_komga", "target_chapter": abs_svc_num, "source_chapter_name": abs_chapter_name}
                        else:
                            logger.debug(f"[SYNC] RESULT: Same position - no sync needed")
                            return {"action": "none", "reason": "equal_position"}
                logger.warning(f"[SYNC] WARNING: Cannot compare - chapter names differ but no numbers found")
                return {"action": "failed", "reason": "cannot_compare_chapters"}

            # Find where each service's current chapter appears in the other's list
            komga_in_abs_pos, komga_in_abs_name = find_chapter_by_name(komga_chapter_name, abs_chapters, "Audiobookshelf")
            abs_in_komga_pos, abs_in_komga_name = find_chapter_by_name(abs_chapter_name, komga_chapters, "Komga")

            logger.debug(f"[SYNC] Cross-match: Komga chapter found in ABS at #{komga_in_abs_pos}, ABS chapter found in Komga at #{abs_in_komga_pos}")

            # Determine which is ahead based on cross-service positions
            if komga_in_abs_pos and abs_in_komga_pos:
                # Both chapters found - compare which is further along
                # If Komga's chapter is at position X in ABS, and ABS is currently at Y, X > Y means Komga is ahead
                if komga_in_abs_pos > abs_svc_num:
                    # Komga is ahead - update ABS to where Komga's chapter is
                    logger.debug(f"[SYNC] ACTION: Komga ahead (ch in ABS at #{komga_in_abs_pos} > ABS pos #{abs_svc_num}) -> Updating Audiobookshelf")
                    if set_audiobookshelf_progress_to_chapter(book_name, komga_in_abs_pos):
                        sync_audiobookshelf_progress(book_name, filename)
                        logger.debug(f"[SYNC] SUCCESS: Updated Audiobookshelf to chapter #{komga_in_abs_pos}")
                        return {"action": "updated_audiobookshelf", "target_chapter": komga_in_abs_pos, "source_chapter_name": komga_chapter_name}
                    else:
                        logger.error(f"[SYNC] ERROR: Failed to update Audiobookshelf")
                        return {"action": "failed", "reason": "audiobookshelf_update_failed"}
                elif abs_in_komga_pos > komga_svc_num:
                    # ABS is ahead - update Komga to where ABS's chapter is
                    logger.debug(f"[SYNC] ACTION: ABS ahead (ch in Komga at #{abs_in_komga_pos} > Komga pos #{komga_svc_num}) -> Updating Komga")
                    if set_komga_progress_to_chapter(book_name, filename, abs_in_komga_pos):
                        sync_komga_progress(book_name, filename)
                        logger.debug(f"[SYNC] SUCCESS: Updated Komga to chapter #{abs_in_komga_pos}")
                        return {"action": "updated_komga", "target_chapter": abs_in_komga_pos, "source_chapter_name": abs_chapter_name}
                    else:
                        logger.error(f"[SYNC] ERROR: Failed to update Komga")
                        return {"action": "failed", "reason": "komga_update_failed"}
                else:
                    logger.debug(f"[SYNC] RESULT: Chapters are at equivalent positions - no sync needed")
                    return {"action": "none", "reason": "equal_cross_position"}

            elif komga_in_abs_pos:
                # Only Komga chapter found in ABS - assume Komga is ahead
                if komga_in_abs_pos > abs_svc_num:
                    logger.debug(f"[SYNC] ACTION: Komga ahead (found in ABS at #{komga_in_abs_pos}) -> Updating Audiobookshelf")
                    if set_audiobookshelf_progress_to_chapter(book_name, komga_in_abs_pos):
                        sync_audiobookshelf_progress(book_name, filename)
                        logger.debug(f"[SYNC] SUCCESS: Updated Audiobookshelf to chapter #{komga_in_abs_pos}")
                        return {"action": "updated_audiobookshelf", "target_chapter": komga_in_abs_pos, "source_chapter_name": komga_chapter_name}

            elif abs_in_komga_pos:
                # Only ABS chapter found in Komga - assume ABS is ahead
                if abs_in_komga_pos > komga_svc_num:
                    logger.debug(f"[SYNC] ACTION: ABS ahead (found in Komga at #{abs_in_komga_pos}) -> Updating Komga")
                    if set_komga_progress_to_chapter(book_name, filename, abs_in_komga_pos):
                        sync_komga_progress(book_name, filename)
                        logger.debug(f"[SYNC] SUCCESS: Updated Komga to chapter #{abs_in_komga_pos}")
                        return {"action": "updated_komga", "target_chapter": abs_in_komga_pos, "source_chapter_name": abs_chapter_name}

            # Fallback: use service positions if chapter counts are similar
            if komga_total > 0 and abs_total > 0:
                ratio = max(komga_total, abs_total) / min(komga_total, abs_total)
                if ratio <= 1.5:  # Similar chapter counts
                    if komga_svc_num > abs_svc_num:
                        logger.debug(f"[SYNC] Using position fallback: Komga #{komga_svc_num} > ABS #{abs_svc_num}")
                        if set_audiobookshelf_progress_to_chapter(book_name, komga_svc_num):
                            sync_audiobookshelf_progress(book_name, filename)
                            logger.debug(f"[SYNC] SUCCESS: Updated Audiobookshelf to position #{komga_svc_num}")
                            return {"action": "updated_audiobookshelf", "target_chapter": komga_svc_num, "source_chapter_name": komga_chapter_name}
                    elif abs_svc_num > komga_svc_num:
                        logger.debug(f"[SYNC] Using position fallback: ABS #{abs_svc_num} > Komga #{komga_svc_num}")
                        if set_komga_progress_to_chapter(book_name, filename, abs_svc_num):
                            sync_komga_progress(book_name, filename)
                            logger.debug(f"[SYNC] SUCCESS: Updated Komga to position #{abs_svc_num}")
                            return {"action": "updated_komga", "target_chapter": abs_svc_num, "source_chapter_name": abs_chapter_name}
                    else:
                        logger.debug(f"[SYNC] RESULT: Same position - no sync needed")
                        return {"action": "none", "reason": "equal_position"}

            logger.warning(f"[SYNC] WARNING: Cannot compare chapters - no matching found")
            return {"action": "failed", "reason": "cannot_compare_chapters"}

    # Handle case where one has a chapter number and the other doesn't
    if komga_book_chapter is None:
        komga_book_chapter = 0
    if abs_book_chapter is None:
        abs_book_chapter = 0

    # Determine which service is ahead based on book chapter number
    if komga_book_chapter == abs_book_chapter:
        # Chapter names yield same number, but check if relative positions differ significantly
        # This handles cases where "Chapter 1" appears at different positions in different TOC structures
        komga_progress = komga_svc_num / komga_total if komga_total > 0 else 0
        abs_progress = abs_svc_num / abs_total if abs_total > 0 else 0
        progress_diff = abs(komga_progress - abs_progress)

        # If positions differ by more than 5%, use position-based sync
        if progress_diff > 0.05 and komga_total > 0 and abs_total > 0:
            logger.info(f"[SYNC] {short_name}: Same chapter name but different positions (K:{komga_svc_num}/{komga_total}={komga_progress:.1%}, ABS:{abs_svc_num}/{abs_total}={abs_progress:.1%})")
            if abs_progress > komga_progress:
                # ABS is ahead by position - update Komga
                # Calculate equivalent Komga chapter based on ABS progress
                target_komga_chapter = max(1, int(abs_progress * komga_total) + 1)
                target_komga_chapter = min(target_komga_chapter, komga_total)
                logger.info(f"[SYNC] {short_name}: ABS ahead by position -> Updating Komga to chapter {target_komga_chapter}")
                if set_komga_progress_to_chapter(book_name, filename, target_komga_chapter):
                    sync_komga_progress(book_name, filename)
                    return {"action": "updated_komga", "target_chapter": target_komga_chapter, "reason": "position_based"}
                else:
                    return {"action": "failed", "reason": "komga_update_failed"}
            else:
                # Komga is ahead by position - update ABS
                target_abs_chapter = max(1, int(komga_progress * abs_total) + 1)
                target_abs_chapter = min(target_abs_chapter, abs_total)
                logger.info(f"[SYNC] {short_name}: Komga ahead by position -> Updating ABS to chapter {target_abs_chapter}")
                if set_audiobookshelf_progress_to_chapter(book_name, target_abs_chapter):
                    sync_audiobookshelf_progress(book_name, filename)
                    return {"action": "updated_audiobookshelf", "target_chapter": target_abs_chapter, "reason": "position_based"}
                else:
                    return {"action": "failed", "reason": "audiobookshelf_update_failed"}

        logger.debug(f"[SYNC] RESULT: Both at chapter {komga_book_chapter} - no sync needed")
        return {"action": "none", "reason": "equal", "book_chapter": komga_book_chapter}

    # Get full chapter lists for name-based matching
    komga_chapters = get_komga_chapters(book_name, filename)
    abs_chapters = get_audiobookshelf_chapters(book_name)

    if not komga_chapters:
        logger.warning(f"[SYNC] WARNING: Could not get Komga chapter list")
    if not abs_chapters:
        logger.warning(f"[SYNC] WARNING: Could not get Audiobookshelf chapter list")

    if komga_book_chapter > abs_book_chapter:
        # Komga is ahead - update Audiobookshelf
        diff = komga_book_chapter - abs_book_chapter
        logger.debug(f"[SYNC] ACTION: Komga ahead by {diff} ch. -> Updating Audiobookshelf")

        # Find the matching chapter in Audiobookshelf by name
        target_abs_num = None
        if abs_chapters:
            target_abs_num, matched_name = find_chapter_by_name(komga_chapter_name, abs_chapters, "Audiobookshelf")
            if target_abs_num:
                logger.debug(f"[SYNC] Found matching ABS chapter at #{target_abs_num}: '{matched_name}'")

        if not target_abs_num:
            # Fallback: use service chapter number if chapter lists are similar size
            if komga_total > 0 and abs_total > 0:
                ratio = max(komga_total, abs_total) / min(komga_total, abs_total)
                if ratio <= 2:  # Similar chapter counts
                    target_abs_num = komga_svc_num
                    logger.debug(f"[SYNC] Using Komga position as fallback: #{target_abs_num}")
                else:
                    logger.error(f"[SYNC] ERROR: Cannot match chapters - counts differ too much")
                    return {"action": "failed", "reason": "chapter_not_found_in_audiobookshelf"}
            else:
                logger.error(f"[SYNC] ERROR: Cannot find matching chapter in Audiobookshelf")
                return {"action": "failed", "reason": "chapter_not_found_in_audiobookshelf"}

        if set_audiobookshelf_progress_to_chapter(book_name, target_abs_num):
            sync_audiobookshelf_progress(book_name, filename)
            logger.debug(f"[SYNC] SUCCESS: Updated Audiobookshelf to chapter #{target_abs_num}")
            return {"action": "updated_audiobookshelf", "target_chapter": target_abs_num, "source_chapter_name": komga_chapter_name}
        else:
            logger.error(f"[SYNC] ERROR: Failed to update Audiobookshelf")
            return {"action": "failed", "reason": "audiobookshelf_update_failed"}

    else:
        # Audiobookshelf is ahead - update Komga
        diff = abs_book_chapter - komga_book_chapter
        logger.debug(f"[SYNC] ACTION: Audiobookshelf ahead by {diff} ch. -> Updating Komga")

        # Find the matching chapter in Komga by name
        target_komga_num = None
        if komga_chapters:
            target_komga_num, matched_name = find_chapter_by_name(abs_chapter_name, komga_chapters, "Komga")
            if target_komga_num:
                logger.debug(f"[SYNC] Found matching Komga chapter at #{target_komga_num}: '{matched_name}'")

        if not target_komga_num:
            # Fallback: use service chapter number if chapter lists are similar size
            if komga_total > 0 and abs_total > 0:
                ratio = max(komga_total, abs_total) / min(komga_total, abs_total)
                if ratio <= 2:  # Similar chapter counts
                    target_komga_num = abs_svc_num
                    logger.debug(f"[SYNC] Using ABS position as fallback: #{target_komga_num}")
                else:
                    logger.error(f"[SYNC] ERROR: Cannot match chapters - counts differ too much")
                    return {"action": "failed", "reason": "chapter_not_found_in_komga"}
            else:
                logger.error(f"[SYNC] ERROR: Cannot find matching chapter in Komga")
                return {"action": "failed", "reason": "chapter_not_found_in_komga"}

        if set_komga_progress_to_chapter(book_name, filename, target_komga_num):
            sync_komga_progress(book_name, filename)
            logger.debug(f"[SYNC] SUCCESS: Updated Komga to chapter #{target_komga_num}")
            return {"action": "updated_komga", "target_chapter": target_komga_num, "source_chapter_name": abs_chapter_name}
        else:
            logger.error(f"[SYNC] ERROR: Failed to update Komga")
            return {"action": "failed", "reason": "komga_update_failed"}


def sync_all_bidirectional_progress():
    """Perform bi-directional sync for all sync-enabled books."""
    if not os.path.isdir(EPUB_DIR):
        return []
    
    epub_files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]
    sync_results = []
    
    for epub_file in epub_files:
        book_name = os.path.splitext(epub_file)[0]
        try:
            result = sync_bidirectional_progress(book_name)
            if result:
                sync_results.append({"book_name": book_name, **result})
        except Exception as e:
            logger.error(f"Bi-directional sync failed for {book_name}: {e}")
            sync_results.append({"book_name": book_name, "action": "failed", "reason": "exception", "error": str(e)})
    
    return sync_results


def sync_all_external_data():
    """Sync all books' progress from all external services (Komga + Audiobookshelf)."""
    # Invalidate caches to ensure fresh data
    invalidate_komga_cache()

    if not os.path.isdir(EPUB_DIR):
        return {"komga": [], "audiobookshelf": [], "sync_available": []}

    epub_files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]
    komga_updated = []
    audiobookshelf_updated = []
    sync_available_books = []

    for epub_file in epub_files:
        book_name = os.path.splitext(epub_file)[0]

        # Sync from both services
        komga_found = False
        abs_found = False

        try:
            komga_found = sync_komga_progress(book_name, epub_file)
            komga_updated.append(book_name)
            logger.debug(f"Synced Komga progress for: {book_name}")
        except Exception as e:
            logger.warning(f"Could not sync Komga progress for {epub_file}: {e}")

        try:
            abs_found = sync_audiobookshelf_progress(book_name, epub_file)
            audiobookshelf_updated.append(book_name)
            logger.debug(f"Synced Audiobookshelf progress for: {book_name}")
        except Exception as e:
            logger.warning(f"Could not sync Audiobookshelf progress for {epub_file}: {e}")

        # Update sync_available status
        if komga_found and abs_found:
            update_sync_available(book_name)
            sync_available_books.append(book_name)
        else:
            # Still update the flag even if not available
            update_sync_available(book_name)

    return {
        "komga": komga_updated,
        "audiobookshelf": audiobookshelf_updated,
        "sync_available": sync_available_books,
    }
