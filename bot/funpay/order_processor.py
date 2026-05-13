"""
Core pipeline: new FunPay order → parse fields → search Fragment → confirm → gift → report.
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from loguru import logger

from database import AsyncSessionFactory, OrderRepository, OrderStatus, OrderType
from fragment import FragmentManager, FragmentUser
from utils.blur import blur_username
from utils.ton_viewer import tx_url

# ── Field name variants FunPay uses ──────────────────────────────────────────
_TG_USERNAME_FIELDS = (
    "TELEGRAM USERNAME",
    "Telegram Username",
    "telegram username",
    "USERNAME",
    "Username",
    "Telegram",
    "ТЕЛЕГРАМ",
    "Телеграм",
    "Ник",
    "НИК",
)
_STARS_COUNT_FIELDS = (
    "КОЛИЧЕСТВО ЗВЁЗД",
    "Количество звёзд",
    "КОЛИЧЕСТВО ЗВЕЗД",
    "Количество звезд",
    "Stars",
    "STARS",
    "Звёзды",
    "Звезды",
)
_PREMIUM_FIELDS = (
    "СРОК ПОДПИСКИ",
    "Срок подписки",
    "Months",
    "MONTHS",
    "Месяцы",
    "Premium",
)

_STARS_IN_TEXT  = re.compile(r'stars?\s*[:\-]?\s*(\d+)', re.IGNORECASE)
_PREMIUM_IN_TEXT = re.compile(r'premium\s*[:\-]?\s*(\d+)', re.IGNORECASE)
_DIGIT_RE       = re.compile(r'(\d+)')
_USERNAME_RE    = re.compile(r'@?([A-Za-z0-9_]{5,32})')


def _extract_username(fields: dict, description: str) -> Optional[str]:
    """Try FunPay custom fields first, then fall back to parsing description text."""
    for key in _TG_USERNAME_FIELDS:
        val = fields.get(key, "").strip()
        if val:
            return val.lstrip("@")

    # Fallback: any @username pattern in description
    for word in description.split():
        if word.startswith("@") and _USERNAME_RE.match(word):
            return word.lstrip("@")
    return None


def _extract_stars(fields: dict, description: str) -> Optional[int]:
    for key in _STARS_COUNT_FIELDS:
        val = fields.get(key, "").strip()
        if val:
            m = _DIGIT_RE.search(val)
            if m:
                return int(m.group(1))

    m = _STARS_IN_TEXT.search(description)
    if m:
        return int(m.group(1))
    return None


def _extract_premium_months(fields: dict, description: str) -> Optional[int]:
    for key in _PREMIUM_FIELDS:
        val = fields.get(key, "").strip()
        if val:
            m = _DIGIT_RE.search(val)
            if m:
                return int(m.group(1))

    m = _PREMIUM_IN_TEXT.search(description)
    if m:
        return int(m.group(1))
    return None


class OrderProcessor:
    def __init__(
        self,
        fragment: FragmentManager,
        send_funpay_message,   # async callable(chat_id, text)
        notify_admin,          # async callable(text)
    ) -> None:
        self._fragment = fragment
        self._send = send_funpay_message
        self._notify = notify_admin
        # funpay_order_id → asyncio.Event
        self._pending_confirms: dict[str, asyncio.Event] = {}
        # funpay chat_id → order_id (for routing "Да" confirmations)
        self._chat_order_map: dict[int, str] = {}

    # ── Entry point ───────────────────────────────────────────────────────────

    async def handle_new_order(
        self,
        funpay_order_id: str,
        chat_id: int,
        buyer_username: str,
        description: str,
        fields: dict,
    ) -> None:
        logger.info(
            f"Processing order #{funpay_order_id} | buyer={buyer_username} | "
            f"fields={fields} | desc={description!r}"
        )

        # ── Dedup ─────────────────────────────────────────────────────────────
        async with AsyncSessionFactory() as session:
            if await OrderRepository(session).get_by_funpay_id(funpay_order_id):
                logger.debug(f"Order #{funpay_order_id} already in DB, skipping")
                return

        # ── Parse TG username ─────────────────────────────────────────────────
        tg_username = _extract_username(fields, description)
        if not tg_username:
            await self._send(
                chat_id,
                "❌ Не удалось определить Telegram-никнейм получателя.\n"
                "Напишите его вручную: <b>@username</b>",
            )
            return

        # ── Parse order type & amount ─────────────────────────────────────────
        stars  = _extract_stars(fields, description)
        months = _extract_premium_months(fields, description)

        if stars:
            order_type, amount = OrderType.STARS, stars
        elif months:
            order_type, amount = OrderType.PREMIUM, months
        else:
            await self._send(
                chat_id,
                "❌ Не удалось определить кол-во звёзд или срок Premium.\n"
                "Ожидается: <b>stars:50</b> или <b>premium:3</b>",
            )
            return

        # ── Save to DB ────────────────────────────────────────────────────────
        async with AsyncSessionFactory() as session:
            await OrderRepository(session).create(
                funpay_order_id=funpay_order_id,
                funpay_chat_id=chat_id,
                buyer_username=buyer_username,
                order_type=order_type,
                amount=amount,
                recipient_tg_username=tg_username,
                status=OrderStatus.PENDING_CONFIRM,
            )

        # ── Search Fragment ───────────────────────────────────────────────────
        fragment_user: Optional[FragmentUser] = await self._fragment.search_user(tg_username)
        if not fragment_user:
            await self._send(
                chat_id,
                f"❌ Пользователь <b>@{tg_username}</b> не найден на Fragment.\n"
                "Проверьте никнейм.",
            )
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id, OrderStatus.FAILED,
                    error_message="User not found on Fragment",
                )
            return

        # Store fragment username
        async with AsyncSessionFactory() as session:
            await OrderRepository(session).update_status(
                funpay_order_id, OrderStatus.PENDING_CONFIRM,
                fragment_found_username=fragment_user.username,
            )

        # ── Ask buyer to confirm ──────────────────────────────────────────────
        blurred   = blur_username(fragment_user.username)
        kind_label = (
            f"⭐ <b>{amount} звёзд</b>" if order_type == OrderType.STARS
            else f"💎 <b>Telegram Premium {amount} мес.</b>"
        )
        confirm_text = (
            f"📦 Заказ <b>#{funpay_order_id}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{kind_label}\n"
            f"Получатель: <b>nickname: {blurred}</b>\n\n"
            f"Всё верно? Напишите <b>Да</b> для подтверждения."
        )
        await self._send(chat_id, confirm_text)

        # Register in maps for confirm routing
        self._chat_order_map[chat_id] = funpay_order_id

        # Wait for "Да" (10 min timeout)
        event = asyncio.Event()
        self._pending_confirms[funpay_order_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=600)
        except asyncio.TimeoutError:
            self._pending_confirms.pop(funpay_order_id, None)
            self._chat_order_map.pop(chat_id, None)
            await self._send(chat_id, "⏰ Время подтверждения вышло. Обратитесь в поддержку.")
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id, OrderStatus.FAILED,
                    error_message="Confirmation timeout",
                )
            return

        self._pending_confirms.pop(funpay_order_id, None)
        self._chat_order_map.pop(chat_id, None)

        # ── Execute gift ──────────────────────────────────────────────────────
        await self._process_gift(
            funpay_order_id, chat_id, fragment_user.username,
            order_type, amount,
        )

    async def handle_buyer_message(self, chat_id: int, text: str) -> None:
        """Called when the buyer writes a message in the FunPay chat."""
        if text.strip().lower() in ("да", "да.", "yes", "+", "ок", "ok"):
            order_id = self._chat_order_map.get(chat_id)
            if order_id:
                await self.confirm_order(order_id)

    async def confirm_order(self, order_id: str) -> bool:
        event = self._pending_confirms.get(order_id)
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
        await self._send(chat_id, "⏳ Отправляем подарок, подождите...")
        async with AsyncSessionFactory() as session:
            await OrderRepository(session).update_status(funpay_order_id, OrderStatus.PROCESSING)

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
                f"{kind_label} отправлено\n"
                f"Получатель: <b>nickname: {blurred}</b>\n\n"
                f"🔗 Транзакция:\n{result.ton_viewer_url}"
            )
            await self._send(chat_id, success_text)
            await self._notify(
                f"✅ Заказ #{funpay_order_id} выполнен\n"
                f"Получатель: {blurred}\n"
                f"TX: {result.ton_viewer_url}"
            )
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id, OrderStatus.COMPLETED,
                    tx_hash=result.tx_hash,
                    ton_viewer_url=result.ton_viewer_url,
                )
        else:
            await self._send(
                chat_id,
                f"❌ Ошибка при отправке.\n{result.error}\n\nОбратитесь в поддержку.",
            )
            await self._notify(
                f"❌ Заказ #{funpay_order_id} ОШИБКА\n"
                f"Получатель: {blurred}\nОшибка: {result.error}"
            )
            async with AsyncSessionFactory() as session:
                await OrderRepository(session).update_status(
                    funpay_order_id, OrderStatus.FAILED,
                    error_message=result.error,
                )
