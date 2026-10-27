# Flutterwave setup & go-live guide

## How payment works
1. Customer submits the checkout form → server creates an **unpaid** order (price read from the database, never from the browser) and a Flutterwave hosted payment → customer is redirected to Flutterwave's checkout page.
2. After paying, Flutterwave redirects to `/payment/callback?status=…&tx_ref=…&transaction_id=…`; Flutterwave also POSTs a `charge.completed` event to `/webhooks/flutterwave`.
3. Both paths call Flutterwave's **verify API** server-side. The order is marked `paid` (and `status` → `confirmed`) only if Flutterwave says `successful` AND the amount and currency equal what we charged. The two paths are idempotent, so whichever arrives first wins.
4. Admin cannot move an unpaid online-payment order to confirmed/processing/shipped/delivered.

## Environment variables
| Variable | Required | Notes |
|---|---|---|
| `FLUTTERWAVE_SECRET_KEY` | yes | Secret key from Flutterwave Dashboard → Settings → API Keys (Live: `FLWSECK-…`, Test: `FLWSECK_TEST-…`). Server-side only. |
| `FLUTTERWAVE_SECRET_HASH` | yes (for webhook) | Any long random string you invent; must be identical in Flutterwave Dashboard → Settings → Webhooks → Secret hash. |
| `APP_BASE_URL` | yes (prod) | e.g. `https://aprilskin.store`, no trailing slash. Used for the callback URL. |
| `FLUTTERWAVE_CURRENCY` | yes | Currency to charge (default `USD`), e.g. `NGN`. |
| `FLUTTERWAVE_USD_EXCHANGE_RATE` | only if currency ≠ USD | Units of that currency per 1 USD, e.g. `1500`. The app refuses to charge without it. |

The old `PAYSTACK_*` variables are no longer used and can be deleted. No Flutterwave public key is needed (hosted-redirect flow).

## Steps
1. Run `supabase/migrations/002_flutterwave_gateway.sql` once in the Supabase SQL editor (safe to re-run; deletes nothing). It renames `paystack_transaction_id` to `gateway_transaction_id`. (New databases built from `schema.sql` already have the new name.)
2. In Render → Environment, add the variables above, remove the `PAYSTACK_*` ones, then deploy.
3. Flutterwave Dashboard → Settings → Webhooks: set the **Webhook URL** to `https://<your-domain>/webhooks/flutterwave`, set the **Secret hash** to the same value as `FLUTTERWAVE_SECRET_HASH`, tick the option to receive webhooks / retry failed webhooks, and save.
4. Make sure the currency you charge in (e.g. NGN) is enabled on your Flutterwave account and the payment methods you want (card, bank transfer…) are turned on under Settings → Payment methods.

## Testing before going live
Use your **test** secret key and test mode in the dashboard.
- **Success / failure:** use the test cards listed in Flutterwave's testing docs (https://developer.flutterwave.com/docs/integration-guides/testing-helpers). Expect on success: redirect back → "Thank you!", order `payment: paid`, status `confirmed`, admin Orders shows a green `paid` badge with the reference. On failure or cancel: "Payment not completed" / "Awaiting payment" with a retry button; order stays unpaid and admin cannot ship it.
- **Webhook:** pay, then check Flutterwave Dashboard → Settings → Webhooks / transaction details for a `200` delivery. Resend it; the order must not change. To test the webhook alone, pay and close the tab before the redirect; the order should still become paid.
- **Amount tamper:** edit the price in browser dev tools before submitting; the charged amount must still equal the package price.
- **Go live:** switch `FLUTTERWAVE_SECRET_KEY` to the live key, make one small real payment, then refund it.
- Automated checks: `python -m unittest discover -s tests -v` (Flutterwave and Supabase are mocked; no keys needed).

## Operations notes
- If logs show `DUPLICATE PAYMENT` or `PAYMENT MISMATCH`, check that transaction in the Flutterwave dashboard and refund/resolve manually; the order is intentionally not auto-marked paid on a mismatch.
- The app returns 500 to webhooks only for transient errors so Flutterwave can retry.
- Old gift-card orders and any earlier Paystack-era orders are untouched and still display in admin.
