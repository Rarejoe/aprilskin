# MAISON SKIN — Model Package Checkout App

A luxury skincare & makeup e-commerce portal built for models/talent to sign
in with an invitation code, browse curated packages at a discounted rate,
and check out. Includes a full admin dashboard for managing invitation
codes, products, packages, inventory, and orders.

**Stack:** Flask (backend + Jinja templates) · Supabase (Postgres + Auth +
Storage) · Bootstrap-free custom CSS design system · Render (hosting).

---

## 1. Project layout

```
skincare-app/
├── app/
│   ├── __init__.py          # app factory
│   ├── config.py            # env-driven config
│   ├── extensions.py        # Supabase client wrappers
│   ├── utils.py             # decorators & helpers
│   ├── auth/routes.py       # welcome page, login, register, logout
│   ├── main/routes.py       # packages, checkout, order placement
│   ├── admin/routes.py      # admin auth + full CRUD
│   ├── templates/           # Jinja templates (welcome, packages, checkout...)
│   └── static/css/js        # design system + small JS
├── supabase/schema.sql      # full Postgres schema + RLS policies
├── requirements.txt
├── Procfile                 # Render start command
├── render.yaml              # Render service blueprint (optional)
├── runtime.txt              # Python version pin
├── wsgi.py                  # local dev entry point
└── .env.example
```

## 2. Set up Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** and run the contents of `supabase/schema.sql`. This
   creates every table (`app_users`, `models`, `invitation_codes`,
   `products`, `inventory`, `packages`, `package_items`, `orders`,
   `order_items`), indexes, and row-level security policies, and seeds two
   sample invitation codes for local testing.
3. Under **Storage**, create two public buckets:
   - `product-images`
   - `banner-images`
   (names must match `SUPABASE_PRODUCT_BUCKET` / `SUPABASE_BANNER_BUCKET`).
4. Under **Authentication → Providers**, keep Email/Password enabled. Disable
   "Confirm email" while testing locally so sign-up works instantly (or
   confirm the account manually in the Auth table).
5. Create your first admin:
   - Sign up a model account through the app's "New Model" tab (or via the
     Supabase Auth dashboard), then in the SQL editor run:
     ```sql
     update public.app_users set role = 'admin' where email = 'you@yourbrand.com';
     ```
     If the row doesn't exist yet because you signed up through Supabase
     directly rather than the app, insert it first:
     ```sql
     insert into public.app_users (id, email, role)
     values ('<auth-user-uuid>', 'you@yourbrand.com', 'admin');
     ```
6. Copy your **Project URL**, **anon public key**, and **service_role key**
   from Project Settings → API into `.env`.

## 3. Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in your Supabase keys
python wsgi.py
```

Visit `http://localhost:5000` for the model portal and
`http://localhost:5000/admin/login` for the admin dashboard.

Seeded invitation codes from `schema.sql`: `WELCOME2026` (5 uses) and
`MODEL-VIP-01` (1 use).

## 4. Deploy: GitHub → Render

1. Push this project to a new GitHub repository.
2. In Render, choose **New → Web Service**, connect the repo, and either:
   - let Render read `render.yaml` automatically ("Blueprint" deploy), or
   - set manually: Build command `pip install -r requirements.txt`,
     Start command `gunicorn "app:create_app()" --bind 0.0.0.0:$PORT`.
3. Add the environment variables from `.env.example`
   (`SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
   `SUPABASE_SERVICE_ROLE_KEY`) in the Render dashboard — never commit them.
4. Deploy. Render builds on every push to your default branch.

## 5. How the invitation-code flow works

- The welcome page (`/`) shows **Continue as Model** with Sign In / New
  Model tabs, each requiring Gmail address, password, and invitation code.
- On submit, the backend first validates the invitation code (exists, not
  expired, not used up, and — if pre-assigned — matches the submitted
  email) *before* touching Supabase Auth.
- Supabase Auth then verifies (or creates) the credential pair.
- On success the code's `use_count` increments and it's linked to that
  model; the model session starts and redirects to `/packages`.
- Invalid codes, wrong passwords, or expired/used-up codes all produce an
  inline flash message on the same page — no separate error screen.

## 6. Extending

- Swap the Unsplash placeholder imagery in `templates/*.html` and
  `static/css/style.css` for your brand's real photography.
- Shipping is currently a flat `$6.00` fee (`FLAT_SHIPPING_FEE` in
  `app/main/routes.py`) — replace with your own logic if needed.
- Payment methods are captured as a string (`card` / `paypal` /
  `bank_transfer`) for the admin's reference; wire up a real payment
  processor (Stripe, etc.) in `place_order()` before going live.
