import os


class Config:
    """Base configuration, populated from environment variables.

    Locally these come from a `.env` file (see `.env.example`); on Render
    they are set as environment variables in the service dashboard.
    """

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    SUPABASE_PRODUCT_BUCKET = os.environ.get("SUPABASE_PRODUCT_BUCKET", "product-images")
    SUPABASE_BANNER_BUCKET = os.environ.get("SUPABASE_BANNER_BUCKET", "banner-images")
    SUPABASE_PAYMENT_PROOF_BUCKET = os.environ.get("SUPABASE_PAYMENT_PROOF_BUCKET", "payment-proofs")

    # --- Flutterwave (secret key is server-side only; never put it in templates/JS) ---
    FLUTTERWAVE_SECRET_KEY = os.environ.get("FLUTTERWAVE_SECRET_KEY", "")
    # Secret hash you set in Flutterwave Dashboard -> Settings -> Webhooks (authenticates webhooks).
    FLUTTERWAVE_SECRET_HASH = os.environ.get("FLUTTERWAVE_SECRET_HASH", "")
    # Store prices are in USD. Set FLUTTERWAVE_CURRENCY to the currency you want to charge in;
    # if it isn't USD you must also set FLUTTERWAVE_USD_EXCHANGE_RATE (units of that
    # currency per 1 USD).
    FLUTTERWAVE_CURRENCY = os.environ.get("FLUTTERWAVE_CURRENCY", "USD")
    FLUTTERWAVE_USD_EXCHANGE_RATE = os.environ.get("FLUTTERWAVE_USD_EXCHANGE_RATE", "")
    # Public https URL of this site, e.g. https://shop.example.com (no trailing slash).
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "")

    SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "14092295172")

    ADMIN_BOOTSTRAP_EMAIL = os.environ.get("ADMIN_BOOTSTRAP_EMAIL", "")
    ADMIN_BOOTSTRAP_PASSWORD = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Render terminates TLS at the edge; behind that proxy this is safe to force.
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"

    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB upload ceiling for images
