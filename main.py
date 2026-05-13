"""
Entry point — starts Telegram bot, FunPay listener, and Fragment manager
in a single asyncio event loop.
"""

from __future__ import annotations

import asyncio
import signal

from aiogram import Bot
from loguru import logger

from utils import setup_logger
from database import init_db
from fragment import FragmentManager
from bot.funpay import FunPayListener, OrderProcessor
from bot.telegram import create_bot, create_dispatcher, notify_admins
from bot.telegram.handlers.admin_handler import set_fragment_manager


async def main() -> None:
    setup_logger()
    logger.info("Starting FunPay Star/Premium Bot")

    await init_db()
    logger.info("Database ready")

    bot: Bot = create_bot()
    dp = create_dispatcher()

    fragment = FragmentManager()
    await fragment.start()
    set_fragment_manager(fragment)   # make manager available to Telegram handlers

    # Notification helper for both FunPay chat and Telegram admin
    async def send_funpay_message(chat_id: int, text: str) -> None:
        await funpay_listener.send_message(chat_id, text)

    async def notify_admin(text: str) -> None:
        await notify_admins(bot, text)

    processor = OrderProcessor(
        fragment=fragment,
        send_funpay_message=send_funpay_message,
        notify_admin=notify_admin,
    )

    async def on_new_order(order_id: str, chat_id: int, buyer: str, desc: str, fields: dict) -> None:
        await processor.handle_new_order(order_id, chat_id, buyer, desc, fields)

    async def on_buyer_message(chat_id: int, author: str, text: str) -> None:
        await processor.handle_buyer_message(chat_id, text)

    loop = asyncio.get_event_loop()
    funpay_listener = FunPayListener(
        on_new_order=on_new_order,
        on_buyer_message=on_buyer_message,
        loop=loop,
    )
    await funpay_listener.start()

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _handle_signal(*_):
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows

    logger.info("Bot is running. Press Ctrl+C to stop.")
    await notify_admins(bot, "🚀 Бот запущен и готов к работе")

    # Run Telegram polling + wait for stop
    polling_task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False)
    )
    stop_task = asyncio.create_task(stop_event.wait())

    done, pending = await asyncio.wait(
        [polling_task, stop_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()

    # Cleanup
    await funpay_listener.stop()
    await fragment.stop()
    await bot.session.close()
    logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
