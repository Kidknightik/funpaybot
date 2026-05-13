"""
FunPay event listener — wraps FunPayAPI Runner and routes events
to OrderProcessor.
"""

from __future__ import annotations

import asyncio
import sys
import os
from pathlib import Path
from typing import Callable, Awaitable

from loguru import logger

# FunPayAPI lives at repo root, add it to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import FunPayAPI

from config import settings

# Mapping funpay chat_id → list of pending order ids (for confirm routing)
_chat_to_orders: dict[int, list[str]] = {}


class FunPayListener:
    """
    Runs FunPayAPI's Runner in a background thread (it's synchronous)
    and bridges events to async handlers.
    """

    def __init__(
        self,
        on_new_order: Callable[[str, int, str, str], Awaitable[None]],
        on_buyer_message: Callable[[int, str, str], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._on_new_order = on_new_order
        self._on_buyer_message = on_buyer_message
        self._loop = loop
        self._account: FunPayAPI.Account | None = None
        self._runner: FunPayAPI.Runner | None = None
        self._task: asyncio.Task | None = None

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

    async def send_message(self, chat_id: int, text: str) -> None:
        """Send a FunPay chat message from the async side."""
        if not self._account:
            return
        await asyncio.to_thread(
            self._account.send_message, chat_id, text, update_last_saved_message=True
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """Iterate runner events, dispatch to handlers."""
        assert self._runner
        logger.info("FunPay listener started")
        while True:
            try:
                events = await asyncio.to_thread(self._runner_poll)
                for event in events:
                    await self._dispatch(event)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"FunPay listener error: {exc}")
                await asyncio.sleep(5)

    def _runner_poll(self) -> list:
        """Synchronous call that returns a batch of events."""
        events = []
        for event in self._runner.listen():
            events.append(event)
            if len(events) >= 50:
                break
        return events

    async def _dispatch(self, event) -> None:
        ev_type = event.type

        if ev_type == FunPayAPI.events.EventTypes.NEW_ORDER:
            order: FunPayAPI.types.OrderShortcut = event.order
            chat_id = order.buyer_id
            description = f"{order.description} {order.short_description or ''}"
            _chat_to_orders.setdefault(chat_id, []).append(order.id)
            asyncio.ensure_future(
                self._on_new_order(order.id, chat_id, order.buyer_username, description)
            )

        elif ev_type == FunPayAPI.events.EventTypes.NEW_MESSAGE:
            msg: FunPayAPI.types.Message = event.message
            if msg.author_id == self._account.id:
                return
            text = (msg.text or "").strip()
            if not text:
                return
            asyncio.ensure_future(
                self._on_buyer_message(msg.chat_id, msg.author, text)
            )
