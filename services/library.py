"""Ebook library backend facade.

Selects between Komga and Grimmory based on the EBOOK_LIBRARY environment
variable (default: "komga"). All read-side functions are re-exported here;
write-side operations (set_progress_to_chapter, mark_as_completed) are
available for the Grimmory backend only — Komga's write logic lives in
metadata.py to avoid circular imports.
"""
import os

_BACKEND = os.environ.get("EBOOK_LIBRARY", "komga").lower()

if _BACKEND == "grimmory":
    from services.grimmory import (
        find_book_progress,
        get_all_books,
        invalidate_cache,
        get_chapters_for_book,
        scan_all_libraries,
        is_grimmory_available as is_library_available,
        set_progress_to_chapter,
        mark_as_completed,
    )
else:
    from services.komga import (
        find_book_progress,
        get_all_books,
        invalidate_cache,
        get_chapters_for_book,
        scan_all_libraries,
        is_komga_available as is_library_available,
    )

    # Komga write functions (set_progress_to_chapter, mark_as_completed) are
    # dispatched directly inside metadata.py to avoid a circular import.


def get_backend():
    """Return the active backend name: 'komga' or 'grimmory'."""
    return _BACKEND
