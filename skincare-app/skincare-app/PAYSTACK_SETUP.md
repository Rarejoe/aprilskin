# Paystack setup & go-live guide

## How payment works
1. Customer submits the checkout form → server creates an **unpaid** order (price read from the database, never from the browser) and a Paystack transaction → customer is redirected to Paystack's hosted checkout.
2. After paying, Paystack redirects to `/payment/callback`; Paystack also POSTs `charge.success` to `/webhooks/paystack`.
3. Both paths call Paystack's **verify API** server-side. The order is marked `paid` (and `status` → `confirmed`) only if Paystack says `success` AND the amount and currency equal what we charged. The two paths are idempotent, so whichever arrives first wins.
4. Admin cannot move an unpaid Paystack order to confirmed/processing/shipped/delivered.

## Environment variables
| Variable | Required | Notes |
|---|---|---|
| `PAYSTACK_SECRET_KEY` | yes | `sk_live_...` from Paystack Dashboard → Settings → API Keys & Webhooks. Server-side only. |
| `APP_BASE_URL` | yes (prod) | e.g. `https://yourshop.com`, no trailing slash. Used for the callback URL. |
| `PAYSTACK_CURRENCY` | yes | Currency your Paystack account charges in (default `USD`). |
| `PAYSTACK_USD_EXCHANGE_RATE` | only if currency ≠ USD | Units of that currency per 1 USD, e.g. `1500`. The app refuses to charge without it. |

No Paystack public key is needed (hosted-redirect flow). Existing Supabase/Flask variables are unchanged.

## Steps
1. Run `supabase/migrations/001_paystack_payments.sql` in the Supabase SQL editor (safe to re-run; deletes nothing).
2. In Render → Environment, add the variables above, then deploy.
3. Paystack Dashboard → Settings → API Keys & Webhooks:
   - **Live Webhook URL:** `https://<your-domain>/webhooks/paystack`
   - **Live Callback URL:** `https://<your-domain>/payment/callback` (a fallback; the app also sends it per transaction)
   - Save. Use the same for Test mode with your `sk_test_` key while testing.
4. Confirm the currency you intend to charge is enabled on your Paystack account (Settings → Preferences / contact Paystack for USD).

## Testing before going live
Use a **test** secret key (`sk_test_...`) and a public test webhook URL (your Render URL, or a tunnel such as ngrok locally; set `APP_BASE_URL` to match).
- **Success:** Paystack test card `4084 084 084 084084`, any future expiry, CVV `408`, OTP `123456` (see Paystack's test-card docs for current numbers). Expect: redirect back → "Thank you!", order `payment: paid`, status `confirmed`, admin Orders shows a green `paid` badge with the reference.
- **Failure:** use Paystack's declined test card, or close the Paystack page. Expect "Payment not completed" / "Awaiting payment" with a retry button; order stays `unpaid`/`failed`; admin cannot ship it.
- **Verification / webhook:** pay, then in Paystack Dashboard → Developers → Webhooks → Logs confirm a `200` for `charge.success`. Resend it; the order must not change. To test the webhook alone, pay and close the tab before the redirect; the order should still become paid.
- **Amount tamper:** edit the price field in browser dev tools before submitting; the charged amount must still equal the package price.
- **Go live:** switch `PAYSTACK_SECRET_KEY` to `sk_live_...`, confirm live webhook URL, make one small real payment, then refund it from the dashboard.
- Automated checks: `python -m unittest discover -s tests -v` (Paystack and Supabase are mocked; no keys needed).

## Operations notes
- If logs show `DUPLICATE PAYMENT` or `PAYMENT MISMATCH`, check that transaction in the Paystack dashboard and refund/resolve manually; the order is intentionally not auto-marked paid on a mismatch.
- Paystack retries failed webhooks (non-2xx); the app returns 500 only for transient errors.
- Old gift-card orders are untouched and still display in admin; the gift-card form fields were removed from checkout.
