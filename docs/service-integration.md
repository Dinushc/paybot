# Подключение сервиса к платформе

Новый сервис добавляется через админку/Admin API без изменения кода бота.
Платформа знает только `service_code`, `kind`, `quantity` и URL-адреса сервиса.

## 1. Создание сервиса

```http
POST /api/admin/services
Authorization: Bearer <platform_admin_token>
Content-Type: application/json
```

```json
{
  "code": "photo",
  "name": "DVPhotoChecker",
  "description": "Проверка фотографий",
  "emoji": "📷",
  "status": "draft",
  "webapp_url": "https://example.com/app",
  "handoff_url": null,
  "events_url": "https://example.com/api/paybot/events",
  "events_secret": "shared-signing-secret",
  "terms_url": "https://example.com/legal/terms",
  "privacy_url": "https://example.com/legal/privacy",
  "support_email": "support@example.com",
  "platform_fee_bps": 1000,
  "sort_order": 10
}
```

`status=active` требует `terms_url`, `privacy_url` и `support_email` или
`support_url`. Все URL — только `https://`. `platform_fee_bps` — комиссия
платформы в базисных пунктах (1000 = 10 %).

Товары:

```http
POST /api/admin/services/{service_id}/products
```

```json
{
  "code": "pack5",
  "name": "5 проверок",
  "description": "Пакет из пяти проверок",
  "price_stars": 615,
  "entitlement_kind": "photo_check",
  "entitlement_quantity": 5,
  "entitlement_ttl_days": null,
  "active": true,
  "sort_order": 1
}
```

Цена фиксируется на заказе в момент создания; изменение товара не пересчитывает
старые заказы. `active=false` или `status=disabled` — kill switch без удаления
данных.

## 2. API-токен сервиса

```http
POST /api/admin/services/{service_id}/tokens
Authorization: Bearer <platform_admin_token>
```

```json
{"scopes": ["handoff", "entitlements:read", "entitlements:consume", "orders:read"]}
```

Ответ содержит `token` один раз. Токен хранится в БД только как SHA-256 хеш;
отзыв — `DELETE /api/admin/tokens/{id}`. Токен привязан к `service_id`, и все
операции автоматически ограничены этим сервисом.

Скоупы: `handoff`, `entitlements:read`, `entitlements:consume`, `orders:read`.

## 3. Переход пользователя

### Вариант A. Telegram Mini App

Бот отправляет кнопку `web_app` с `webapp_url`. Сервис проверяет
`Telegram.WebApp.initData`:

- HMAC-SHA256 по `bot_token`, ключ `WebAppData`;
- `auth_date` не старше `INIT_DATA_TTL_SECONDS` (по умолчанию 300 с);
- `user.id` соответствует аккаунту;
- повторное использование отклоняется.

Если сервис не должен знать токен бота, он вызывает:

```http
POST /internal/v1/initdata/verify
Authorization: Bearer <service_token>
Content-Type: application/json

{"init_data": "query_id=...&user=...&auth_date=...&hash=..."}
```

```json
{
  "valid": true,
  "auth_date": "1762500000",
  "user": {"id": 123456, "first_name": "..."},
  "account_id": "uuid",
  "locale": "ru"
}
```

Платформа хранит использованные `initData` до 24 часов и возвращает
`401 init_data_replayed` при повторе.

### Вариант B. Внешний веб-сервис (handoff)

Платформа создаёт одноразовый подписанный токен:

```http
POST /internal/v1/handoff
Authorization: Bearer <service_token>
Content-Type: application/json

{"account_id": "uuid", "service_code": "photo"}
```

```json
{
  "token": "eyJ...signature",
  "url": "https://example.com/app?handoff=...",
  "expires_in": 60
}
```

Сервис обменивает токен на сессию:

```http
POST /internal/v1/handoff/exchange
Authorization: Bearer <service_token>
Content-Type: application/json

{"token": "eyJ...signature"}
```

```json
{
  "account_id": "uuid",
  "service_code": "photo",
  "locale": "ru",
  "issued_at": 1762500000,
  "nonce": "...",
  "server_time": 1762500002
}
```

TTL 60 секунд, токен одноразовый. Повторный обмен — `401 handoff_replayed`.
Токен другого сервиса — `403 service_mismatch`.

## 4. Списание доступа

```http
POST /internal/v1/entitlements/consume
Authorization: Bearer <service_token>
Idempotency-Key: <uuid>
Content-Type: application/json

{
  "account_id": "uuid",
  "kind": "photo_check",
  "quantity": 1,
  "metadata": {"job_id": "job-42"}
}
```

```json
{
  "ok": true,
  "entitlement_id": "uuid",
  "remaining": 4,
  "balance": 4,
  "consumed": [{"entitlement_id": "uuid", "quantity": 1}]
}
```

- `Idempotency-Key` обязателен; повтор возвращает сохранённый результат с
  `"replayed": true` и не списывает второй раз. Тот же ключ с другим телом
  запроса — `409 idempotency_conflict`.
- `402 insufficient_entitlement` — доступ закончился.
- `409 expired` — доступ истёк.
- `409 service_mismatch` — у аккаунта есть доступ этого `kind` в другом сервисе;
  чужой доступ списать нельзя.
- `422 validation_error` — неверный запрос/нет ключа идемпотентности.

Остаток:

```http
GET /internal/v1/entitlements?account_id=uuid&kind=photo_check
Authorization: Bearer <service_token>
```

```json
{
  "balance": 4,
  "items": [
    {
      "entitlement_id": "uuid",
      "kind": "photo_check",
      "remaining": 4,
      "quantity": 5,
      "expires_at": null,
      "status": "active"
    }
  ]
}
```

Статус заказа:

```http
GET /internal/v1/orders/{order_id}
Authorization: Bearer <service_token>
```

Доступен только для заказов этого сервиса.

## 4.1. Счёт для оплаты на сайте/Mini App

Если заказ уже создан (например, из бота или веб-каталога), сервис может получить
ссылку на оплату Stars в Telegram:

```http
POST /internal/v1/orders/{order_id}/invoice-link
Authorization: Bearer <service_token>
```

```json
{"order_id": "uuid", "invoice_url": "https://t.me/$..."}
```

Ссылка создаётся через `createInvoiceLink` с `payload=order.id`,
`currency=XTR` и зафиксированной на заказе ценой. После оплаты вебхук платформы
начислит доступ. Для этого нужен скоуп `orders:read`; заказ должен принадлежать
сервису и быть в статусе `pending`/`expired`.

## 5. События (outbox)

После оплаты/возврата платформа пишет событие в outbox и доставляет его на
`events_url` с ретраями и экспоненциальной задержкой. Если `events_url` не
задан, событие помечается `failed`.

Заголовки:

```text
Content-Type: application/json
X-Platform-Event: order.paid
X-Platform-Event-Id: <event uuid>
X-Platform-Signature: sha256=<hex hmac>
```

Подпись: HMAC-SHA256 от **точного тела запроса** ключом `events_secret`.
Проверяйте её в constant-time. Тело:

```json
{
  "id": "event uuid",
  "type": "order.paid",
  "aggregate_id": "order uuid",
  "occurred_at": "2026-11-07T08:00:00+00:00",
  "data": {
    "order_id": "order uuid",
    "account_id": "account uuid",
    "service_id": "service uuid",
    "service_code": "photo",
    "product_id": "product uuid",
    "product_code": "pack5",
    "kind": "photo_check",
    "quantity": 5,
    "price_stars": 615,
    "status": "paid",
    "review_reason": null
  }
}
```

`2xx` — доставлено; `5xx`/`408`/`429`/сеть — повтор; прочие `4xx` — `failed`.
После `OUTBOX_MAX_ATTEMPTS` — `dead` и алерт администратору. Событие не является
источником истины для баланса: баланс всегда читается через API.

Типы событий: `order.paid`, `order.refunded`.

## 6. Обязательный чек-лист сервиса

- [ ] Принимает handoff-токен или проверяет `initData`.
- [ ] Обменивает handoff один раз и создаёт свою сессию.
- [ ] Списывает доступ через `consume` с уникальным `Idempotency-Key` на
      бизнес-операцию.
- [ ] Читает баланс через `entitlements`.
- [ ] Проверяет подпись событий на `events_url`.
- [ ] Имеет health-check.
- [ ] Терпит недоступность платформы: не выполняет платную операцию без
      успешного `consume`, но не теряет задачу пользователя.
- [ ] Тестовая покупка, открытие, списание и возврат проверены в тестовой среде.

## 7. Пример манифеста

```yaml
code: photo
name: DVPhotoChecker
status: draft
webapp_url: https://example.com/app
terms_url: https://example.com/legal/terms
privacy_url: https://example.com/legal/privacy
support_email: support@example.com
platform_fee_bps: 1000
products:
  - code: solo
    name: 1 проверка
    price_stars: 231
    entitlement_kind: photo_check
    entitlement_quantity: 1
  - code: pack5
    name: 5 проверок
    price_stars: 615
    entitlement_kind: photo_check
    entitlement_quantity: 5
```
