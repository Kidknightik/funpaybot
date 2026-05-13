"""
Core pipeline: new FunPay order → search Fragment → confirm → gift → report.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from database import AsyncSessionFactory, OrderRepository, OrderStatus, OrderType
from fragment import FragmentManager, FragmentUser
from utils.blur import blur_username, format_recipient
from utils.ton_viewer import tx_url
from utils.validators import parse_tg_username, parse_stars_amount, parse_premium_months


class OrderProcessor:
    """
    Handles the full lifecycle of a single FunPay order in-process.

    The FunPay runner calls `handle_new_order` when a new paid order arrives.
    Confirmation ("Да") from the buyer is awaited via `confirm_order`.
    """

    def __init__(
        self,
        fragment: FragmentManager,
        send_funpay_message,   # async callable(chat_id, text)
        notify_admin,          # async callable(text)
    ) -> None:
        self._fragment = fragment
        self._send = send_funpay_message
        self._notify = notify_admin
        # Map funpay_order_id → asyncio.Event (set when buyer confirms)
        self._pending_confirms: dict[str, asyncio.Event] = {}

    # ── Entry point ───────────────────────────────────────────────────────────

    async def handle_new_order(
        self,
        funpay_order_id: str,
        chat_id: int,
        buyer_username: str,
        description: str,
    ) -> None:
        """Called from the FunPay runner for each new paid order."""
        logger.info(f"New order #{funpay_order_id} from {buyer_username}: {description!r}")

        # Parse order type and recipient from description
        tg_username = parse_tg_username(description)
        stars = parse_stars_amount(description)
        premium_months = parse_premium_months(description)

        if not tg_username:
            await self._send(
                chat_id,
                "❌ Не удалось определить Telegram-никнейм получателя.\n"
                "Напишите его в формате: @username",
            )
            return

        if stars:
            order_type = OrderType.STARS
            amount = stars
        elif premium_months:
            order_type = OrderType.PREMIUM
            amount = premium_months
        else:
            await self._send(
                chat_id,
                "❌ Не удалось определить количество звёзд или срок Premium.\n"
                "Пример: stars:50 или premium:3",
            )
            return

        # Save to DB
        async with AsyncSessionFactory() as session:
            repo = OrderRepository(session)
            existing = await repo.get_by_funpay_id(funpay_order_id)
            if existing:
                return
            await repo.create(
                funpay_order_id=funpay_order_id,
                funpay_chat_id=chat_id,
                buyer_username=buyer_username,
                order_type=order_type,
                amount=amount,
                recipient_tg_username=tg_username,
                status=OrderStatus.PENDING_CONFIRM,
            )

        # Search Fragment
        fragment_user: Optional[FragmentUser] = await self._fragment.search_user(tg_username)

        if not fragment_user:
            await self._send(
                chat_id,
                f"❌ Пользователь @{tg_username} не найден на Fragment.\n"
                "Проверьте никнейм и напишите снова.",
            )
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id,
                    OrderStatus.FAILED,
                    error_message="User not found on Fragment",
                )
            return

        # Store found username
        async with AsyncSessionFactory() as session:
            await OrderRepository(session).update_status(
                funpay_order_id,
                OrderStatus.PENDING_CONFIRM,
                fragment_found_username=fragment_user.username,
            )

        blurred = blur_username(fragment_user.username)
        kind_label = (
            f"⭐ {amount} звёзд" if order_type == OrderType.STARS
            else f"💎 Telegram Premium на {amount} мес."
        )
        confirm_text = (
            f"📦 Заказ #{funpay_order_id}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{kind_label}\n"
            f"Получатель: <b>nickname: {blurred}</b>\n\n"
            f"Всё верно? Напишите <b>Да</b> для подтверждения."
        )
        await self._send(chat_id, confirm_text)

        # Wait for buyer confirmation (up to 10 minutes)
        event = asyncio.Event()
        self._pending_confirms[funpay_order_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=600)
        except asyncio.TimeoutError:
            del self._pending_confirms[funpay_order_id]
            await self._send(
                chat_id,
                "⏰ Время подтверждения вышло. Обратитесь в поддержку.",
            )
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id,
                    OrderStatus.FAILED,
                    error_message="Confirmation timeout",
                )
            return

        del self._pending_confirms[funpay_order_id]

        # Process gift
        await self._process_gift(
            funpay_order_id, chat_id, fragment_user.username,
            order_type, amount,
        )

    async def confirm_order(self, funpay_order_id: str) -> bool:
        """Called when the buyer sends 'Да'. Returns True if order was waiting."""
        event = self._pending_confirms.get(funpay_order_id)
        if event:
            event.set()
            return True
        return False

    # ── Gift execution ────────────────────────────────────────────────────────

    async def _process_gift(
        self,
        funpay_order_id: str,
        chat_id: int,
        username: str,
        order_type: OrderType,
        amount: int,
    ) -> None:
        await self._send(chat_id, "⏳ Обрабатываем подарок, подождите...")

        async with AsyncSessionFactory() as session:
            await OrderRepository(session).update_status(
                funpay_order_id, OrderStatus.PROCESSING
            )

        if order_type == OrderType.STARS:
            result = await self._fragment.gift_stars(username, amount)
        else:
            result = await self._fragment.gift_premium(username, amount)

        blurred = blur_username(username)

        if result.success:
            kind_label = (
                f"⭐ {amount} звёзд" if order_type == OrderType.STARS
                else f"💎 Premium {amount} мес."
            )
            success_text = (
                f"✅ Готово!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{kind_label} отправлено получателю\n"
                f"Получатель: <b>nickname: {blurred}</b>\n\n"
                f"🔗 Транзакция:\n{result.ton_viewer_url}"
            )
            await self._send(chat_id, success_text)
            await self._notify(
                f"✅ Заказ #{funpay_order_id} выполнен\n"
                f"Получатель: {blurred}\n"
                f"Транзакция: {result.ton_viewer_url}"
            )
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id,
                    OrderStatus.COMPLETED,
                    tx_hash=result.tx_hash,
                    ton_viewer_url=result.ton_viewer_url,
                )
        else:
            error_text = (
                f"❌ Ошибка при отправке подарка.\n"
                f"Подробности: {result.error}\n\n"
                "Пожалуйста, обратитесь в поддержку."
            )
            await self._send(chat_id, error_text)
            await self._notify(
                f"❌ Заказ #{funpay_order_id} ОШИБКА\n"
                f"Получатель: {blurred}\n"
                f"Ошибка: {result.error}"
            )
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id,
                    OrderStatus.FAILED,
                    error_message=result.error,
                )
