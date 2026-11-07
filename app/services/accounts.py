"""Account and Telegram identity management."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.i18n import normalize_lang
from app.models import Account, AccountIdentity, TelegramProfile, utcnow


def get_account(db: Session, account_id: str) -> Account | None:
    return db.get(Account, account_id)


def get_by_telegram_id(db: Session, telegram_user_id: int | str) -> Account | None:
    profile = db.execute(
        select(TelegramProfile).where(
            TelegramProfile.telegram_user_id == int(telegram_user_id)
        )
    ).scalar_one_or_none()
    if not profile:
        return None
    return db.get(Account, profile.account_id)


def get_or_create_telegram_account(
    db: Session, tg_user: dict, *, update: bool = True
) -> Account:
    """Return the platform account for a Telegram user, creating it if needed."""
    telegram_id = int(tg_user["id"])
    profile = db.execute(
        select(TelegramProfile).where(TelegramProfile.telegram_user_id == telegram_id)
    ).scalar_one_or_none()
    if profile:
        account = db.get(Account, profile.account_id)
        if account is not None:
            if update:
                profile.username = (tg_user.get("username") or None)
                profile.language_code = (tg_user.get("language_code") or None)
                profile.last_seen_at = utcnow()
            return account
        db.delete(profile)
        db.flush()

    locale = normalize_lang(tg_user.get("language_code"))
    try:
        with db.begin_nested():
            account = Account(
                locale=locale,
                name=(tg_user.get("first_name") or None),
            )
            db.add(account)
            db.flush()
            db.add(
                AccountIdentity(
                    account_id=account.id,
                    provider="telegram",
                    subject=str(telegram_id),
                )
            )
            db.add(
                TelegramProfile(
                    account_id=account.id,
                    telegram_user_id=telegram_id,
                    username=(tg_user.get("username") or None),
                    language_code=(tg_user.get("language_code") or None),
                    last_seen_at=utcnow(),
                )
            )
            db.flush()
    except IntegrityError:
        # A concurrent webhook created the same identity; use that account.
        profile = db.execute(
            select(TelegramProfile).where(TelegramProfile.telegram_user_id == telegram_id)
        ).scalar_one()
        account = db.get(Account, profile.account_id)
    return account


def set_locale(db: Session, account: Account, lang: str) -> None:
    account.locale = normalize_lang(lang)
    db.flush()


def link_identity(db: Session, account: Account, provider: str, subject: str) -> None:
    existing = db.execute(
        select(AccountIdentity).where(
            AccountIdentity.provider == provider, AccountIdentity.subject == subject
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(AccountIdentity(account_id=account.id, provider=provider, subject=subject))
        db.flush()
    elif existing.account_id != account.id:
        raise ValueError("identity is already linked to another account")
