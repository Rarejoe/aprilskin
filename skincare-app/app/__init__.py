import os

from flask import Flask
from dotenv import load_dotenv

from app.config import Config
from app.utils import to_currency

load_dotenv()


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Jinja helpers
    app.jinja_env.filters["currency"] = to_currency

    # Blueprints
    from app.auth.routes import auth_bp
    from app.main.routes import main_bp
    from app.admin.routes import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    @app.context_processor
    def inject_globals():
        return {
            "brand_name": "APRILSKIN",
            "support_phone": app.config.get("SUPPORT_PHONE", ""),
        }

    @app.errorhandler(404)
    def not_found(_e):
        from flask import render_template
        return render_template("404.html"), 404

    return app
