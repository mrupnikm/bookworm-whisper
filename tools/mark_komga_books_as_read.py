#!/usr/bin/env python3
"""
Mark Komga books as read using API.

This script demonstrates how to mark books as completed in Komga by updating 
their reading progress to the final page.
"""

import os
import sys

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import config to set environment variables first
import config
import json
import urllib.request
import urllib.error
import time
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_komga_config():
    """Get Komga configuration from environment."""
    url = os.environ.get("KOMGA_URL", "")
    api_key = os.environ.get("KOMGA_API_KEY", "")
    return url, api_key


def make_api_request(url, method="GET", data=None):
    """Make HTTP request to Komga API."""
    api_key = get_komga_config()[1]
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    req = None
    try:
        if method == "GET":
            req = urllib.request.Request(url, headers=headers)
        elif method in ("POST", "PATCH", "PUT"):
            if data:
                json_data = json.dumps(data).encode('utf-8')
                req = urllib.request.Request(url, data=json_data, headers=headers, method=method)
            else:
                req = urllib.request.Request(url, headers=headers, method=method)
        else:
            req = urllib.request.Request(url, headers=headers, method=method)
        
        with urllib.request.urlopen(req, timeout=30) as response:
            response_text = response.read().decode('utf-8')
            try:
                return json.loads(response_text), response.getcode()
            except json.JSONDecodeError:
                return {"response": response_text}, response.getcode()
                
    except urllib.error.HTTPError as e:
        error_data = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
        return {"error": error_data}, e.code
    except Exception as e:
        return {"error": str(e)}, 0


def get_komga_libraries():
    """Get all libraries from Komga."""
    base_url, api_key = get_komga_config()
    if not base_url:
        logger.error("Komga URL not configured")
        return None
    
    logger.info(f"Attempting to connect to Komga at: {base_url}")
    
    # Try the libraries endpoint directly
    data, status = make_api_request(f"{base_url}/api/v1/libraries")
    if status == 200:
        logger.info("Successfully connected to Komga API")
        return data
    else:
        logger.error(f"Failed to connect to Komga API. Status: {status}, Response: {data}")
        return None


def get_komga_books():
    """Get all books from Komga (not per library)."""
    base_url, _ = get_komga_config()
    if not base_url:
        return None
    
    data, status = make_api_request(f"{base_url}/api/v1/books?unpaged=true&size=10000")
    return data if status == 200 else None


def get_book_progress(book_id):
    """Get current reading progress for a specific book."""
    base_url, _ = get_komga_config()
    if not base_url:
        return None
    
    data, status = make_api_request(f"{base_url}/api/v1/books/{book_id}/read-progress")
    return data if status == 200 else None


def get_book_positions(book_id):
    """Get book positions for EPUB chapter calculation."""
    base_url, _ = get_komga_config()
    if not base_url:
        return None
    
    data, status = make_api_request(f"{base_url}/api/v1/books/{book_id}/positions")
    return data if status == 200 else None


def mark_epub_book_up_to_chapter(book_id, target_chapter=3):
    """
    Mark an EPUB book as read up to a specific chapter using progression API.
    
    Args:
        book_id: The Komga book ID
        target_chapter: Target chapter to read up to (default: 3)
    
    Returns:
        bool: True if successful, False otherwise
    """
    base_url, _ = get_komga_config()
    if not base_url:
        logger.error("Komga configuration missing")
        return False
    
    try:
        # Get book positions to find chapter locations
        positions_data = get_book_positions(book_id)
        if not positions_data or not positions_data.get("positions"):
            logger.error(f"Could not get positions for EPUB book {book_id}")
            return False
        
        positions = positions_data["positions"]
        logger.info(f"Found {len(positions)} positions in EPUB book")
        
        # Find position for target chapter (positions are ordered by chapter)
        if len(positions) < target_chapter:
            logger.warning(f"Book only has {len(positions)} positions, marking as complete")
            target_position = positions[-1]  # Last position
        else:
            target_position = positions[target_chapter - 1]  # Chapter 3 is index 2
        
        # Prepare progression data (using Readium progression format)
        import datetime
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
                "id": "bookworm-whisper-script",
                "name": "BookWorm Whisper Script"
            },
            "modified": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        
        logger.info(f"Marking EPUB book {book_id} up to chapter {target_chapter} (progression: {progression_data['locator']['locations']['totalProgression']})")
        
        data, status = make_api_request(
            f"{base_url}/api/v1/books/{book_id}/progression",
            method="PUT",  # Use PUT for progression
            data=progression_data
        )
        
        if status in [200, 204]:
            logger.info(f"Successfully marked EPUB book {book_id} up to chapter {target_chapter}")
            return True
        else:
            logger.error(f"Progression API failed. Status: {status}, Response: {data}")
            return False
        
    except Exception as e:
        logger.error(f"Failed to update EPUB progression: {e}")
        return False


def mark_book_up_to_chapter(book_id, total_pages, target_chapter=3):
    """
    Mark a Komga book as read up to a specific chapter.
    Tries EPUB progression first, falls back to page-based progress.
    
    Args:
        book_id: The Komga book ID
        total_pages: Total number of pages in the book
        target_chapter: Target chapter to read up to (default: 3)
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # First try EPUB progression API (works for EPUB books)
        logger.info(f"Trying EPUB progression for book {book_id}")
        if mark_epub_book_up_to_chapter(book_id, target_chapter):
            return True
        
        # If EPUB progression fails, try page-based progress (for other formats)
        logger.info(f"EPUB progression failed, trying page-based progress")
        target_page = min(total_pages, (total_pages // target_chapter) * target_chapter)
        
        base_url, _ = get_komga_config()
        progress_data = {
            "page": target_page,
            "completed": False
        }
        
        data, status = make_api_request(
            f"{base_url}/api/v1/books/{book_id}/read-progress",
            method="PATCH",
            data=progress_data
        )
        
        if status in [200, 204]:
            logger.info(f"Successfully marked book {book_id} up to chapter {target_chapter} using page-based progress")
            return True
        else:
            logger.error(f"Both EPUB and page-based progress failed")
            return False
        
    except Exception as e:
        logger.error(f"Failed to update Komga progress: {e}")
        return False


def find_book_by_title(books_data, search_title):
    """Find a book in Komga data by title matching."""
    if not books_data or not books_data.get("content"):
        return None
    
    search_lower = search_title.lower()
    
    for book in books_data["content"]:
        book_title = book.get("name", "").lower()
        book_metadata = book.get("metadata", {}).get("title", "").lower()
        
        # Check both name and metadata title
        if search_lower in book_title or search_lower in book_metadata:
            return book
    
    return None
    
    # If search_title is empty, return the first book
    if not search_title:
        return books_data["content"][0] if books_data["content"] else None
    
    search_lower = search_title.lower()
    
    for book in books_data["content"]:
        book_title = book.get("name", "").lower()
        book_metadata = book.get("metadata", {}).get("title", "").lower()
        
        # Check both name and metadata title
        if search_lower in book_title or search_lower in book_metadata:
            return book
    
    return None


def main():
    """Main function demonstrating book marking for books containing 'test'."""
    search_title = "test"  # Search for books with 'test' in title
    
    logger.info(f"Looking for books containing: {search_title}")
    
    # Get libraries
    libraries = get_komga_libraries()
    if not libraries:
        logger.error("Failed to fetch Komga libraries")
        return 1
    
    logger.info(f"Available libraries: {[lib.get('name', 'Unknown') for lib in libraries]}")
    
    # Get all books directly (not per library)
    logger.info("Fetching all books from Komga")
    books_data = get_komga_books()
    if not books_data:
        logger.error("Failed to fetch books from Komga")
        return 1
        
    book_count = len(books_data.get('content', []))
    logger.info(f"Found {book_count} books total")
    
    if book_count == 0:
        logger.error("No books found in Komga")
        return 1
    
    # Search for the target book
    target_book = find_book_by_title(books_data, search_title)
    
    if not target_book:
        logger.error(f"Book containing '{search_title}' not found")
        return 1
    
    book_id = target_book["id"]
    book_title = target_book.get("metadata", {}).get("title", "Unknown")
    total_pages = target_book.get("media", {}).get("pagesCount", 0)
    
    logger.info(f"Found book: {book_title}")
    logger.info(f"Book ID: {book_id}")
    logger.info(f"Total pages: {total_pages}")
    
    # Mark the book as read up to chapter 3
    success = mark_book_up_to_chapter(book_id, total_pages, target_chapter=3)
    
    if success:
        logger.info(f"✅ Successfully marked '{book_title}' as read in Komga!")
        return 0
    else:
        logger.error(f"❌ Failed to mark '{book_title}' as read in Komga")
        return 1


if __name__ == "__main__":
    # Check for required environment variables
    if not os.environ.get("KOMGA_URL"):
        logger.error("Please set KOMGA_URL environment variable")
        logger.info("For testing, you can use AUDIOBOOKSHELF_URL if Komga not available")
        sys.exit(1)
    
    sys.exit(main())