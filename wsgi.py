"""Production entry point.

Windows / Linux / macOS:
    pip install -r requirements.txt
    python wsgi.py
"""

import os

if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "5000"))
    from backup_store import runtime_lock, start_daily_backups
    with runtime_lock():
        from app import app
        stop_backup_worker = start_daily_backups()
        try:
            from waitress import serve
            print("Serving on http://%s:%d  (waitress)" % (host, port))
            serve(app, host=host, port=port, threads=8)
        finally:
            stop_backup_worker.set()
