-- =====================================================================
-- Paystack payments — run ONCE in the Supabase SQL editor on an existing
-- database. Safe to re-run (idempotent). Nothing is dropped or deleted;
-- existing orders (including gift-card orders) are untouched.
-- =====================================================================

alter table public.orders
    add column if not exists payment_status text not null default 'unpaid',
    add column if not exists payment_reference text,
    add column if not exists payment_currency text,
    add column if not exists payment_amount bigint,          -- expected charge in subunits (kobo/cents)
    add column if not exists paid_at timestamptz,
    add column if not exists paystack_transaction_id bigint,
    add column if not exists payment_channel text,
    add column if not exists payment_gateway_response text;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'orders_payment_status_check'
    ) then
        alter table public.orders
            add constraint orders_payment_status_check
            check (payment_status in ('unpaid', 'paid', 'failed'));
    end if;
end $$;

-- A reference can belong to only one order.
create unique index if not exists idx_orders_payment_reference
    on public.orders (payment_reference) where payment_reference is not null;

-- Defence in depth: the Flask backend uses the service-role key (bypasses RLS), but
-- if anyone ever inserts through a client key, they must not be able to create an
-- order that is already "paid" or beyond "pending".
drop policy if exists "Models can create own orders" on public.orders;
create policy "Models can create own orders" on public.orders
    for insert with check (
        model_id in (select id from public.models where user_id = auth.uid())
        and payment_status = 'unpaid'
        and status = 'pending'
    );
