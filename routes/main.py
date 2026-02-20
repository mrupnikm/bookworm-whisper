import logging
import os
import re
import subprocess
import sys
import threading
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from config import BASE_DIR, EPUB_DIR, OUTPUT_DIR
from metadata import get_metadata_path, load_metadata, save_metadata, sync_all_komga_data, sync_all_audiobookshelf_data, sync_all_external_data
from process import current_process, process_lock, read_process_output
from services.sync import manual_sync_all

logger = logging.getLogger(__name__)

main_bp = Blueprint("main", __name__)

# Allowed voice models (whitelist for security)
ALLOWED_VOICE_MODELS = {
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
}

# Allowed title modes
ALLOWED_TITLE_MODES = {"auto", "tag_text", "first_few"}


def validate_chapter_number(value: str, default: int) -> int:
    """Validate and convert chapter number input."""
    try:
        num = int(value)
        # -1 is allowed for chapter_end (means "to the end")
        if num < -1:
            return default
        return num
    except (ValueError, TypeError):
        return default


def validate_speed(value: str) -> float:
    """Validate speed is within acceptable range."""
    try:
        speed = float(value)
        if speed < 0.25 or speed > 4.0:
            return 1.0
        return speed
    except (ValueError, TypeError):
        return 1.0


def validate_voice_model(value: str) -> str:
    """Validate voice model against whitelist, or check format if custom."""
    if not value:
        return "af_heart"
    # Check whitelist first
    if value in ALLOWED_VOICE_MODELS:
        return value
    # Allow custom models matching safe pattern (letters, numbers, underscores only)
    if re.match(r'^[a-zA-Z][a-zA-Z0-9_]{0,49}$', value):
        return value
    logger.warning(f"Invalid voice model rejected: {value}")
    return "af_heart"


@main_bp.route("/")
def index():
    show_hidden = request.args.get("show_hidden", "").lower() == "true"

    if not os.path.isdir(EPUB_DIR):
        files = []
    else:
        files = [f for f in os.listdir(EPUB_DIR) if f.lower().endswith(".epub")]

    # Sync Komga data for all books (non-blocking)
    try:
        sync_all_komga_data()
    except Exception as e:
        logger.warning(f"Failed to sync Komga data: {e}")

    # Load metadata for each file
    book_metadata = {}
    visible_files = []

    for f in files:
        book_name = os.path.splitext(f)[0]
        metadata_path = get_metadata_path(book_name)
        metadata = load_metadata(metadata_path)
        book_metadata[f] = metadata

        # Filter hidden books unless show_hidden is true
        is_hidden = metadata.get("hidden", False)
        if show_hidden or not is_hidden:
            visible_files.append(f)

    return render_template("index.html", files=visible_files, book_metadata=book_metadata, show_hidden=show_hidden)


@main_bp.route("/convert/<filename>")
def convert_form(filename):
    """Show the conversion form for a specific file."""
    input_path = os.path.join(EPUB_DIR, filename)

    if not os.path.exists(input_path):
        flash(f"File {filename} not found.")
        return redirect(url_for("main.index"))

    return render_template("convert.html", filename=filename)


@main_bp.route("/convert", methods=["POST"])
def convert():
    global current_process

    # Get and validate all form inputs BEFORE acquiring lock
    filename = request.form.get("file_name")
    if not filename:
        flash("No file selected.")
        return redirect(url_for("main.index"))

    # Sanitize filename to prevent path traversal
    filename = secure_filename(filename)
    if not filename:
        flash("Invalid filename.")
        return redirect(url_for("main.index"))

    input_path = os.path.join(EPUB_DIR, filename)

    # Verify file is within EPUB_DIR (prevent path traversal)
    real_path = os.path.realpath(input_path)
    real_epub_dir = os.path.realpath(EPUB_DIR)
    if not real_path.startswith(real_epub_dir):
        logger.warning(f"Path traversal attempt in convert: {filename}")
        flash("Invalid filename.")
        return redirect(url_for("main.index"))

    if not os.path.exists(input_path):
        flash("File not found.")
        return redirect(url_for("main.index"))

    # Validate all inputs
    voice_model = validate_voice_model(request.form.get("voice_model", "af_heart"))
    chapter_start = validate_chapter_number(request.form.get("chapter_start", "1"), 1)
    chapter_end = validate_chapter_number(request.form.get("chapter_end", "-1"), -1)
    speed = validate_speed(request.form.get("speed", "1.0"))
    title_mode = request.form.get("title_mode", "auto")
    if title_mode not in ALLOWED_TITLE_MODES:
        title_mode = "auto"

    # Atomic check-and-set to prevent race condition
    with process_lock:
        if current_process["status"] == "running":
            flash("A conversion is already in progress.")
            return redirect(url_for("main.index"))
        # Set status immediately while holding lock
        current_process["status"] = "running"
        current_process["filename"] = filename
        current_process["output"] = []
        current_process["error"] = None

    # Create book-specific output directory
    book_name = os.path.splitext(filename)[0]
    book_output_dir = os.path.join(OUTPUT_DIR, book_name)
    os.makedirs(book_output_dir, exist_ok=True)

    # Update process state with book info (already holding status from above)
    with process_lock:
        current_process["book_name"] = book_name
        current_process["book_output_dir"] = book_output_dir

    # Initialize/update metadata
    metadata_path = get_metadata_path(book_name)
    metadata = load_metadata(metadata_path)
    metadata["book_name"] = book_name
    metadata["source_file"] = filename
    metadata["voice_model"] = voice_model
    metadata["last_conversion_started"] = datetime.now().isoformat()
    save_metadata(metadata_path, metadata)

    # Run the converter in background with output capture
    # All arguments are validated above, safe to pass to subprocess
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                os.path.join(BASE_DIR, "epub_to_audiobook", "main.py"),
                input_path,
                book_output_dir,
                "--tts",
                "openai",
                "--voice_name",
                voice_model,
                "--model_name",
                "tts-1",
                "--chapter_start",
                str(chapter_start),
                "--chapter_end",
                str(chapter_end),
                "--speed",
                str(speed),
                "--title_mode",
                title_mode,
                "--no_prompt",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        logger.error(f"Failed to start conversion process: {e}")
        with process_lock:
            current_process["status"] = "error"
            current_process["error"] = f"Failed to start: {e}"
        flash(f"Failed to start conversion: {e}")
        return redirect(url_for("main.index"))

    with process_lock:
        current_process["process"] = proc

    # Start thread to read output
    thread = threading.Thread(target=read_process_output, args=(proc,))
    thread.daemon = True
    thread.start()

    flash(f"Conversion started for {filename} with voice {voice_model}")
    return redirect(url_for("main.index"))


@main_bp.route("/sync-all")
def sync_all():
    """Manually trigger sync of all reading progress from external services."""
    try:
        from services.sync import manual_sync_all
        result = manual_sync_all()
        
        if result.get("success"):
            sync_results = result.get("sync_results", {})
            bi_results = result.get("bi_directional_results", [])
            
            # Count successful bi-directional syncs
            updated_count = sum(1 for r in bi_results 
                              if r.get("action") not in ["none", "failed"])
            
            if updated_count > 0:
                flash(f"Bi-directional sync: {updated_count} books updated, {len(bi_results) - updated_count} already in sync")
            else:
                flash(f"Bi-directional sync: All {len(bi_results)} books already in sync")
                
            # Also show data sync results
            flash(f"Data sync: Komga ({len(sync_results.get('komga', []))} books), Audiobookshelf ({len(sync_results.get('audiobookshelf', []))} books)")
        else:
            flash(f"Error during bi-directional sync: {result.get('error', 'Unknown error')}")
    except Exception as e:
        flash(f"Error during bi-directional sync: {e}")
    return redirect(url_for("main.index"))
