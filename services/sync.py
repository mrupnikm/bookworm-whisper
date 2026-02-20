import os
import logging
import threading
import time

logger = logging.getLogger(__name__)

_sync_thread = None
_stop_event = threading.Event()


def _sync_loop():
    """Background loop that periodically syncs data from external services."""
    from metadata import sync_all_external_data, check_sync_differences, sync_all_bidirectional_progress

    sync_interval = int(os.environ.get("GLOBAL_SYNC_TIME", "300"))
    logger.info(f"Background sync started with interval: {sync_interval}s")

    # Initial sync on startup
    try:
        logger.debug("Running initial sync...")
        results = sync_all_external_data()
        logger.debug(f"Fetched: Komga={len(results['komga'])}, ABS={len(results['audiobookshelf'])} books")

        # Check sync differences and perform bi-directional sync
        check_sync_differences()
        sync_results = sync_all_bidirectional_progress()
        if sync_results:
            synced_count = sum(1 for r in sync_results if r.get("action") not in ["none", "failed"])
            if synced_count > 0:
                logger.info(f"Sync complete: {synced_count} books updated")
        
    except Exception as e:
        logger.error(f"Initial sync failed: {e}")

    while not _stop_event.is_set():
        # Wait for sync interval or until stop is requested
        if _stop_event.wait(timeout=sync_interval):
            break  # Stop event was set

        try:
            logger.debug("Running periodic sync...")
            results = sync_all_external_data()
            logger.debug(f"Fetched: Komga={len(results['komga'])}, ABS={len(results['audiobookshelf'])} books")

            # Check sync differences and perform bi-directional sync
            check_sync_differences()
            sync_results = sync_all_bidirectional_progress()
            if sync_results:
                synced_count = sum(1 for r in sync_results if r.get("action") not in ["none", "failed"])
                if synced_count > 0:
                    logger.info(f"Sync complete: {synced_count} books updated")
                
        except Exception as e:
            logger.error(f"Periodic sync failed: {e}")

    logger.info("Background sync stopped")


def start_background_sync():
    """Start the background sync thread."""
    global _sync_thread

    sync_interval = int(os.environ.get("GLOBAL_SYNC_TIME", "0"))
    if sync_interval <= 0:
        logger.info("Background sync disabled (GLOBAL_SYNC_TIME <= 0)")
        return

    if _sync_thread is not None and _sync_thread.is_alive():
        logger.warning("Background sync already running")
        return

    _stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_loop, daemon=True)
    _sync_thread.start()


def stop_background_sync():
    """Stop the background sync thread."""
    global _sync_thread

    if _sync_thread is None or not _sync_thread.is_alive():
        return

    logger.info("Stopping background sync...")
    _stop_event.set()
    _sync_thread.join(timeout=5)
    _sync_thread = None


def manual_sync_all():
    """Manually trigger a full sync including bi-directional progress sync."""
    from metadata import sync_all_external_data, check_sync_differences, sync_all_bidirectional_progress
    
    logger.debug("Running manual sync...")
    
    try:
        # Sync from both services
        results = sync_all_external_data()
        logger.info(
            f"Sync complete: Komga={len(results['komga'])} books, "
            f"Audiobookshelf={len(results['audiobookshelf'])} books"
        )
        
        # Check differences
        differences = check_sync_differences()
        
        # Perform bi-directional sync
        sync_results = sync_all_bidirectional_progress()
        if sync_results:
            synced_count = sum(1 for r in sync_results if r.get("action") not in ["none", "failed"])
            logger.info(f"Bi-directional sync complete: {synced_count} books updated")
            for result in sync_results:
                if result.get("action") not in ["none", "failed"]:
                    book_name = result.get("book_name", "Unknown")
                    action = result.get("action", "unknown")
                    target = result.get("target_chapter", 0)
                    logger.info(f"  - {book_name}: {action} to chapter {target}")
        
        return {
            "success": True,
            "sync_results": results,
            "differences": differences,
            "bi_directional_results": sync_results
        }
        
    except Exception as e:
        logger.error(f"Manual sync failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def get_sync_status():
    """Get current sync status."""
    sync_interval = int(os.environ.get("GLOBAL_SYNC_TIME", "0"))
    return {
        "enabled": sync_interval > 0,
        "interval_seconds": sync_interval,
        "running": _sync_thread is not None and _sync_thread.is_alive(),
    }
