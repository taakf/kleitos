# Axion Telegram Bot Integration


def is_telegram_configured() -> bool:
    """Return True if the Telegram bot is started AND at least one chat
    is authorised.  Imports are deferred so a missing bot module never
    breaks a no-Telegram build.

    Phase 13 — callers (e.g. the insight notifier) use this to decide
    whether to attempt delivery without blocking on a long Telegram
    timeout when the bot isn't even running.
    """
    try:
        from src.integrations.telegram.bot import _authorized_chats, _bot_app
    except Exception:
        return False
    if _bot_app is None:
        return False
    return bool(_authorized_chats)
