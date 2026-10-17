"""Local development entry point. Production uses the Procfile's gunicorn command
pointed at `app:create_app()` directly, so this file is only needed for `python wsgi.py`."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=app.config["DEBUG"])
