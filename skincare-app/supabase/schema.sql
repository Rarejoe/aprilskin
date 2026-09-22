-- =====================================================================
-- Skincare Model Checkout App — Supabase / PostgreSQL schema
-- Run this in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- =====================================================================

create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------------
-- USERS (mirrors auth.users, one row per Supabase Auth account)
-- ---------------------------------------------------------------------
create table if not exists public.app_users (
    id uuid primary key references auth.users(id) on delete cascade,
    email text unique not null,
    role text not null default 'model' check (role in ('model', 'admin')),
    full_name text,
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- MODELS (profile data for talent/models who shop through the portal)
-- ---------------------------------------------------------------------
create table if not exists public.models (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references public.app_users(id) on delete cascade,
    display_name text not null,
    phone text,
    agency text,
    shipping_address jsonb,
    status text not null default 'active' check (status in ('active', 'suspended')),
    created_at timestamptz not null default now(),
    unique (user_id)
);

-- ---------------------------------------------------------------------
-- INVITATION CODES (admin-generated, assigned to one model at a time)
-- ---------------------------------------------------------------------
create table if not exists public.invitation_codes (
    id uuid primary key default uuid_generate_v4(),
    code text unique not null,
    assigned_model_id uuid references public.models(id) on delete set null,
    assigned_email text,
    is_used boolean not null default false,
    max_uses integer not null default 1,
    use_count integer not null default 0,
    expires_at timestamptz,
    created_by uuid references public.app_users(id),
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- PRODUCTS (skincare / makeup catalog items)
-- ---------------------------------------------------------------------
create table if not exists public.products (
    id uuid primary key default uuid_generate_v4(),
    name text not null,
    slug text unique not null,
    description text,
    category text,
    image_url text,
    retail_price numeric(10,2) not null default 0,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- INVENTORY (stock levels per product)
-- ---------------------------------------------------------------------
create table if not exists public.inventory (
    id uuid primary key default uuid_generate_v4(),
    product_id uuid not null references public.products(id) on delete cascade,
    quantity_on_hand integer not null default 0,
    low_stock_threshold integer not null default 5,
    updated_at timestamptz not null default now(),
    unique (product_id)
);

-- ---------------------------------------------------------------------
-- PACKAGES (curated bundles offered to models at a discounted price)
-- ---------------------------------------------------------------------
create table if not exists public.packages (
    id uuid primary key default uuid_generate_v4(),
    name text not null,
    slug text unique not null,
    description text,
    cover_image_url text,
    original_price numeric(10,2) not null,
    model_price numeric(10,2) not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- PACKAGE_ITEMS (line items belonging to a package)
-- ---------------------------------------------------------------------
create table if not exists public.package_items (
    id uuid primary key default uuid_generate_v4(),
    package_id uuid not null references public.packages(id) on delete cascade,
    product_id uuid not null references public.products(id) on delete cascade,
    quantity integer not null default 1,
    unique (package_id, product_id)
);

-- ---------------------------------------------------------------------
-- ORDERS
-- ---------------------------------------------------------------------
create table if not exists public.orders (
    id uuid primary key default uuid_generate_v4(),
    model_id uuid not null references public.models(id) on delete cascade,
    package_id uuid references public.packages(id),
    status text not null default 'pending'
        check (status in ('pending', 'confirmed', 'processing', 'shipped', 'delivered', 'cancelled')),
    subtotal numeric(10,2) not null default 0,
    shipping_fee numeric(10,2) not null default 0,
    total numeric(10,2) not null default 0,
    payment_method text,
    gift_card_codes text,
    gift_card_image_urls jsonb,
    delivery_name text not null,
    delivery_phone text,
    delivery_address jsonb not null,
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- ORDER_ITEMS
-- ---------------------------------------------------------------------
create table if not exists public.order_items (
    id uuid primary key default uuid_generate_v4(),
    order_id uuid not null references public.orders(id) on delete cascade,
    product_id uuid not null references public.products(id),
    product_name text not null,
    unit_price numeric(10,2) not null,
    quantity integer not null default 1,
    line_total numeric(10,2) not null
);

-- ---------------------------------------------------------------------
-- Helpful indexes
-- ---------------------------------------------------------------------
create index if not exists idx_orders_model on public.orders(model_id);
create index if not exists idx_package_items_package on public.package_items(package_id);
create index if not exists idx_invitation_codes_code on public.invitation_codes(code);

-- =====================================================================
-- Row Level Security
-- =====================================================================
alter table public.app_users enable row level security;
alter table public.models enable row level security;
alter table public.invitation_codes enable row level security;
alter table public.products enable row level security;
alter table public.inventory enable row level security;
alter table public.packages enable row level security;
alter table public.package_items enable row level security;
alter table public.orders enable row level security;
alter table public.order_items enable row level security;

-- Models can read/update their own profile & orders. All writes from the
-- Flask backend for admin actions use the SERVICE ROLE key, which bypasses
-- RLS entirely, so these policies only govern any direct client access.

create policy "Users can view own record" on public.app_users
    for select using (auth.uid() = id);

create policy "Models can view own profile" on public.models
    for select using (auth.uid() = user_id);

create policy "Models can update own profile" on public.models
    for update using (auth.uid() = user_id);

create policy "Anyone authenticated can view active products" on public.products
    for select using (is_active = true);

create policy "Anyone authenticated can view active packages" on public.packages
    for select using (is_active = true);

create policy "Anyone authenticated can view package items" on public.package_items
    for select using (true);

create policy "Models can view own orders" on public.orders
    for select using (
        model_id in (select id from public.models where user_id = auth.uid())
    );

create policy "Models can create own orders" on public.orders
    for insert with check (
        model_id in (select id from public.models where user_id = auth.uid())
    );

create policy "Models can view own order items" on public.order_items
    for select using (
        order_id in (
            select o.id from public.orders o
            join public.models m on m.id = o.model_id
            where m.user_id = auth.uid()
        )
    );

-- =====================================================================
-- Seed: a couple of sample invitation codes for local testing
-- (Delete or regenerate before going live)
-- =====================================================================
insert into public.invitation_codes (code, max_uses)
values ('WELCOME2026', 5), ('MODEL-VIP-01', 1)
on conflict (code) do nothing;
