# Runbook

Операционные процедуры для PayBot. Все команды предполагают доступ к окружению
и переменным из `.env`; секреты в тикеты и чаты не копируются.

## 1. Деплой

```bash
alembic upgrade head        # один раз, отдельным job/init-контейнером
# задеплоить приложение
curl -fsS "$PUBLIC_BASE_URL/healthz"
curl -fsS "$PUBLIC_BASE_URL/readyz"
curl -fsS "$PUBLIC_BASE_URL/metrics" | head
```

Порядок:

1. Бэкап БД.
2. `alembic upgrade head` один раз (в compose — сервис `migrate`).
3. Выкатить API (`WORKERS_ENABLED=0`, если воркер отдельный процесс).
4. Выкатить/перезапустить worker.
5. Проверить `/healthz`, `/readyz`.
6. Проверить `getWebhookInfo`.
7. Smoke: каталог → тестовая покупка → доступ → возврат.
8. Включить новый сервис/товар (`status=active` / `active=true`).

Не использовать `drop_pending_updates=true` при обычном деплое.

## 2. Вебхук Telegram

Проверка:

```bash
curl -sS "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo"
```

Смотреть `url`, `pending_update_count`, `last_error_message`,
`last_error_date`. Worker проверяет это каждые `WEBHOOK_CHECK_INTERVAL_SECONDS`
и шлёт алерт в `ADMIN_CHAT_ID` при появлении новой ошибки.

Если вебхук сломан:

1. Проверить DNS/TLS: `curl -v $PUBLIC_BASE_URL/healthz`.
2. Проверить, что секрет совпадает с `TELEGRAM_WEBHOOK_SECRET`.
3. Проверить логи: `webhook_rejected`, `updates_failed`.
4. Перерегистрировать:

```bash
curl -sS "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=$PUBLIC_BASE_URL/api/webhooks/telegram" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

5. Наблюдать `pending_update_count` (должен уменьшаться) и `webhook_latency_seconds`.

Симптомы:

| Симптом | Причина | Действие |
|---|---|---|
| `401` на все webhook | неверный `TELEGRAM_WEBHOOK_SECRET` | синхронизировать секрет, `setWebhook` |
| `413` | тело > `WEBHOOK_MAX_BODY_BYTES` | проверить прокси/атаку, не поднимать лимит без нужды |
| `429` | rate limit | проверить источник; Telegram повторит |
| `5xx` | БД/неожиданная ошибка | смотреть логи, БД; Telegram повторит update |
| Растёт `pending_update_count` | приложение не успевает/лежит | масштабировать, проверить БД и latency |

## 3. Оплата прошла, доступа нет

1. Найти заказ в админке (`telegram_user_id`, `order_id`, `payment_id`) или:

```bash
curl -sS "$PUBLIC_BASE_URL/api/admin/orders?payment_id=$CHARGE" \
  -H "Authorization: Bearer $ADMIN_API_TOKEN"
```

2. Если заказ `review` — посмотреть `review_reason`:
   - `amount_mismatch` / `currency_mismatch` — платёж не совпал; доступ не
     начислен. Вернуть Stars через `POST /api/admin/orders/{id}/refund` или
     решить вручную. `payment_id` сохранён для возврата.
   - `user_blocked` / `payer_mismatch` — ручная проверка.
   - `service_inactive` / `product_inactive` — доступ уже начислен, но заказ
     помечен `review`; включить сервис/товар, затем
     `POST /api/admin/orders/{id}/resolve?action=mark_paid`.
   - `broken_reference` — удалён товар/сервис, эскалация платформенному админу.
3. Если заказ `paid`, но entitlement отсутствует:
   - проверить `entitlement_ledger` и `payment_events` (`event_key=tg:<charge>`);
   - проверить, что `successful_payment` был обработан (не 5xx);
   - повторный update безопасен: `update_id` и charge идемпотентны;
   - при ручной выдаче — `POST /api/admin/entitlements/{id}/adjust`, только
     после сверки с Telegram, и с записью причины.

## 4. Возврат

1. Убедиться, что заказ `paid`/`review`, provider `telegram_stars`, есть
   `payment_id`.
2. Выполнить `POST /api/admin/orders/{id}/refund`.
3. Успех: `status=refunded`, неиспользованный остаток отозван (`revoked`),
   использованный не отзывается автоматически.
4. Ошибка Telegram (`502 telegram_refund_failed`): заказ остаётся `paid`,
   доступ не меняется — повторить позже. Проверить баланс бота в Telegram.
5. Дублирующие/спорные списания, не привязанные к заказу, возвращаются через
   `POST /api/admin/payment-events/{id}/refund` (в получателе используется
   фактический payer из платёжного события).
6. Частичных возвратов Stars через Bot API нет. Для компенсации использовать
   ручное начисление/списание entitlement и зафиксировать решение в аудите.

## 5. Outbox

Метрики: `outbox_pending`, `outbox_lag_seconds`, `outbox_sent`, `outbox_retry`,
`outbox_dead`.

- Рост `outbox_pending`/`lag` — сервис недоступен или worker не работает.
- `outbox_dead` + алерт `admin_outbox_dead` — событие не доставлено после
  `OUTBOX_MAX_ATTEMPTS`. Проверить `events_url`, TLS, подпись, ответы сервиса.
  После исправления — переотправить вручную (создать новое событие через
  повторную доставку/скрипт) и сверить состояние сервиса.
- Worker не запущен: `WORKERS_ENABLED=1` в worker-процессе, логи `worker started`.
- При горизонтальном масштабировании API держать `WORKERS_ENABLED=0`; воркер —
  один или несколько с `FOR UPDATE SKIP LOCKED` (PostgreSQL).

## 6. Сервис недоступен

- Платежи продолжают зачисляться: доступ хранится на платформе, событие
  остаётся в outbox и повторяется.
- Не отключать сервис ради «остановки событий» без понимания: статус `disabled`
  блокирует handoff/consume и pre-checkout новых оплат.
- При длительной недоступности — уведомить владельца сервиса; при необходимости
  `status=disabled` (kill switch), пользователям отвечает поддержка.

## 7. Ошибки списания

- `insufficient_entitlement` — ожидаемо при нулевом остатке.
- `expired` — истёк срок; продление только через новую покупку/адjust.
- `service_mismatch` — сервис пытается списать чужой `kind`; проверить токен и
  `service_code`.
- `401/403` — отозван токен/не хватает скоупа/сервис выключен. Ротация токена:
  выпустить новый, задеплоить в сервис, отозвать старый.

## 8. Безопасность

- Ротация `TELEGRAM_BOT_TOKEN`: BotFather → новый токен → env → рестарт →
  `setWebhook` (секрет можно не менять, но лучше сменить).
- Ротация `TELEGRAM_WEBHOOK_SECRET`: новый секрет в env → рестарт → `setWebhook`.
- Ротация `ADMIN_API_TOKEN`: новый env → рестарт (bootstrap-ключ обновится).
- Ротация `SECRET_KEY` инвалидирует подписанные handoff/admin-session токены —
  допустимо, они короткоживущие; предупредить админов.
- Утечка admin-ключа: `DELETE /api/admin/keys/{id}`; проверить `audit_log`.
- Утечка service-токена: `DELETE /api/admin/tokens/{id}`; сервис временно
  потеряет доступ к API.
- В логах не должно быть токенов, `initData`, полных charge id. Если нашли —
  очистить логи и сменить секрет.
- Удаление аккаунта: `POST /api/admin/users/{account_id}/anonymize` (только
  platform_admin) — очищает email/имя/username, блокирует аккаунт, сохраняет
  финансовые записи; действие пишется в `audit_log`.

## 9. БД и восстановление

- PostgreSQL; Alembic — единственный способ менять схему в production.
- Индексы: `orders.status`, `orders.telegram_user_id`, `payment_events.event_key`,
  `entitlements(account_id, service_id, kind)`, `outbox(status, next_attempt_at)`.
- При деградации: проверить медленные запросы, размер `outbox_events`,
  `init_data_uses`, `bot_updates`. `bot_updates` можно чистить старше 30 дней,
  `init_data_uses` чистит worker (24 часа).
- Восстановление: остановить worker и API → restore БД → `alembic upgrade head`
  → поднять worker → проверить `/readyz` и `getWebhookInfo` → smoke-покупка.
- После restore возможны повторные Telegram updates: они идемпотентны по
  `update_id` и charge id.

## 10. Метрики и алерты

Метрики Prometheus: `/metrics`.

Рекомендуемые алерты:

- `updates_failed` > 0 несколько минут;
- `webhook_rejected{reason="secret"}` > 0;
- `webhook_latency_seconds` p95 > 3 с;
- `payments_review` > 0 (amount mismatch/unknown order);
- `outbox_dead` > 0 или `outbox_lag_seconds` > 300;
- `telegram_pending_updates` растёт;
- `/readyz` != 200;
- рост 5xx.

## 11. Известные ограничения

- Один бот на платформу; отдельные боты для партнёров — вне scope.
- Нет подписок/рекуррентных платежей.
- Частичный возврат Stars невозможен через Bot API.
- `events_secret` хранится в БД в открытом виде (нужен для HMAC); доступ к БД
  должен быть ограничен. Ротация — через админку.
- In-process rate limit и метрики не разделяются между процессами; для строгих
  лимитов использовать reverse proxy/Redis.
