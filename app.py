import os

from flask import Flask

# Import config first to set environment defaults
import config
from config import COMPATIBLE_VERSIONS
from routes import register_blueprints
from metadata import ensure_metadata_for_all_books, update_all_sync_available
from services.sync import start_background_sync

app = Flask(__name__)
app.secret_key = "secret"

# Register all route blueprints
register_blueprints(app)


@app.context_processor
def inject_versions():
    """Inject version information into all templates."""
    return {"versions": COMPATIBLE_VERSIONS}

# Ensure all EPUB files have metadata JSON files on startup
print("Checking for missing metadata files...")
created_files = ensure_metadata_for_all_books()
if created_files:
    print(f"Created metadata files for: {', '.join(created_files)}")
else:
    print("All metadata files are present.")

# Update sync_available flags based on existing data
sync_available = update_all_sync_available()
if sync_available:
    print(f"Sync available for: {', '.join(sync_available)}")

# Start background sync service
start_background_sync()

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5000, debug=debug)
