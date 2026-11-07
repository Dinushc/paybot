"""Localized texts for the bot and notifications.

English is the source language and the fallback for every missing key.
Service and product names may carry per-language translations in a JSON
column; when a translation is absent the base value is used.
"""
from __future__ import annotations

from typing import Any

SUPPORTED_LANGUAGES = ("ru", "en", "es", "fa", "uz", "sah")
DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {
    "ru": "Русский",
    "en": "English",
    "es": "Español",
    "fa": "فارسی",
    "uz": "O‘zbekcha",
    "sah": "Саха тыла",
}


def normalize_lang(value: str | None) -> str:
    """Map a Telegram language code to a supported locale."""
    if not value:
        return DEFAULT_LANGUAGE
    code = value.strip().lower().replace("_", "-").split("-", 1)[0]
    aliases = {"tt": "ru", "ba": "ru", "ky": "ru", "kk": "ru"}
    code = aliases.get(code, code)
    return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def localized(
    translations: dict | None, lang: str, field: str, default: str | None = None
) -> str:
    """Pick a translated service/product field with a safe fallback."""
    if default is None:
        default = ""
    if isinstance(translations, dict):
        entry = translations.get(lang)
        if isinstance(entry, dict):
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                return value
    return default


EN: dict[str, str] = {
    "welcome": "Choose a service to see products and prices in Telegram Stars.",
    "welcome_back": "Welcome back. Choose a service:",
    "services_title": "Available services:",
    "services_empty": "No services are available right now.",
    "service_card": "{name}\n\n{description}",
    "service_products": "Products:",
    "service_no_products": "No products are available right now.",
    "product_line": "• {name} — {price} ⭐",
    "service_links": "{terms}\n{support}",
    "service_terms": "Terms: {url}",
    "service_support": "Support: {contact}",
    "service_open_hint": "You already have access to this service.",
    "balance_title": "Your access by service:",
    "balance_line": "• {service}: {balance}",
    "balance_empty": "You have no active access yet.",
    "orders_title": "Your last orders:",
    "orders_empty": "You have no orders yet.",
    "order_line": "• {order_id} — {service} / {product} — {price} ⭐ — {status}",
    "order_status_header": "Order {order_id}: {status}",
    "order_not_found": "Order not found for your account.",
    "support_text": "Support: {contact}",
    "support_missing": "Support contact is not configured yet.",
    "paysupport_text": (
        "Payments in this bot are made with Telegram Stars.\n"
        "To check an order, send /paysupport <order_id>.\n"
        "Support: {contact}"
    ),
    "paysupport_order": "Order {order_id}: {status}.",
    "terms_text": "Terms of use: {url}",
    "privacy_text": "Privacy policy: {url}",
    "legal_missing": "This document is not published yet. Contact support: {contact}",
    "language_title": "Choose a language:",
    "language_saved": "Language set to {language}.",
    "unknown_command": "Unknown command. Use /services to open the catalog or /help for the command list.",
    "help_text": (
        "/services — catalog\n"
        "/balance — your access\n"
        "/orders — last orders\n"
        "/paysupport — payment support\n"
        "/support — contact support\n"
        "/language — change language\n"
        "/terms — terms of use\n"
        "/privacy — privacy policy"
    ),
    "product_unavailable": "This product is not available right now.",
    "service_unavailable": "This service is not available right now.",
    "open_failed": "Could not open the service. Try again later or contact support.",
    "invoice_failed": "Could not create the Telegram invoice. Try again later.",
    "blocked": "Your account is blocked. Contact support.",
    "generic_error": "Something went wrong. Please try again later.",
    "btn_services": "Services",
    "btn_orders": "My orders",
    "btn_support": "Support",
    "btn_language": "Language",
    "btn_open": "Open service",
    "btn_buy": "Buy",
    "btn_back": "Back",
    "btn_balance": "My access",
    "btn_help": "Help",
    "invoice_title": "{service}: {product}",
    "invoice_description": "{description}",
    "payment_success": (
        "Payment received ✅\n{service}: +{quantity} access.\n"
        "Tap “Open service” to continue."
    ),
    "payment_review": "We received the payment, but it needs a manual check. Support has been notified.",
    "payment_unknown": "We could not match this payment to an order. Support has been notified.",
    "payment_blocked": "Payment received, but your account is blocked. Contact support.",
    "refund_done": "Refund for order {order_id} completed.",
    "access_expired": "Your access to {service} has expired.",
    "status_pending": "pending",
    "status_paid": "paid",
    "status_review": "under review",
    "status_refunded": "refunded",
    "status_expired": "expired",
    "status_failed": "failed",
    "err_order_not_found": "Order not found.",
    "err_order_not_payable": "This order can no longer be paid.",
    "err_order_expired": "This invoice has expired. Create a new order.",
    "err_currency_mismatch": "Unsupported payment currency.",
    "err_amount_mismatch": "The payment amount does not match the order.",
    "err_service_inactive": "The service is temporarily unavailable.",
    "err_product_inactive": "The product is temporarily unavailable.",
    "err_user_blocked": "Your account is blocked.",
    "admin_amount_mismatch": (
        "Payment review: order {order_id}, expected {expected} XTR, got {actual} XTR, "
        "charge {charge}, reason {reason}."
    ),
    "admin_unknown_order": "Unknown order payment: payload {payload}, charge {charge}.",
    "admin_payment_review": "Order {order_id} moved to review: {reason}.",
    "admin_outbox_dead": (
        "Outbox event {event_id} ({event_type}) is dead after {attempts} attempts: {error}"
    ),
    "admin_webhook_error": "Telegram webhook error: {error}",
    "admin_service_unavailable": "Service {service} is unavailable for event delivery: {error}",
    "cmd_start": "Start the bot",
    "cmd_services": "Service catalog",
    "cmd_balance": "My access",
    "cmd_orders": "My orders",
    "cmd_support": "Support",
    "cmd_language": "Change language",
    "cmd_terms": "Terms of use",
    "cmd_privacy": "Privacy policy",
    "cmd_paysupport": "Payment support",
    "cmd_help": "Commands",
}

RU: dict[str, str] = {
    "welcome": "Выберите сервис, чтобы увидеть товары и цены в Telegram Stars.",
    "welcome_back": "С возвращением. Выберите сервис:",
    "services_title": "Доступные сервисы:",
    "services_empty": "Сейчас сервисы недоступны.",
    "service_card": "{name}\n\n{description}",
    "service_products": "Товары:",
    "service_no_products": "Сейчас товары недоступны.",
    "product_line": "• {name} — {price} ⭐",
    "service_links": "{terms}\n{support}",
    "service_terms": "Условия: {url}",
    "service_support": "Поддержка: {contact}",
    "service_open_hint": "У вас уже есть доступ к этому сервису.",
    "balance_title": "Ваш доступ по сервисам:",
    "balance_line": "• {service}: {balance}",
    "balance_empty": "У вас пока нет активного доступа.",
    "orders_title": "Последние заказы:",
    "orders_empty": "У вас пока нет заказов.",
    "order_line": "• {order_id} — {service} / {product} — {price} ⭐ — {status}",
    "order_status_header": "Заказ {order_id}: {status}",
    "order_not_found": "Заказ не найден для вашего аккаунта.",
    "support_text": "Поддержка: {contact}",
    "support_missing": "Контакт поддержки пока не настроен.",
    "paysupport_text": (
        "Оплата в боте происходит через Telegram Stars.\n"
        "Чтобы проверить заказ, отправьте /paysupport <order_id>.\n"
        "Поддержка: {contact}"
    ),
    "paysupport_order": "Заказ {order_id}: {status}.",
    "terms_text": "Условия использования: {url}",
    "privacy_text": "Политика конфиденциальности: {url}",
    "legal_missing": "Документ пока не опубликован. Поддержка: {contact}",
    "language_title": "Выберите язык:",
    "language_saved": "Язык изменён на {language}.",
    "unknown_command": "Неизвестная команда. Откройте каталог: /services или список команд: /help.",
    "help_text": (
        "/services — каталог\n"
        "/balance — ваш доступ\n"
        "/orders — последние заказы\n"
        "/paysupport — поддержка по оплате\n"
        "/support — связаться с поддержкой\n"
        "/language — сменить язык\n"
        "/terms — условия\n"
        "/privacy — конфиденциальность"
    ),
    "product_unavailable": "Этот товар сейчас недоступен.",
    "service_unavailable": "Этот сервис сейчас недоступен.",
    "open_failed": "Не удалось открыть сервис. Попробуйте позже или обратитесь в поддержку.",
    "invoice_failed": "Не удалось создать счёт Telegram. Попробуйте позже.",
    "blocked": "Ваш аккаунт заблокирован. Обратитесь в поддержку.",
    "generic_error": "Что-то пошло не так. Попробуйте позже.",
    "btn_services": "Сервисы",
    "btn_orders": "Мои заказы",
    "btn_support": "Поддержка",
    "btn_language": "Язык",
    "btn_open": "Открыть сервис",
    "btn_buy": "Купить",
    "btn_back": "Назад",
    "btn_balance": "Мой доступ",
    "btn_help": "Помощь",
    "invoice_title": "{service}: {product}",
    "invoice_description": "{description}",
    "payment_success": (
        "Оплата получена ✅\n{service}: начислено {quantity}.\n"
        "Нажмите «Открыть сервис», чтобы продолжить."
    ),
    "payment_review": "Оплата получена, но требует ручной проверки. Поддержка уведомлена.",
    "payment_unknown": "Не удалось сопоставить оплату с заказом. Поддержка уведомлена.",
    "payment_blocked": "Оплата получена, но аккаунт заблокирован. Обратитесь в поддержку.",
    "refund_done": "Возврат по заказу {order_id} выполнен.",
    "access_expired": "Срок доступа к сервису {service} истёк.",
    "status_pending": "ожидает оплаты",
    "status_paid": "оплачен",
    "status_review": "на проверке",
    "status_refunded": "возвращён",
    "status_expired": "истёк",
    "status_failed": "ошибка",
    "err_order_not_found": "Заказ не найден.",
    "err_order_not_payable": "Этот заказ больше нельзя оплатить.",
    "err_order_expired": "Счёт истёк. Создайте новый заказ.",
    "err_currency_mismatch": "Валюта оплаты не поддерживается.",
    "err_amount_mismatch": "Сумма оплаты не совпадает с заказом.",
    "err_service_inactive": "Сервис временно недоступен.",
    "err_product_inactive": "Товар временно недоступен.",
    "err_user_blocked": "Ваш аккаунт заблокирован.",
    "cmd_start": "Запустить бота",
    "cmd_services": "Каталог сервисов",
    "cmd_balance": "Мой доступ",
    "cmd_orders": "Мои заказы",
    "cmd_support": "Поддержка",
    "cmd_language": "Сменить язык",
    "cmd_terms": "Условия использования",
    "cmd_privacy": "Конфиденциальность",
    "cmd_paysupport": "Поддержка по оплате",
    "cmd_help": "Команды",
}

ES: dict[str, str] = {
    "welcome": "Elige un servicio para ver productos y precios en Telegram Stars.",
    "welcome_back": "Bienvenido de nuevo. Elige un servicio:",
    "services_title": "Servicios disponibles:",
    "services_empty": "No hay servicios disponibles ahora.",
    "service_card": "{name}\n\n{description}",
    "service_products": "Productos:",
    "service_no_products": "No hay productos disponibles ahora.",
    "product_line": "• {name} — {price} ⭐",
    "service_links": "{terms}\n{support}",
    "service_terms": "Términos: {url}",
    "service_support": "Soporte: {contact}",
    "service_open_hint": "Ya tienes acceso a este servicio.",
    "balance_title": "Tu acceso por servicio:",
    "balance_line": "• {service}: {balance}",
    "balance_empty": "Todavía no tienes acceso activo.",
    "orders_title": "Tus últimos pedidos:",
    "orders_empty": "Todavía no tienes pedidos.",
    "order_line": "• {order_id} — {service} / {product} — {price} ⭐ — {status}",
    "order_status_header": "Pedido {order_id}: {status}",
    "order_not_found": "Pedido no encontrado para tu cuenta.",
    "support_text": "Soporte: {contact}",
    "support_missing": "El contacto de soporte aún no está configurado.",
    "paysupport_text": (
        "Los pagos en este bot se realizan con Telegram Stars.\n"
        "Para consultar un pedido, envía /paysupport <order_id>.\n"
        "Soporte: {contact}"
    ),
    "paysupport_order": "Pedido {order_id}: {status}.",
    "terms_text": "Términos de uso: {url}",
    "privacy_text": "Política de privacidad: {url}",
    "legal_missing": "El documento aún no está publicado. Soporte: {contact}",
    "language_title": "Elige un idioma:",
    "language_saved": "Idioma cambiado a {language}.",
    "unknown_command": "Comando desconocido. Usa /services para el catálogo o /help para los comandos.",
    "help_text": (
        "/services — catálogo\n"
        "/balance — tu acceso\n"
        "/orders — últimos pedidos\n"
        "/paysupport — soporte de pago\n"
        "/support — contactar soporte\n"
        "/language — cambiar idioma\n"
        "/terms — términos\n"
        "/privacy — privacidad"
    ),
    "product_unavailable": "Este producto no está disponible ahora.",
    "service_unavailable": "Este servicio no está disponible ahora.",
    "open_failed": "No se pudo abrir el servicio. Inténtalo más tarde o contacta al soporte.",
    "invoice_failed": "No se pudo crear la factura de Telegram. Inténtalo más tarde.",
    "blocked": "Tu cuenta está bloqueada. Contacta al soporte.",
    "generic_error": "Algo salió mal. Inténtalo más tarde.",
    "btn_services": "Servicios",
    "btn_orders": "Mis pedidos",
    "btn_support": "Soporte",
    "btn_language": "Idioma",
    "btn_open": "Abrir servicio",
    "btn_buy": "Comprar",
    "btn_back": "Atrás",
    "btn_balance": "Mi acceso",
    "btn_help": "Ayuda",
    "invoice_title": "{service}: {product}",
    "invoice_description": "{description}",
    "payment_success": (
        "Pago recibido ✅\n{service}: +{quantity} de acceso.\n"
        "Pulsa «Abrir servicio» para continuar."
    ),
    "payment_review": "Recibimos el pago, pero necesita una revisión manual. Soporte ha sido notificado.",
    "payment_unknown": "No pudimos asociar este pago a un pedido. Soporte ha sido notificado.",
    "payment_blocked": "Pago recibido, pero tu cuenta está bloqueada. Contacta al soporte.",
    "refund_done": "Reembolso del pedido {order_id} completado.",
    "access_expired": "Tu acceso a {service} ha caducado.",
    "status_pending": "pendiente",
    "status_paid": "pagado",
    "status_review": "en revisión",
    "status_refunded": "reembolsado",
    "status_expired": "expirado",
    "status_failed": "fallido",
    "err_order_not_found": "Pedido no encontrado.",
    "err_order_not_payable": "Este pedido ya no se puede pagar.",
    "err_order_expired": "La factura expiró. Crea un nuevo pedido.",
    "err_currency_mismatch": "Moneda de pago no compatible.",
    "err_amount_mismatch": "El importe del pago no coincide con el pedido.",
    "err_service_inactive": "El servicio no está disponible temporalmente.",
    "err_product_inactive": "El producto no está disponible temporalmente.",
    "err_user_blocked": "Tu cuenta está bloqueada.",
    "cmd_start": "Iniciar el bot",
    "cmd_services": "Catálogo de servicios",
    "cmd_balance": "Mi acceso",
    "cmd_orders": "Mis pedidos",
    "cmd_support": "Soporte",
    "cmd_language": "Cambiar idioma",
    "cmd_terms": "Términos de uso",
    "cmd_privacy": "Privacidad",
    "cmd_paysupport": "Soporte de pago",
    "cmd_help": "Comandos",
}

FA: dict[str, str] = {
    "welcome": "برای دیدن محصولات و قیمت‌ها با Telegram Stars یک سرویس انتخاب کنید.",
    "welcome_back": "خوش آمدید. یک سرویس انتخاب کنید:",
    "services_title": "سرویس‌های موجود:",
    "services_empty": "در حال حاضر سرویسی موجود نیست.",
    "service_card": "{name}\n\n{description}",
    "service_products": "محصولات:",
    "service_no_products": "در حال حاضر محصولی موجود نیست.",
    "product_line": "• {name} — {price} ⭐",
    "service_links": "{terms}\n{support}",
    "service_terms": "شرایط: {url}",
    "service_support": "پشتیبانی: {contact}",
    "service_open_hint": "شما از قبل به این سرویس دسترسی دارید.",
    "balance_title": "دسترسی شما به تفکیک سرویس:",
    "balance_line": "• {service}: {balance}",
    "balance_empty": "هنوز دسترسی فعالی ندارید.",
    "orders_title": "آخرین سفارش‌های شما:",
    "orders_empty": "هنوز سفارشی ندارید.",
    "order_line": "• {order_id} — {service} / {product} — {price} ⭐ — {status}",
    "order_status_header": "سفارش {order_id}: {status}",
    "order_not_found": "سفارشی برای حساب شما پیدا نشد.",
    "support_text": "پشتیبانی: {contact}",
    "support_missing": "اطلاعات تماس پشتیبانی هنوز تنظیم نشده است.",
    "paysupport_text": (
        "پرداخت در این ربات با Telegram Stars انجام می‌شود.\n"
        "برای بررسی سفارش، /paysupport <order_id> را بفرستید.\n"
        "پشتیبانی: {contact}"
    ),
    "paysupport_order": "سفارش {order_id}: {status}.",
    "terms_text": "شرایط استفاده: {url}",
    "privacy_text": "سیاست حفظ حریم خصوصی: {url}",
    "legal_missing": "سند هنوز منتشر نشده است. پشتیبانی: {contact}",
    "language_title": "یک زبان انتخاب کنید:",
    "language_saved": "زبان به {language} تغییر کرد.",
    "unknown_command": "دستور ناشناخته. برای کاتالوگ /services و برای فهرست دستورها /help را بزنید.",
    "help_text": (
        "/services — کاتالوگ\n"
        "/balance — دسترسی شما\n"
        "/orders — آخرین سفارش‌ها\n"
        "/paysupport — پشتیبانی پرداخت\n"
        "/support — تماس با پشتیبانی\n"
        "/language — تغییر زبان\n"
        "/terms — شرایط\n"
        "/privacy — حریم خصوصی"
    ),
    "product_unavailable": "این محصول در حال حاضر موجود نیست.",
    "service_unavailable": "این سرویس در حال حاضر موجود نیست.",
    "open_failed": "باز کردن سرویس ممکن نشد. بعداً تلاش کنید یا با پشتیبانی تماس بگیرید.",
    "invoice_failed": "ساخت صورت‌حساب تلگرام ممکن نشد. بعداً تلاش کنید.",
    "blocked": "حساب شما مسدود شده است. با پشتیبانی تماس بگیرید.",
    "generic_error": "خطایی رخ داد. بعداً تلاش کنید.",
    "btn_services": "سرویس‌ها",
    "btn_orders": "سفارش‌های من",
    "btn_support": "پشتیبانی",
    "btn_language": "زبان",
    "btn_open": "باز کردن سرویس",
    "btn_buy": "خرید",
    "btn_back": "بازگشت",
    "btn_balance": "دسترسی من",
    "btn_help": "راهنما",
    "invoice_title": "{service}: {product}",
    "invoice_description": "{description}",
    "payment_success": (
        "پرداخت دریافت شد ✅\n{service}: {quantity} دسترسی اضافه شد.\n"
        "برای ادامه «باز کردن سرویس» را بزنید."
    ),
    "payment_review": "پرداخت دریافت شد اما نیاز به بررسی دستی دارد. پشتیبانی مطلع شد.",
    "payment_unknown": "نتوانستیم این پرداخت را به سفارشی متصل کنیم. پشتیبانی مطلع شد.",
    "payment_blocked": "پرداخت دریافت شد اما حساب شما مسدود است. با پشتیبانی تماس بگیرید.",
    "refund_done": "بازپرداخت سفارش {order_id} انجام شد.",
    "access_expired": "دسترسی شما به {service} منقضی شد.",
    "status_pending": "در انتظار پرداخت",
    "status_paid": "پرداخت‌شده",
    "status_review": "در حال بررسی",
    "status_refunded": "بازپرداخت‌شده",
    "status_expired": "منقضی",
    "status_failed": "ناموفق",
    "err_order_not_found": "سفارش پیدا نشد.",
    "err_order_not_payable": "این سفارش دیگر قابل پرداخت نیست.",
    "err_order_expired": "صورت‌حساب منقضی شده است. سفارش جدید بسازید.",
    "err_currency_mismatch": "ارز پرداخت پشتیبانی نمی‌شود.",
    "err_amount_mismatch": "مبلغ پرداخت با سفارش مطابقت ندارد.",
    "err_service_inactive": "سرویس موقتاً در دسترس نیست.",
    "err_product_inactive": "محصول موقتاً در دسترس نیست.",
    "err_user_blocked": "حساب شما مسدود است.",
    "cmd_start": "شروع ربات",
    "cmd_services": "کاتالوگ سرویس‌ها",
    "cmd_balance": "دسترسی من",
    "cmd_orders": "سفارش‌های من",
    "cmd_support": "پشتیبانی",
    "cmd_language": "تغییر زبان",
    "cmd_terms": "شرایط استفاده",
    "cmd_privacy": "حریم خصوصی",
    "cmd_paysupport": "پشتیبانی پرداخت",
    "cmd_help": "دستورها",
}

UZ: dict[str, str] = {
    "welcome": "Mahsulotlar va Telegram Stars narxlarini ko‘rish uchun servisni tanlang.",
    "welcome_back": "Xush kelibsiz. Servisni tanlang:",
    "services_title": "Mavjud servislar:",
    "services_empty": "Hozir servislar mavjud emas.",
    "service_card": "{name}\n\n{description}",
    "service_products": "Mahsulotlar:",
    "service_no_products": "Hozir mahsulotlar mavjud emas.",
    "product_line": "• {name} — {price} ⭐",
    "service_links": "{terms}\n{support}",
    "service_terms": "Shartlar: {url}",
    "service_support": "Qo‘llab-quvvatlash: {contact}",
    "service_open_hint": "Sizda bu servisga allaqachon kirish bor.",
    "balance_title": "Servislar bo‘yicha kirishingiz:",
    "balance_line": "• {service}: {balance}",
    "balance_empty": "Sizda hozircha faol kirish yo‘q.",
    "orders_title": "Oxirgi buyurtmalaringiz:",
    "orders_empty": "Sizda hozircha buyurtma yo‘q.",
    "order_line": "• {order_id} — {service} / {product} — {price} ⭐ — {status}",
    "order_status_header": "Buyurtma {order_id}: {status}",
    "order_not_found": "Hisobingiz uchun buyurtma topilmadi.",
    "support_text": "Qo‘llab-quvvatlash: {contact}",
    "support_missing": "Qo‘llab-quvvatlash kontakti hali sozlanmagan.",
    "paysupport_text": (
        "Bu botda to‘lov Telegram Stars orqali amalga oshiriladi.\n"
        "Buyurtmani tekshirish uchun /paysupport <order_id> yuboring.\n"
        "Qo‘llab-quvvatlash: {contact}"
    ),
    "paysupport_order": "Buyurtma {order_id}: {status}.",
    "terms_text": "Foydalanish shartlari: {url}",
    "privacy_text": "Maxfiylik siyosati: {url}",
    "legal_missing": "Hujjat hali e’lon qilinmagan. Qo‘llab-quvvatlash: {contact}",
    "language_title": "Tilni tanlang:",
    "language_saved": "Til {language} ga o‘zgartirildi.",
    "unknown_command": "Noma’lum buyruq. Katalog uchun /services yoki buyruqlar ro‘yxati uchun /help dan foydalaning.",
    "help_text": (
        "/services — katalog\n"
        "/balance — kirishingiz\n"
        "/orders — oxirgi buyurtmalar\n"
        "/paysupport — to‘lov yordami\n"
        "/support — qo‘llab-quvvatlash\n"
        "/language — tilni o‘zgartirish\n"
        "/terms — shartlar\n"
        "/privacy — maxfiylik"
    ),
    "product_unavailable": "Bu mahsulot hozir mavjud emas.",
    "service_unavailable": "Bu servis hozir mavjud emas.",
    "open_failed": "Servisni ochib bo‘lmadi. Keyinroq urinib ko‘ring yoki qo‘llab-quvvatlashga murojaat qiling.",
    "invoice_failed": "Telegram hisob-fakturasini yaratib bo‘lmadi. Keyinroq urinib ko‘ring.",
    "blocked": "Hisobingiz bloklangan. Qo‘llab-quvvatlashga murojaat qiling.",
    "generic_error": "Nimadir xato ketdi. Keyinroq urinib ko‘ring.",
    "btn_services": "Servislar",
    "btn_orders": "Buyurtmalarim",
    "btn_support": "Yordam",
    "btn_language": "Til",
    "btn_open": "Servisni ochish",
    "btn_buy": "Sotib olish",
    "btn_back": "Orqaga",
    "btn_balance": "Kirishim",
    "btn_help": "Yordam",
    "invoice_title": "{service}: {product}",
    "invoice_description": "{description}",
    "payment_success": (
        "To‘lov qabul qilindi ✅\n{service}: +{quantity} kirish.\n"
        "Davom etish uchun «Servisni ochish» tugmasini bosing."
    ),
    "payment_review": "To‘lov qabul qilindi, lekin qo‘lda tekshirish kerak. Qo‘llab-quvvatlash xabardor qilindi.",
    "payment_unknown": "Bu to‘lovni buyurtmaga bog‘lay olmadik. Qo‘llab-quvvatlash xabardor qilindi.",
    "payment_blocked": "To‘lov qabul qilindi, lekin hisobingiz bloklangan. Qo‘llab-quvvatlashga murojaat qiling.",
    "refund_done": "{order_id} buyurtmasi bo‘yicha qaytarish amalga oshirildi.",
    "access_expired": "{service} xizmatiga kirishingiz muddati tugadi.",
    "status_pending": "to‘lov kutilmoqda",
    "status_paid": "to‘langan",
    "status_review": "tekshiruvda",
    "status_refunded": "qaytarilgan",
    "status_expired": "muddati o‘tgan",
    "status_failed": "xato",
    "err_order_not_found": "Buyurtma topilmadi.",
    "err_order_not_payable": "Bu buyurtmani endi to‘lash mumkin emas.",
    "err_order_expired": "Hisob-faktura muddati o‘tgan. Yangi buyurtma yarating.",
    "err_currency_mismatch": "To‘lov valyutasi qo‘llab-quvvatlanmaydi.",
    "err_amount_mismatch": "To‘lov summasi buyurtmaga mos kelmaydi.",
    "err_service_inactive": "Servis vaqtincha mavjud emas.",
    "err_product_inactive": "Mahsulot vaqtincha mavjud emas.",
    "err_user_blocked": "Hisobingiz bloklangan.",
    "cmd_start": "Botni ishga tushirish",
    "cmd_services": "Servislar katalogi",
    "cmd_balance": "Kirishim",
    "cmd_orders": "Buyurtmalarim",
    "cmd_support": "Qo‘llab-quvvatlash",
    "cmd_language": "Tilni o‘zgartirish",
    "cmd_terms": "Foydalanish shartlari",
    "cmd_privacy": "Maxfiylik",
    "cmd_paysupport": "To‘lov yordami",
    "cmd_help": "Buyruqlar",
}

SAH: dict[str, str] = {
    "welcome": "Telegram Stars сыанатын уонна табаардары көрөргө сервис талыҥ.",
    "welcome_back": "Төннүбүтүҥүнэн! Сервис талыҥ:",
    "services_title": "Баар сервистэр:",
    "services_empty": "Билигин сервистэр суохтар.",
    "service_card": "{name}\n\n{description}",
    "service_products": "Табаардар:",
    "service_no_products": "Билигин табаардар суохтар.",
    "product_line": "• {name} — {price} ⭐",
    "service_links": "{terms}\n{support}",
    "service_terms": "Условиялар: {url}",
    "service_support": "Өйөбүл: {contact}",
    "service_open_hint": "Эн бу сервиска киириилээххин.",
    "balance_title": "Сервис эрээриҥинэн киирииҥ:",
    "balance_line": "• {service}: {balance}",
    "balance_empty": "Эн билигин киириитэ суоххун.",
    "orders_title": "Тиһэх заказтарыҥ:",
    "orders_empty": "Эн билигин заказтара суох.",
    "order_line": "• {order_id} — {service} / {product} — {price} ⭐ — {status}",
    "order_status_header": "Заказ {order_id}: {status}",
    "order_not_found": "Эн аккаунуҥ заказа булуллубата.",
    "support_text": "Өйөбүл: {contact}",
    "support_missing": "Өйөбүл контакка өссө олохтоммото.",
    "paysupport_text": (
        "Бу ботка төлөбүр Telegram Stars нөҥүө барар.\n"
        "Заказы бэрэбиэркэлээһин иһин /paysupport <order_id> ыыт.\n"
        "Өйөбүл: {contact}"
    ),
    "paysupport_order": "Заказ {order_id}: {status}.",
    "terms_text": "Туһаныы условиятa: {url}",
    "privacy_text": "Кистэл сиэрдэрэ: {url}",
    "legal_missing": "Докумуон өссө бэчээттэммэтэ. Өйөбүл: {contact}",
    "language_title": "Тылы талыҥ:",
    "language_saved": "Тыл {language} буолла.",
    "unknown_command": "Биллибэт команда. Каталог /services эбэтэр командалар /help.",
    "help_text": (
        "/services — каталог\n"
        "/balance — киирииҥ\n"
        "/orders — тиһэх заказтар\n"
        "/paysupport — төлөбүр өйөбүлэ\n"
        "/support — өйөбүлгэ кыттыс\n"
        "/language — тылы уларыт\n"
        "/terms — условиятa\n"
        "/privacy — кистэл"
    ),
    "product_unavailable": "Бу табаар билигин суох.",
    "service_unavailable": "Бу сервис билигин суох.",
    "open_failed": "Сервискы аспат. Кэлиҥҥигэ холон эбэтэр өйөбүлгэ кыттыс.",
    "invoice_failed": "Telegram счёт оҥоһуллубата. Кэлиҥҥигэ холон.",
    "blocked": "Эн аккаунуҥ бобуллубут. Өйөбүлгэ кыттыс.",
    "generic_error": "Туох эрэ сатаммата. Кэлиҥҥигэ холон.",
    "btn_services": "Сервистэр",
    "btn_orders": "Заказтарым",
    "btn_support": "Өйөбүл",
    "btn_language": "Тыл",
    "btn_open": "Сервискы ас",
    "btn_buy": "Атыылас",
    "btn_back": "Төннүү",
    "btn_balance": "Киириим",
    "btn_help": "Көмө",
    "invoice_title": "{service}: {product}",
    "invoice_description": "{description}",
    "payment_success": (
        "Төлөбүр кэллэ ✅\n{service}: +{quantity} киирии.\n"
        "Салгыы «Сервискы ас» диэни бас."
    ),
    "payment_review": "Төлөбүр кэллэ, ол гынан баран илиинэн бэрэбиэркэ наада. Өйөбүл сэрэтилиннэ.",
    "payment_unknown": "Бу төлөбүрү заказка сибээстии булунна. Өйөбүл сэрэтилиннэ.",
    "payment_blocked": "Төлөбүр кэллэ, ол гынан баран аккаун бобуллубут. Өйөбүлгэ кыттыс.",
    "refund_done": "{order_id} заказ төлөбүрэ төннөрүлүннэ.",
    "access_expired": "{service} сервиска киирииҥ болдьоҕо ааста.",
    "status_pending": "төлөбүрү күүтэ",
    "status_paid": "төлөммүт",
    "status_review": "бэрэбиэркэҕэ",
    "status_refunded": "төннөрүллүбүт",
    "status_expired": "болдьоҕо ааста",
    "status_failed": "сатаммата",
    "err_order_not_found": "Заказ булуллубата.",
    "err_order_not_payable": "Бу заказы төлүүр кыах суох.",
    "err_order_expired": "Счёт болдьоҕо ааста. Саҥа заказ оҥор.",
    "err_currency_mismatch": "Төлөбүр валютата өйөммөт.",
    "err_amount_mismatch": "Төлөбүр суумата заказка сөп түспэт.",
    "err_service_inactive": "Сервис билигин суох.",
    "err_product_inactive": "Табаар билигин суох.",
    "err_user_blocked": "Эн аккаунуҥ бобуллубут.",
    "cmd_start": "Боту саҕалаа",
    "cmd_services": "Сервис каталога",
    "cmd_balance": "Киириим",
    "cmd_orders": "Заказтарым",
    "cmd_support": "Өйөбүл",
    "cmd_language": "Тылы уларыт",
    "cmd_terms": "Туһаныы условиятa",
    "cmd_privacy": "Кистэл",
    "cmd_paysupport": "Төлөбүр өйөбүлэ",
    "cmd_help": "Командалар",
}

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": EN,
    "ru": RU,
    "es": ES,
    "fa": FA,
    "uz": UZ,
    "sah": SAH,
}


def t(lang: str | None, key: str, **kwargs: Any) -> str:
    """Translate ``key`` and interpolate placeholders, falling back to English."""
    language = normalize_lang(lang)
    catalog = TRANSLATIONS.get(language) or TRANSLATIONS[DEFAULT_LANGUAGE]
    template = catalog.get(key) or EN.get(key) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def status_label(lang: str | None, status: str) -> str:
    return t(lang, f"status_{status}") if f"status_{status}" in EN else status
