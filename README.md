# PayBot — Telegram Stars platform for digital services

Modular-monolith implementation of the specification in
[`docs/telegram-platform-bot.md`](docs/telegram-platform-bot.md), built as a
standalone `paybot` service. One Telegram bot sells products from any number of
services; every service is namespaced by `service_code`, and access is tracked
as per-service entitlements instead of one global credits balance. The photo
service from the specification is wired in as the first service by the seed
script, and a second demo service proves extensibility without deploys.

## What is implemented

- Telegram webhook (`pre_checkout_query`, `successful_payment`, commands, callbacks)
  with `X-Telegram-Bot-Api-Secret-Token` verification and `update_id` idempotency.
- Catalog of services/products with Stars prices, kill switches, deep links and
  per-language names/descriptions.
- Orders with a frozen `price_stars`, 30-second click dedupe, 30-minute expiry and
  optional late-payment window.
- `sendInvoice` for the bot and `createInvoiceLink` in the Bot API client for
  web/Mini App checkout integrations.
- Password-less accounts: `accounts` + `account_identities` + `telegram_profiles`.
- Entitlements per `service_id`/`kind`, with ledger, consume idempotency,
  expiry, revocation on refund and admin adjustments.
- Internal service API: handoff (create/exchange), consume, balance, order
  status and `initData` verification with replay protection.
- Transactional outbox with retries, exponential backoff, dead-letter and
  signed service webhooks.
- Refunds through `refundStarPayment`, revoking only unused access.
- Admin API (RBAC: platform admin, service admin, support, finance), audit log,
  revenue reports (JSON/CSV) and a server-rendered `/admin` UI with CSRF.
- Background worker: outbox delivery, order/entitlement expiry, webhook health,
  initData cleanup.
- i18n: `ru`, `en`, `es`, `fa`, `uz`, `sah` with English fallback.
- Structured logs, metrics (`/metrics`), `/healthz`, `/readyz`.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# edit .env: SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET,
#            ADMIN_API_TOKEN, PUBLIC_BASE_URL

python -m scripts.seed        # creates the photo service, a demo service + tokens
uvicorn app.main:app --reload
```

Open `http://localhost:8000/admin` and sign in with `ADMIN_API_TOKEN`, or use the
API with `Authorization: Bearer $ADMIN_API_TOKEN`.

Local development does not need a public URL: use the admin UI/API, the seeded
services, and the tests. To receive Telegram updates, expose the app with
ngrok/cloudflared, set `PUBLIC_BASE_URL`, enable `TELEGRAM_AUTO_SETUP=1`, or run:

```bash
curl -sS "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=$PUBLIC_BASE_URL/api/webhooks/telegram" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

`TELEGRAM_DROP_PENDING_UPDATES=1` should only be used for the very first hookup.

## Production

```bash
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.worker        # separate process; keep API WORKERS_ENABLED=0
```

`docker compose up -d --build` runs PostgreSQL, the API and a single worker.
The app refuses to start in production when:

- `PUBLIC_BASE_URL` is not HTTPS,
- `SECRET_KEY` is the default/too short,
- a bot token is configured but `TELEGRAM_WEBHOOK_SECRET` is empty.

## Configuration

See [`.env.example`](.env.example). Key variables:

| Variable | Meaning |
|---|---|
| `PUBLIC_BASE_URL` | Public HTTPS base URL |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET` | Bot API + webhook auth |
| `TELEGRAM_AUTO_SETUP` | Register webhook and commands at startup |
| `ADMIN_API_TOKEN` | Bootstrap platform-admin token (hashed in DB) |
| `ADMIN_CHAT_ID` | Chat that receives operational alerts |
| `DATABASE_URL` | `postgresql+psycopg://...` in production |
| `ORDER_TTL_MINUTES` / `ORDER_DEDUPE_SECONDS` | Checkout timings |
| `ALLOW_LATE_PAYMENT` / `LATE_PAYMENT_MAX_MINUTES` | Late Stars payment policy |
| `HANDOFF_TTL_SECONDS` / `INIT_DATA_TTL_SECONDS` | Handoff/Mini App token TTL |
| `WORKERS_ENABLED` | Run the background worker inside this process |
| `OUTBOX_MAX_ATTEMPTS` | Retries before a service event is dead |
| `WEBHOOK_RATE_LIMIT_PER_MINUTE` | In-process webhook rate limit |

Service-specific data (URLs, tokens, prices) lives in the database, not in env.

## Flows

### Purchase

1. `/start` → catalog → service card → **Buy**.
2. The platform creates a `pending` order and calls `sendInvoice` with
   `provider_token=""`, `currency="XTR"`, `amount=price_stars`,
   `payload=order.id`.
3. `pre_checkout_query` is validated against the DB only (order, payer, service,
   product, currency, amount, blocked account) and answered from a threadpool.
4. `successful_payment` is the only proof of payment. It is deduplicated by
   `tg:<telegram_payment_charge_id>` and `update_id`, then atomically writes the
   order, payment event, entitlement, ledger row and `order.paid` outbox event.
5. The worker sends the localized confirmation with an **Open service** button.
   Access is granted even if the service's event endpoint is down.

### Open service / handoff

- Mini App: the bot sends a `web_app` button; the service validates `initData`
  (HMAC, `auth_date` TTL) or calls the platform verification endpoint.
- External web app: `POST /internal/v1/handoff` returns a signed one-time token
  with a 60-second TTL; the service exchanges it once at
  `POST /internal/v1/handoff/exchange`. Replays return `401`.

### Consume

`POST /internal/v1/entitlements/consume` with a service bearer token and
`Idempotency-Key`. The service is always derived from the token, so it cannot
consume another service's access. Errors: `402 insufficient_entitlement`,
`409 expired`, `409 service_mismatch`, `422 validation_error`.

### Refund

Admin refund calls `refundStarPayment`, marks the order `refunded`, revokes only
the unused remainder of the entitlement and emits `order.refunded`. If Telegram
fails, the order stays `paid` and access is untouched so the operator can retry.

## Admin API

All endpoints require `Authorization: Bearer <admin token>`.

```text
GET/POST/PATCH  /api/admin/services
GET/POST/PATCH  /api/admin/services/{id}/products
GET             /api/admin/orders
POST            /api/admin/orders/{id}/refund
POST            /api/admin/orders/{id}/resolve?action=mark_paid|refund
GET             /api/admin/payment-events
GET             /api/admin/entitlements
POST            /api/admin/entitlements/{id}/adjust
GET             /api/admin/users?query=...
GET             /api/admin/audit
GET             /api/admin/reports/revenue[?format=csv]
POST            /api/admin/services/{id}/tokens
DELETE          /api/admin/tokens/{id}
POST/DELETE     /api/admin/keys[/{id}]
```

Roles are enforced per token: `platform_admin`, `service_admin` (scoped to one
service), `support`, `finance`. Every mutation writes `audit_log`.

The `/admin` UI uses the same tokens for login, stores a signed HttpOnly cookie,
checks CSRF on every form and exposes services, products, orders/refunds, audit
and reports.

## Testing

```bash
pytest                 # unit + integration, fake Bot API, SQLite
ruff check .
```

The suite covers duplicate payments, amount mismatch, unknown orders, late
payments, consume idempotency, service isolation, expired access, handoff
replay, refund success/failure, RBAC, admin validation, outbox retry/dead-letter,
expiry jobs, i18n fallback, webhook secret/body limits, initData replay and the
admin UI.

## Operations

See [`docs/runbook.md`](docs/runbook.md) and
[`docs/service-integration.md`](docs/service-integration.md).

## Repository layout

```text
app/
  api/          webhook, internal API, admin API, admin UI, health
  bot/          update routing, commands, keyboards, invoice actions
  services/     accounts, catalog, orders, payments, entitlements, handoff,
                outbox, RBAC, reports, audit
  config.py     environment settings and production guards
  db.py         engine/session helpers
  models.py     SQLAlchemy schema
  i18n.py       ru/en/es/fa/uz/sah texts
  telegram.py   Bot API client
  worker.py     outbox + expiry + webhook health
alembic/        initial platform schema + initData replay table
docs/           spec, runbook, service integration guide
scripts/seed.py seeds two services and prints API tokens once
tests/          pytest suite
```
