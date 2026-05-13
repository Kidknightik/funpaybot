from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import settings


class AdminOnlyMiddleware(BaseMiddleware):
    """Blocks all non-admin users from interacting with the bot."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id: int | None = None

        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id not in settings.ADMIN_IDS:
            if isinstance(event, Message):
                await event.answer(
                    "🚫 Доступ запрещён. Этот бот только для администраторов."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Нет доступа", show_alert=True)
            return

        return await handler(event, data)
