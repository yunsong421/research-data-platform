"""Production entry point.

Windows / Linux / macOS:
    pip install -r requirements.txt
    python wsgi.py
"""

import os

from app import app


if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "5000"))
    try:
        from waitress import serve
        print("Serving on http://%s:%d  (waitress)" % (host, port))
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        # fall back to the development server if waitress is unavailable
        print("waitress not installed, falling back to the built-in server")
        app.run(host=host, port=port, debug=False, threaded=True)
