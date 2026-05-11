import os

import requests
from flask import Blueprint, jsonify

from config import EPUB_DIR, OUTPUT_DIR, COMPATIBLE_VERSIONS

health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
def health():
    """Main health check endpoint for the BookWorm Whisper application."""
    checks = {
        "status": "healthy",
        "version": COMPATIBLE_VERSIONS.get("bookworm_whisper", "unknown"),
        "checks": {}
    }

    # Check books directory is accessible
    try:
        books_accessible = os.path.isdir(EPUB_DIR) or os.makedirs(EPUB_DIR, exist_ok=True) is None
        checks["checks"]["books_dir"] = "ok" if os.path.isdir(EPUB_DIR) else "created"
    except Exception as e:
        checks["checks"]["books_dir"] = f"error: {e}"
        checks["status"] = "degraded"

    # Check output directory is accessible
    try:
        output_accessible = os.path.isdir(OUTPUT_DIR) or os.makedirs(OUTPUT_DIR, exist_ok=True) is None
        checks["checks"]["output_dir"] = "ok" if os.path.isdir(OUTPUT_DIR) else "created"
    except Exception as e:
        checks["checks"]["output_dir"] = f"error: {e}"
        checks["status"] = "degraded"

    return jsonify(checks), 200 if checks["status"] == "healthy" else 503


@health_bp.route("/health/ready")
def health_ready():
    """Readiness check - is the app ready to serve requests?"""
    return jsonify({"status": "ready"}), 200


@health_bp.route("/health/live")
def health_live():
    """Liveness check - is the app running?"""
    return jsonify({"status": "live"}), 200


@health_bp.route("/health/kokoro")
def health_kokoro_tts():
    """Check TTS service health from server-side to avoid CORS"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        base_url = os.environ.get("OPENAI_BASE_URL", "http://localhost:8880/v1")
        # Strip /v1 suffix to get base host for health check
        health_url = base_url.replace("/v1", "") + "/health"
        logger.debug(f"Checking TTS health at: {health_url}")
        response = requests.get(health_url, timeout=10)
        if response.ok:
            logger.debug("TTS service live")
            return jsonify({"status": "live"})
        else:
            logger.warning(f"TTS service error: {response.status_code}")
            return jsonify({"status": "error"})
    except requests.exceptions.Timeout:
        logger.warning("TTS service timeout")
        return jsonify({"status": "timeout"})
    except Exception as e:
        logger.warning(f"TTS service offline: {e}")
        return jsonify({"status": "offline"})


@health_bp.route("/health/audiobookshelf")
def health_audiobookshelf():
    """Check Audiobookshelf service health"""
    import logging
    logger = logging.getLogger(__name__)
    
    base_url = os.environ.get("AUDIOBOOKSHELF_URL", "")
    if not base_url:
        logger.debug("Audiobookshelf not configured")
        return jsonify({"status": "not_configured"})
    try:
        logger.debug(f"Checking Audiobookshelf health at: {base_url}/healthcheck")
        response = requests.get(f"{base_url}/healthcheck", timeout=10)
        if response.ok and response.text.strip() == "OK":
            logger.debug("Audiobookshelf service live")
            return jsonify({"status": "live"})
        else:
            logger.warning(f"Audiobookshelf service error: {response.status_code}")
            return jsonify({"status": "error"})
    except requests.exceptions.Timeout:
        logger.warning("Audiobookshelf service timeout")
        return jsonify({"status": "timeout"})
    except Exception as e:
        logger.warning(f"Audiobookshelf service offline: {e}")
        return jsonify({"status": "offline"})


@health_bp.route("/health/komga")
def health_ebook_library():
    """Check ebook library service health (Komga or Grimmory based on EBOOK_LIBRARY env var)."""
    import logging
    logger = logging.getLogger(__name__)

    backend = os.environ.get("EBOOK_LIBRARY", "komga").lower()

    if backend == "grimmory":
        base_url = os.environ.get("GRIMMORY_URL", "")
        if not base_url:
            return jsonify({"status": "not_configured", "backend": "grimmory"})
        try:
            from services.library import is_library_available
            available = is_library_available()
            return jsonify({"status": "live" if available else "error", "backend": "grimmory"})
        except requests.exceptions.Timeout:
            return jsonify({"status": "timeout", "backend": "grimmory"})
        except Exception as e:
            logger.warning(f"Grimmory health check failed: {e}")
            return jsonify({"status": "offline", "backend": "grimmory"})
    else:
        base_url = os.environ.get("KOMGA_URL", "")
        if not base_url:
            logger.debug("Komga not configured")
            return jsonify({"status": "not_configured", "backend": "komga"})
        try:
            logger.debug(f"Checking Komga health at: {base_url}/actuator/health")
            response = requests.get(f"{base_url}/actuator/health", timeout=10)
            data = response.json()
            if data.get("status") == "UP":
                return jsonify({"status": "live", "backend": "komga"})
            else:
                return jsonify({"status": "error", "backend": "komga"})
        except requests.exceptions.Timeout:
            return jsonify({"status": "timeout", "backend": "komga"})
        except Exception as e:
            logger.warning(f"Komga service offline: {e}")
            return jsonify({"status": "offline", "backend": "komga"})
