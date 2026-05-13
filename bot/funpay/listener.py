"""
FunPay event listener.

Runner.listen() is a synchronous infinite generator with internal time.sleep().
We drive it correctly by calling get_updates() + parse_updates() in a thread
every 6 seconds, then dispatching events on the async side.
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
from FunPayAPI.common.enums import EventTypes

from config import settings


class FunPayListener:
    def __init__(
        self,
        on_new_order: Callable[[str, int, str, str, dict], Awaitable[None]],
        on_buyer_message: Callable[[int, str, str], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._on_new_order = on_new_order
        self._on_buyer_message = on_buyer_message
        self._loop = loop
        self._account: FunPayAPI.Account | None = None
        self._runner: FunPayAPI.Runner | None = None
        self._task: asyncio.Task | None = None

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
        self._task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    # ── Public helpers ────────────────────────────────────────────────────────

    async def send_message(self, chat_id: int, text: str) -> None:
        if not self._account:
            return
        await asyncio.to_thread(
            self._account.send_message, chat_id, text, update_last_saved_message=True
        )

    async def get_full_order(self, order_id: str) -> FunPayAPI.types.Order | None:
        """Fetch full order data (including custom fields) from FunPay."""
        try:
            return await asyncio.to_thread(self._account.get_order, order_id)
        except Exception as exc:
            logger.error(f"Failed to fetch order {order_id}: {exc}")
            return None

    # ── Core loop ─────────────────────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """
        Correct async driver for the synchronous Runner:
        - call get_updates() in a thread (one HTTP request)
        - parse events synchronously (fast, no I/O)
        - dispatch async handlers
        - sleep 6 s
        """
        logger.info("FunPay listener started")
        while True:
            try:
                updates = await asyncio.to_thread(self._runner.get_updates)
                events  = self._runner.parse_updates(updates)
                for event in events:
                    await self._dispatch(event)
                await asyncio.sleep(6)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"FunPay listener error: {exc}")
                await asyncio.sleep(10)

    async def _dispatch(self, event) -> None:
        ev_type = event.type

        if ev_type == EventTypes.NEW_ORDER:
            order = event.order
            logger.info(f"New order #{order.id} from {order.buyer_username}")
            # Fetch full order in background to get custom fields
            asyncio.ensure_future(self._handle_new_order(order))

        elif ev_type == EventTypes.NEW_MESSAGE:
            msg = event.message
            if msg.author_id == self._account.id:
                return
            text = (msg.text or "").strip()
            if not text:
                return
            asyncio.ensure_future(
                self._on_buyer_message(msg.chat_id, msg.author or "", text)
            )

    async def _handle_new_order(self, shortcut: FunPayAPI.types.OrderShortcut) -> None:
        """
        Fetches the full Order object so we have access to custom fields
        (e.g. "TELEGRAM USERNAME"), then calls the on_new_order handler.
        """
        full_order = await self.get_full_order(shortcut.id)
        fields: dict = {}
        description = shortcut.description or ""

        if full_order:
            fields = full_order.fields or {}
            # Combine short + full descriptions for parsing
            parts = [
                shortcut.description or "",
                full_order.short_description or "",
                full_order.full_description or "",
            ]
            description = " | ".join(p for p in parts if p)
            logger.debug(f"Order #{shortcut.id} fields: {fields}")
            logger.debug(f"Order #{shortcut.id} description: {description}")

        await self._on_new_order(
            shortcut.id,
            shortcut.buyer_id,
            shortcut.buyer_username,
            description,
            fields,
        )
