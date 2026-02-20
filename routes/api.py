import os

from flask import Blueprint, jsonify, request

from process import get_status, stop_process, dismiss_process
from metadata import (
    load_metadata,
    save_metadata,
    get_metadata_path,
    sync_all_komga_data,
    sync_all_audiobookshelf_data,
    sync_all_external_data,
)
from services.sync import get_sync_status, manual_sync_all

api_bp = Blueprint("api", __name__)


@api_bp.route("/status")
def status():
    return jsonify(get_status())


@api_bp.route("/stop", methods=["POST"])
def stop():
    return jsonify(stop_process())


@api_bp.route("/dismiss", methods=["POST"])
def dismiss():
    return jsonify(dismiss_process())


@api_bp.route("/api/komga/progress/<path:filename>")
def komga_progress(filename):
    """Get Komga reading progress for a book from local JSON metadata."""
    try:
        # Get book name from filename (remove .epub extension)
        book_name = os.path.splitext(filename)[0]
        metadata_path = get_metadata_path(book_name)

        # Read from local JSON file
        metadata = load_metadata(metadata_path)

        if not metadata:
            return jsonify({"error": "not_found"})

        komga_data = metadata.get("reading_progress", {}).get("komga", {})

        # Return in the format the frontend expects
        return jsonify({
            "title": metadata.get("book_name", book_name),
            "pages_read": komga_data.get("current_page", 0),
            "pages_total": komga_data.get("total_pages", 0),
            "current_chapter_num": komga_data.get("current_chapter_num", 0),
            "num_chapters": komga_data.get("num_chapters", 0),
            "status": komga_data.get("status", "unknown"),
            "chapter": komga_data.get("current_chapter"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/komga/sync", methods=["POST"])
def komga_sync():
    """Trigger a sync of all Komga reading progress to local JSON files."""
    try:
        updated = sync_all_komga_data()
        return jsonify({"success": True, "updated_count": len(updated), "updated": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/audiobookshelf/progress/<path:filename>")
def audiobookshelf_progress(filename):
    """Get Audiobookshelf listening progress for a book from local JSON metadata."""
    try:
        book_name = os.path.splitext(filename)[0]
        metadata_path = get_metadata_path(book_name)

        metadata = load_metadata(metadata_path)

        if not metadata:
            return jsonify({"error": "not_found"})

        abs_data = metadata.get("reading_progress", {}).get("audiobookshelf", {})

        return jsonify({
            "title": metadata.get("book_name", book_name),
            "current_chapter": abs_data.get("current_chapter"),
            "current_chapter_num": abs_data.get("current_chapter_num", 0),
            "num_chapters": abs_data.get("num_chapters", 0),
            "current_position": abs_data.get("current_position", 0),
            "total_duration": abs_data.get("total_duration", 0),
            "progress_percent": abs_data.get("progress_percent", 0),
            "status": abs_data.get("status", "unknown"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/audiobookshelf/sync", methods=["POST"])
def audiobookshelf_sync():
    """Trigger a sync of all Audiobookshelf listening progress to local JSON files."""
    try:
        updated = sync_all_audiobookshelf_data()
        return jsonify({"success": True, "updated_count": len(updated), "updated": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/sync/all", methods=["POST"])
def sync_all():
    """Trigger a sync of all external services (Komga + Audiobookshelf)."""
    try:
        results = sync_all_external_data()
        return jsonify({
            "success": True,
            "komga": {"updated_count": len(results["komga"]), "updated": results["komga"]},
            "audiobookshelf": {"updated_count": len(results["audiobookshelf"]), "updated": results["audiobookshelf"]},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/sync/status")
def sync_status():
    """Get the current background sync status."""
    return jsonify(get_sync_status())


@api_bp.route("/api/sync/enabled/<path:filename>", methods=["POST"])
def set_sync_enabled(filename):
    """Toggle sync_enabled for a book."""
    try:
        book_name = os.path.splitext(filename)[0]
        metadata_path = get_metadata_path(book_name)
        metadata = load_metadata(metadata_path)

        if not metadata:
            return jsonify({"error": "not_found"}), 404

        data = request.get_json() or {}
        enabled = data.get("enabled", False)

        metadata["sync_enabled"] = enabled
        save_metadata(metadata_path, metadata)

        return jsonify({"success": True, "sync_enabled": enabled})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@api_bp.route("/api/book/hidden/<path:filename>", methods=["POST"])
def set_book_hidden(filename):
    """Toggle hidden status for a book."""
    try:
        book_name = os.path.splitext(filename)[0]
        metadata_path = get_metadata_path(book_name)
        metadata = load_metadata(metadata_path)

        if not metadata:
            metadata = {}

        data = request.get_json() or {}
        hidden = data.get("hidden", False)

        metadata["hidden"] = hidden
        save_metadata(metadata_path, metadata)

        return jsonify({"success": True, "hidden": hidden})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/sync/bidirectional", methods=["POST"])
def sync_bidirectional():
    """Trigger bi-directional sync to equalize progress between services."""
    try:
        result = manual_sync_all()
        
        if result.get("success"):
            return jsonify({
                "success": True,
                "sync_results": result.get("sync_results", {}),
                "bi_directional_results": result.get("bi_directional_results", []),
                "updated_count": sum(1 for r in result.get("bi_directional_results", []) 
                                 if r.get("action") not in ["none", "failed"])
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Unknown error")
            }), 500
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
