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

    SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "+1 (409) 229-5172")

    ADMIN_BOOTSTRAP_EMAIL = os.environ.get("ADMIN_BOOTSTRAP_EMAIL", "")
    ADMIN_BOOTSTRAP_PASSWORD = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Render terminates TLS at the edge; behind that proxy this is safe to force.
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"

    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB upload ceiling for images
