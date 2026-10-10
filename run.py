"""Local development entrypoint for CivicSync.

Usage:
    python run.py

Reads CIVICSYNC_SECRET, CIVICSYNC_DATA_SECRET and CIVICSYNC_DB from the
environment (see README.md). Starts the Flask dev server on http://127.0.0.1:5001
with debug and auto-reload disabled. This is a development server only — do not expose it
publicly without a production WSGI server and real secrets.
"""
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("CIVICSYNC_PORT", "5001")), debug=False, use_reloader=False)
