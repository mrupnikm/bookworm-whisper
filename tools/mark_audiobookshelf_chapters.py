#!/usr/bin/env python3
"""
Example implementation for updating a chapter as read in Audiobookshelf.

This script demonstrates how to mark a chapter as completed by updating
the progress in Audiobookshelf via the API.
"""

import os
import sys
import logging
import time
import json
import urllib.request
import urllib.error

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_audiobookshelf_config():
    """Get Audiobookshelf configuration from environment."""
    url = os.environ.get("AUDIOBOOKSHELF_URL", "")
    api_key = os.environ.get("AUDIOBOOKSHELF_API_KEY", "")
    return url, api_key


def make_api_request(url, method="GET", data=None):
    """Make HTTP request to Audiobookshelf API."""
    headers = {
        "Authorization": f"Bearer {os.environ.get('AUDIOBOOKSHELF_API_KEY', '')}",
        "Content-Type": "application/json"
    }
    
    try:
        if method == "GET":
            req = urllib.request.Request(url, headers=headers)
        elif method == "POST" or method == "PATCH":
            if data:
                json_data = json.dumps(data).encode('utf-8')
                req = urllib.request.Request(url, data=json_data, headers=headers, method=method)
            else:
                req = urllib.request.Request(url, headers=headers, method=method)
        else:
            req = urllib.request.Request(url, headers=headers, method=method)
        
        with urllib.request.urlopen(req, timeout=30) as response:
            response_text = response.read().decode('utf-8')
            # Handle simple response like "OK"
            try:
                return json.loads(response_text), response.getcode()
            except json.JSONDecodeError:
                return {"response": response_text}, response.getcode()
                
    except urllib.error.HTTPError as e:
        error_data = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
        return {"error": error_data}, e.code
    except Exception as e:
        return {"error": str(e)}, 0


def get_all_libraries():
    """Get all libraries from Audiobookshelf."""
    base_url, _ = get_audiobookshelf_config()
    if not base_url:
        return None
    
    data, status = make_api_request(f"{base_url}/api/libraries")
    return data if status == 200 else None


def get_library_items(library_id):
    """Get all items in a library."""
    base_url, _ = get_audiobookshelf_config()
    if not base_url:
        return None
    
    data, status = make_api_request(f"{base_url}/api/libraries/{library_id}/items")
    return data if status == 200 else None


def get_item_details(item_id):
    """Get detailed item information including chapters."""
    base_url, _ = get_audiobookshelf_config()
    if not base_url:
        return None
    
    data, status = make_api_request(f"{base_url}/api/items/{item_id}?expanded=1")
    return data if status == 200 else None


def update_chapter_as_read(item_id, chapter_num_to_mark, chapters):
    """
    Update Audiobookshelf progress to mark a specific chapter as read.
    
    Args:
        item_id: The Audiobookshelf item ID
        chapter_num_to_mark: Chapter number to mark as read (1-indexed)
        chapters: List of chapter dictionaries with start/end times
    
    Returns:
        bool: True if successful, False otherwise
    """
    base_url, _ = get_audiobookshelf_config()
    if not base_url:
        logger.error("Audiobookshelf configuration missing")
        return False
    
    if not chapters or chapter_num_to_mark < 1 or chapter_num_to_mark > len(chapters):
        logger.error(f"Invalid chapter number: {chapter_num_to_mark}")
        return False
    
    # Get the chapter to set position to
    chapter_to_mark = chapters[chapter_num_to_mark - 1]
    # Use START of chapter so Audiobookshelf reports being AT this chapter, not the next one
    chapter_start_time = chapter_to_mark.get("start", 0)
    total_duration = chapters[-1].get("end", 1)

    # Calculate progress percentage based on start position
    progress_percentage = chapter_start_time / total_duration if total_duration > 0 else 0

    # Check if this is the final chapter
    is_final_chapter = (chapter_num_to_mark == len(chapters))

    # Prepare progress update data - position at START of chapter
    progress_data = {
        "currentTime": chapter_start_time,
        "duration": total_duration,
        "progress": min(progress_percentage, 1.0),  # Cap at 1.0 (100%)
        "isFinished": False,  # Not finished since we're at the start
        "lastUpdate": int(time.time() * 1000),  # Current timestamp in milliseconds
    }
    
    # Only include finishedAt if this is the final chapter
    if is_final_chapter:
        progress_data["finishedAt"] = int(time.time() * 1000)
    
    try:
        logger.debug(f"Updating ABS progress for item {item_id} to chapter {chapter_num_to_mark}")

        data, status = make_api_request(
            f"{base_url}/api/me/progress/{item_id}",
            method="PATCH",  # Use PATCH instead of POST
            data=progress_data
        )

        if status == 200:
            logger.debug(f"Set ABS position to chapter {chapter_num_to_mark}")
            return True
        else:
            logger.error(f"API request failed with status {status}: {data}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to update Audiobookshelf progress: {e}")
        return False


def find_book_by_title(title):
    """
    Find a book by title and return its full details including chapters.
    
    Args:
        title: Book title to search for
    
    Returns:
        dict: Book details including chapters, or None if not found
    """
    logger.debug(f"Searching for book: {title}")

    # Get all libraries
    libraries_data = get_all_libraries()
    if not libraries_data:
        logger.warning("Failed to fetch ABS libraries")
        return None

    # Search through book libraries
    for library in libraries_data.get("libraries", []):
        if library.get("mediaType") != "book":
            continue

        library_id = library["id"]
        logger.debug(f"Checking ABS library: {library.get('name', 'Unknown')}")
        
        # Get all items in this library
        items_data = get_library_items(library_id)
        if not items_data:
            continue
        
        for item in items_data.get("results", []):
            media = item.get("media", {})
            metadata = media.get("metadata", {})
            item_title = metadata.get("title", "")
            
            # Enhanced matching: extract core book names and compare
            import re as re_match
            def normalize_for_match(text):
                # Remove everything after -- (often metadata suffixes)
                text = re_match.sub(r'\s*--\s*.*$', '', text)
                # Replace underscores and hyphens with spaces
                text = text.replace('_', ' ').replace('-', ' ')
                # Remove quotes and special characters that break matching
                text = re_match.sub(r'["\'\u201c\u201d\u2018\u2019]', '', text)
                # Normalize whitespace
                text = re_match.sub(r'\s+', ' ', text)
                return text.lower().strip()

            title_normalized = normalize_for_match(title)
            item_title_normalized = normalize_for_match(item_title)
            
            # Try multiple matching strategies
            exact_match = title_normalized == item_title_normalized
            partial_match = title_normalized in item_title_normalized or item_title_normalized in title_normalized
            
            # Special handling for series books with different naming formats
            # Extract key words for comparison (remove common words)
            def extract_key_words(text):
                words = text.split()
                # Remove common words that might differ between services
                stop_words = {'the', 'of', 'and', 'in', 'a', 'to', 'vol', 'volume', 'aka', 'by', 'for', 'with'}
                return [w for w in words if w not in stop_words and len(w) > 1]
            
            title_words = set(extract_key_words(title_normalized))
            item_words = set(extract_key_words(item_title_normalized))
            
            # Calculate word overlap
            if title_words and item_words:
                overlap = len(title_words.intersection(item_words))
                similarity = overlap / max(len(title_words), len(item_words))
                word_match = similarity > 0.5  # More than 50% word overlap
            else:
                word_match = False
            
            if exact_match or partial_match or word_match:
                logger.debug(f"Found ABS book: {item_title}")
                
                # Get detailed information including chapters
                item_details = get_item_details(item["id"])
                if item_details:
                    return {
                        "id": item["id"],
                        "title": item_title,
                        "chapters": item_details.get("media", {}).get("chapters", []),
                        "duration": item_details.get("media", {}).get("duration", 0)
                    }
    
    # Debug: show what we searched for and what was available
    logger.debug(f"Book '{title}' not found in Audiobookshelf")
    for lib in libraries_data.get("libraries", []):
        if lib.get("mediaType") != "book":
            continue
        lib_id = lib["id"]
        lib_name = lib.get("name", "Unknown")
        items_data = get_library_items(lib_id)
        if items_data:
            logger.debug(f"  ABS Library '{lib_name}':")
            for item in items_data.get("results", []):
                item_title = item.get("media", {}).get("metadata", {}).get("title", "Unknown")
                logger.debug(f"    - '{item_title}'")
    
    return None


def main():
    """Main function demonstrating chapter marking for the book 'test'."""
    book_title = "test"  # Use the book named test
    
    logger.info(f"Looking for book: {book_title}")
    
    # Find the book
    book = find_book_by_title(book_title)
    if not book:
        logger.error(f"Could not find book '{book_title}'")
        return 1
    
    logger.info(f"Found book: {book['title']}")
    logger.info(f"Total chapters: {len(book['chapters'])}")
    logger.info(f"Duration: {book['duration']} seconds")
    
    # Display chapters
    for i, chapter in enumerate(book['chapters'], 1):
        start = chapter.get("start", 0)
        end = chapter.get("end", 0)
        title = chapter.get("title", f"Chapter {i}")
        logger.info(f"Chapter {i}: {title} ({start}s - {end}s)")
    
    # Mark first 3 chapters as read (as requested)
    chapters_to_mark = min(3, len(book['chapters']))  # Mark up to 3 chapters
    logger.info(f"\nMarking first {chapters_to_mark} chapters as read...")
    
    for chapter_num in range(1, chapters_to_mark + 1):
        logger.info(f"Marking chapter {chapter_num} as read...")
        success = update_chapter_as_read(
            book['id'], 
            chapter_num, 
            book['chapters']
        )
        
        if success:
            logger.info(f"Successfully marked chapter {chapter_num} as read!")
        else:
            logger.error(f"Failed to mark chapter {chapter_num} as read")
            return 1
    
    logger.info(f"All {chapters_to_mark} chapters marked as read successfully!")
    
    return 0


if __name__ == "__main__":
    # Check for required environment variables
    if not os.environ.get("AUDIOBOOKSHELF_URL") or not os.environ.get("AUDIOBOOKSHELF_API_KEY"):
        logger.error("Please set the following environment variables:")
        logger.error("  AUDIOBOOKSHELF_URL - Your Audiobookshelf server URL")
        logger.error("  AUDIOBOOKSHELF_API_KEY - Your Audiobookshelf API key")
        sys.exit(1)
    
    sys.exit(main())