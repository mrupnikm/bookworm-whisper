import logging
import os

# Set default environment variables
# API keys should be set via environment variables or .env file
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("OPENAI_BASE_URL", "http://kokoro:8880/v1")
os.environ.setdefault("AUDIOBOOKSHELF_URL", "")
os.environ.setdefault("AUDIOBOOKSHELF_API_KEY", "")
os.environ.setdefault("KOMGA_URL", "")
os.environ.setdefault("KOMGA_API_KEY", "")
os.environ.setdefault("GLOBAL_SYNC_TIME", "300")  # Sync interval in seconds (default 5 min)
os.environ.setdefault("LOG_LEVEL", "INFO")  # INFO for high-level, DEBUG for detailed

# Configure logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Suppress werkzeug HTTP request logs (they're too verbose at INFO level)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EPUB_DIR = os.path.join(BASE_DIR, "books")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(EPUB_DIR, exist_ok=True)

# Compatible versions (hardcoded for now)
# These indicate what versions this application was built/tested against
COMPATIBLE_VERSIONS = {
    "bookworm_whisper": "0.1.0",
    "komga": "1.20.1",
    "audiobookshelf": "2.19.4",
    "kokoro_tts": "0.9.4",
    "epub_to_audiobook": "1.8.3",
}
