-- =====================================================================
-- Switch from Paystack to Flutterwave — run ONCE in the Supabase SQL
-- editor (after 001_paystack_payments.sql). Safe to re-run. Nothing is
-- deleted; existing orders are untouched.
-- =====================================================================

-- The gateway transaction id column is now gateway-neutral.
do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'orders'
          and column_name = 'paystack_transaction_id'
    ) and not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'orders'
          and column_name = 'gateway_transaction_id'
    ) then
        alter table public.orders rename column paystack_transaction_id to gateway_transaction_id;
    end if;
end $$;

-- Covers a database that never had the old column.
alter table public.orders add column if not exists gateway_transaction_id bigint;
