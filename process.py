import logging
import threading

from services.audiobookshelf import scan_all_libraries as scan_abs_libraries

logger = logging.getLogger(__name__)

# Process tracking state
current_process = {
    "process": None,
    "status": "idle",  # idle, running, completed, error, stopped
    "filename": None,
    "book_name": None,
    "book_output_dir": None,
    "output": [],
    "error": None,
}
process_lock = threading.Lock()


def read_process_output(proc):
    """Read process output in a background thread."""
    global current_process

    try:
        for line in iter(proc.stdout.readline, ""):
            if line:
                stripped_line = line.strip()
                with process_lock:
                    current_process["output"].append(stripped_line)

        proc.stdout.close()
        proc.wait()
        with process_lock:
            if current_process["status"] == "running":
                if proc.returncode == 0:
                    current_process["status"] = "completed"
                    # Trigger Audiobookshelf library scan to discover the new audiobook
                    try:
                        scan_abs_libraries()
                    except Exception as e:
                        logger.warning(f"Failed to trigger Audiobookshelf library scan: {e}")
                else:
                    current_process["status"] = "error"
                    current_process["error"] = (
                        f"Process exited with code {proc.returncode}"
                    )
    except Exception as e:
        with process_lock:
            current_process["status"] = "error"
            current_process["error"] = str(e)


def get_status():
    """Get current process status."""
    with process_lock:
        return {
            "status": current_process["status"],
            "filename": current_process["filename"],
            "output": current_process["output"][-20:],
            "error": current_process["error"],
        }


def stop_process():
    """Stop the running process."""
    global current_process
    with process_lock:
        if current_process["process"] and current_process["status"] == "running":
            current_process["process"].terminate()
            current_process["status"] = "stopped"
            current_process["error"] = "Process was stopped by user"
            return {"success": True, "message": "Process stopped"}
        return {"success": False, "message": "No process running"}


def dismiss_process():
    """Reset process state after completion."""
    global current_process
    with process_lock:
        if current_process["status"] != "running":
            current_process["status"] = "idle"
            current_process["filename"] = None
            current_process["output"] = []
            current_process["error"] = None
            return {"success": True}
        return {"success": False, "message": "Process still running"}
