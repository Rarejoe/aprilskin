"""
Supabase client wiring.

Two clients are kept:

- `anon`   -> used only for Supabase Auth calls (sign in / sign up) so that
              row-level security is evaluated as the calling user.
- `service`-> used for every other database/storage read & write from this
              backend (invitation code checks, catalog, orders, admin CRUD).
              It uses the service-role key and therefore bypasses RLS, which
              is expected since Flask is a trusted server-side context here.

Both are created lazily the first time they're needed so importing this
module never fails just because env vars aren't set yet (e.g. during
`flask db` style tooling or tests).
"""
from flask import current_app, g
from supabase import create_client, Client


def get_anon_client() -> Client:
    if "supabase_anon" not in g:
        g.supabase_anon = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_ANON_KEY"],
        )
    return g.supabase_anon


def get_service_client() -> Client:
    if "supabase_service" not in g:
        g.supabase_service = create_client(
            current_app.config["SUPABASE_URL"],
            current_app.config["SUPABASE_SERVICE_ROLE_KEY"],
        )
    return g.supabase_service
