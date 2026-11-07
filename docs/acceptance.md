# Приёмка: соответствие ТЗ

| Критерий | Реализация | Проверка |
|---|---|---|
| Новый сервис через админку без деплоя | `POST /api/admin/services`, products, tokens; `/admin` UI | `tests/test_admin_api.py`, `tests/test_ops.py` |
| Каталог сервисов, товаров и цен в Stars | `app/bot/gateway.py`, `app/services/catalog.py` | `test_catalog_buy_and_pay_once`, `test_start_deep_links` |
| Оплата начисляет entitlement ровно один раз | `app/services/payments.py`, `app/services/entitlements.py` | `test_catalog_buy_and_pay_once` |
| Повторный `successful_payment` не удваивает доступ | `bot_updates.update_id`, `payment_events.event_key`, `orders.payment_id` | `test_catalog_buy_and_pay_once` |
| `pre_checkout_query` ≤10 с, только БД | `validate_pre_checkout`, вызов из threadpool | `test_payments_flow.py` |
| Неверная сумма/валюта → `review`, без начисления | `handle_successful_payment` + `mismatch()` | `test_amount_mismatch_goes_to_review_without_grant` |
| Возврат отзывает неиспользованный доступ | `refund_order`, `revoke_unused_for_order` | `tests/test_refunds.py` |
| Использованный доступ не отзывается автоматически | `remaining <= 0` пропускается | `test_refund_does_not_claw_back_used_access` |
| Сервис не может списать чужой доступ | `service_id` из токена + `service_mismatch` | `test_consume_cannot_touch_another_service` |
| Handoff одноразовый и истекает | `handoff_tokens` + HMAC + TTL | `test_handoff_exchange_is_one_time`, `test_handoff_expiry_and_service_scope` |
| Все пользовательские тексты локализованы | `app/i18n.py`, 6 языков, fallback en | `test_normalize_and_fallback`, bot-тесты |
| Production без webhook secret не стартует | `Settings.fatal_problems` | `test_production_config_guard` |
| `getWebhookInfo` контролируется | worker `_check_webhook` + алерт | `docs/runbook.md` |
| Логи без токенов/секретов | маскирование, Telegram-ошибки без URL | code review + `mask` тест |
| Runbook | `docs/runbook.md` | — |
| Минимум два сервиса | `scripts/seed.py`: `photo`, `demo` | запуск seed |
| Админка, аудит, отчёты | `/admin`, `audit_log`, `reports.revenue` | `tests/test_ops.py`, `tests/test_admin_api.py` |
| Метрики/алерты/health | `/metrics`, `/healthz`, `/readyz`, admin alerts | `test_health_and_metrics`, worker tests |
| Идемпотентность consume | `Idempotency-Key` + ledger | `test_consume_is_idempotent_and_errors` |
| Outbox с ретраями/dead-letter | `app/services/outbox.py`, worker | `test_outbox_notification_and_retry`, `test_outbox_goes_dead_and_alerts` |
| initData replay protection | `init_data_uses`, TTL | `test_init_data_verification` |

## Осознанно вне первой версии

- Подписки/рекуррентные платежи.
- Фиатный эквайринг и криптоплатежи (кроме Stars).
- Вывод Stars в TON/фиат.
- Отдельные боты на каждый сервис.
- Распределённый rate limit/метрики (процесс-локальные; для строгих лимитов
  ставится reverse proxy/Redis).
