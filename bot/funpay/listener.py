"""
FunPay order & message listener.

Order detection uses account.get_sells(state="paid") polled every 30 s —
this is more reliable than the event-based Runner which can miss orders.

Message detection still uses the Runner's chat machinery (get_updates /
parse_updates) polled every 6 s.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Callable, Awaitable

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import FunPayAPI
from FunPayAPI.common.enums import EventTypes, OrderStatuses

from config import settings


class FunPayListener:
    def __init__(
        self,
        on_new_order: Callable[[str, int, str, str, dict], Awaitable[None]],
        on_buyer_message: Callable[[int, str, str], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
        fragment_ready: asyncio.Event | None = None,
    ) -> None:
        self._on_new_order = on_new_order
        self._on_buyer_message = on_buyer_message
        self._loop = loop
        self._fragment_ready = fragment_ready
        self._account: FunPayAPI.Account | None = None
        self._runner: FunPayAPI.Runner | None = None
        self._order_task: asyncio.Task | None = None
        self._msg_task: asyncio.Task | None = None
        # order IDs we've already dispatched in this session (avoids double-fire)
        self._seen_orders: set[str] = set()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _build_account(self) -> FunPayAPI.Account:
        acc = FunPayAPI.Account(
            golden_key=settings.FUNPAY_GOLDEN_KEY,
            user_agent=settings.FUNPAY_USER_AGENT,
        )
        acc.get()
        return acc

    async def start(self) -> None:
        self._account = await asyncio.to_thread(self._build_account)
        logger.info(f"FunPay account: {self._account.username} (id={self._account.id})")
        self._runner = FunPayAPI.Runner(self._account)

        # Seed seen-orders from DB so we don't replay on restart
        await self._seed_seen_orders()

        self._order_task = asyncio.create_task(self._order_poll_loop())
        self._msg_task = asyncio.create_task(self._message_poll_loop())

    async def stop(self) -> None:
        for t in (self._order_task, self._msg_task):
            if t:
                t.cancel()

    # ── Public helpers ────────────────────────────────────────────────────────

    async def send_message(self, chat_id: int, text: str) -> None:
        if not self._account:
            return
        await asyncio.to_thread(
            self._account.send_message, chat_id, text, update_last_saved_message=True
        )

    async def get_full_order(self, order_id: str) -> FunPayAPI.types.Order | None:
        try:
            return await asyncio.to_thread(self._account.get_order, order_id)
        except Exception as exc:
            logger.error(f"Failed to fetch order {order_id}: {exc}")
            return None

    # ── Order polling loop ────────────────────────────────────────────────────

    async def _seed_seen_orders(self) -> None:
        """Pre-populate _seen_orders from the database to avoid replaying on restart."""
        try:
            from database import AsyncSessionFactory, OrderRepository
            async with AsyncSessionFactory() as session:
                recent = await OrderRepository(session).list_recent(limit=200)
                for order in recent:
                    self._seen_orders.add(order.funpay_order_id)
            logger.debug(f"Seeded {len(self._seen_orders)} seen order IDs from DB")
        except Exception as exc:
            logger.warning(f"Could not seed seen orders: {exc}")

    async def _order_poll_loop(self) -> None:
        """
        Poll get_sells(state='paid') every 30 s.
        Waits for Fragment to be ready before first order dispatch.
        """
        logger.info("FunPay order poller started")

        # Wait until Fragment browser session is confirmed (ready or timed out)
        if self._fragment_ready is not None:
            logger.info("Waiting for Fragment session before processing orders...")
            await self._fragment_ready.wait()
            logger.info("Fragment ready — order processing started")

        while True:
            try:
                await self._check_new_orders()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Order poll error: {exc}")
                await asyncio.sleep(15)

    async def _check_new_orders(self) -> None:
        _, orders = await asyncio.to_thread(
            self._account.get_sells,
            include_paid=True,
            include_closed=False,
            include_refunded=False,
        )

        for shortcut in orders:
            if shortcut.status != OrderStatuses.PAID:
                continue
            if shortcut.id in self._seen_orders:
                continue

            self._seen_orders.add(shortcut.id)
            logger.info(f"New paid order #{shortcut.id} from {shortcut.buyer_username}")
            asyncio.ensure_future(self._handle_new_order(shortcut))

    async def _handle_new_order(self, shortcut: FunPayAPI.types.OrderShortcut) -> None:
        """Fetch full Order (has custom fields) then dispatch."""
        full_order = await self.get_full_order(shortcut.id)
        fields: dict = {}
        description = shortcut.description or ""

        if full_order:
            fields = full_order.fields or {}
            parts = [
                shortcut.description or "",
                full_order.short_description or "",
                full_order.full_description or "",
            ]
            description = " | ".join(p for p in parts if p)
            logger.debug(f"Order #{shortcut.id} fields: {fields}")
            logger.debug(f"Order #{shortcut.id} description: {description!r}")

        await self._on_new_order(
            shortcut.id,
            shortcut.buyer_id,
            shortcut.buyer_username,
            description,
            fields,
        )

    # ── Message polling loop ──────────────────────────────────────────────────

    async def _message_poll_loop(self) -> None:
        """Poll Runner for new chat messages every 6 s."""
        logger.info("FunPay message poller started")
        while True:
            try:
                updates = await asyncio.to_thread(self._runner.get_updates)
                events = self._runner.parse_updates(updates)
                for event in events:
                    if event.type == EventTypes.NEW_MESSAGE:
                        msg = event.message
                        if msg.author_id == self._account.id:
                            continue
                        text = (msg.text or "").strip()
                        if text:
                            asyncio.ensure_future(
                                self._on_buyer_message(msg.chat_id, msg.author or "", text)
                            )
                await asyncio.sleep(6)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"Message poll error: {exc}")
                await asyncio.sleep(10)
