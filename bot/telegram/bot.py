"""Telegram bot setup and notification helpers."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import settings
from bot.telegram.middlewares import AdminOnlyMiddleware
from bot.telegram.handlers import admin_router, orders_router


def create_bot() -> Bot:
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(AdminOnlyMiddleware())
    dp.callback_query.middleware(AdminOnlyMiddleware())
    dp.include_router(admin_router)
    dp.include_router(orders_router)
    return dp


async def notify_admins(bot: Bot, text: str) -> None:
    """Send a message to every admin."""
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass
