import os
import logging
import re

from flask import Blueprint, abort, flash, redirect, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from config import EPUB_DIR
from metadata import create_metadata_template
from services.library import scan_all_libraries, is_library_available

logger = logging.getLogger(__name__)

files_bp = Blueprint("files", __name__)


def is_safe_filename(filename: str) -> bool:
    """Validate filename is safe and doesn't contain path traversal attempts."""
    if not filename:
        return False
    # Check for path traversal patterns
    if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
        return False
    # Only allow alphanumeric, spaces, hyphens, underscores, and dots
    if not re.match(r'^[\w\s\-\.]+$', filename):
        return False
    return True


@files_bp.route("/upload", methods=["POST"])
def upload():
    if "epub_file" not in request.files:
        flash("No file selected.")
        return redirect(url_for("main.index"))

    file = request.files["epub_file"]
    if file.filename is None or file.filename == "":
        flash("No file selected.")
        return redirect(url_for("main.index"))

    # Sanitize filename first
    filename = secure_filename(file.filename)

    # Validate extension AFTER sanitization
    if not filename or not filename.lower().endswith(".epub"):
        flash("Only .epub files are allowed.")
        return redirect(url_for("main.index"))

    os.makedirs(EPUB_DIR, exist_ok=True)

    # Check if file already exists
    target_path = os.path.join(EPUB_DIR, filename)
    if os.path.exists(target_path):
        flash(f"File '{filename}' already exists. Please rename and try again.")
        return redirect(url_for("main.index"))

    file.save(target_path)
    
    # Create metadata JSON file for the uploaded book
    book_name = os.path.splitext(filename)[0]
    try:
        create_metadata_template(book_name, filename)
        flash(f"Uploaded {filename} and created metadata file")
    except Exception as e:
        flash(f"Uploaded {filename}, but failed to create metadata: {e}")

    # Trigger ebook library scan to discover the new book
    if is_library_available():
        try:
            scan_all_libraries()
        except Exception as e:
            logger.warning(f"Failed to trigger library scan: {e}")
    else:
        logger.debug("Ebook library not available, skipping scan")

    return redirect(url_for("main.index"))


@files_bp.route("/epub/<path:filename>")
def download_epub(filename):
    # Sanitize filename to prevent path traversal attacks
    safe_filename = secure_filename(filename)

    if not safe_filename or not is_safe_filename(safe_filename):
        logger.warning(f"Path traversal attempt detected: {filename}")
        abort(400, description="Invalid filename")

    # Verify the file exists and is within EPUB_DIR
    file_path = os.path.join(EPUB_DIR, safe_filename)
    real_path = os.path.realpath(file_path)
    real_epub_dir = os.path.realpath(EPUB_DIR)

    if not real_path.startswith(real_epub_dir):
        logger.warning(f"Path traversal attempt detected: {filename} -> {real_path}")
        abort(400, description="Invalid filename")

    if not os.path.exists(real_path):
        abort(404, description="File not found")

    return send_from_directory(EPUB_DIR, safe_filename, as_attachment=True)
